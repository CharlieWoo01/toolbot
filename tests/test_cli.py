from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    GitHubRelease,
    GitSetup,
    Router,
    commit_all,
    git,
    github_releases,
    make_context,
    widget_files,
    widget_yaml,
    write_tool,
)

from toolbot import cli
from toolbot.config.control import ControlConfig
from toolbot.services.artifacts import apply_version

GITLAB = "https://gitlab.example.com"

CONTROL_YAML = """\
hosts:
  internal:
    type: gitlab
    url: https://gitlab.example.com
    token-env: INTERNAL_GITLAB_TOKEN
rules:
  - tools: ["widget"]
    ignore: [">=2.0.0"]
targets:
  nightly:
    - host: internal
      group: platform
      exclude: ["platform/legacy-*"]
  weekly:
    - host: internal
      repository: team/ci-tools
"""


@pytest.fixture
def repo(git_setup: GitSetup, router: Router) -> GitSetup:
    write_tool(git_setup.work, "widget", widget_yaml("1.0.0"))
    github_releases(router, [GitHubRelease("v1.0.0", widget_files("1.0.0"))])
    apply_version(make_context(router), git_setup.work, "widget")
    commit_all(git_setup.work)
    github_releases(
        router, [GitHubRelease("v2.0.0", widget_files("2.0.0")), GitHubRelease("v1.0.0", widget_files("1.0.0"))]
    )
    return git_setup


LAST_LOG: list[str] = []


def run(
    monkeypatch: pytest.MonkeyPatch, router: Router, arguments: list[str], env: dict[str, str] | None = None
) -> int:
    """Run the CLI with mocked HTTP; the context's log lines end up in LAST_LOG."""

    def fake_create_context(**keyword_arguments: Any) -> Any:
        control: ControlConfig | None = keyword_arguments.get("control")
        context = make_context(router, env or {}, control=control)
        LAST_LOG[:] = []
        context.log = LAST_LOG.append
        return context

    monkeypatch.setattr(cli, "create_context", fake_create_context)
    return cli.main(arguments)


def repository_state(setup: GitSetup) -> tuple[str, str, str, dict[str, bytes]]:
    files = {
        str(path.relative_to(setup.work)): path.read_bytes()
        for path in sorted((setup.work / "tools").rglob("*"))
        if path.is_file()
    }
    return (
        git(setup.work, "status", "--porcelain", "--ignored"),
        git(setup.work, "for-each-ref"),
        git(setup.remote, "for-each-ref"),
        files,
    )


def test_local_dry_run_changes_nothing(
    repo: GitSetup, router: Router, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = repository_state(repo)
    requests_before = len(router.requests)
    exit_code = run(monkeypatch, router, ["--root", str(repo.work), "check", "--dry-run"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Current: 1.0.0" in output and "Latest:  2.0.0" in output and "Update available." in output
    assert "✓ widget-2.0.0-windows-amd64.zip" in output
    assert "Would create: toolbot/widget/2.0.0" in output
    assert "existing change requests not checked" in output
    assert repository_state(repo) == before  # no files, branches, refs, commits
    during = router.requests[requests_before:]
    assert [request for request in during if request.method not in ("GET", "HEAD")] == []
    assert not [request for request in during if request.url.host == "release-assets.githubusercontent.com"]


def test_dry_run_fails_like_the_real_run_when_provider_digest_is_missing(
    tmp_path: Path, router: Router, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    yaml_text = widget_yaml("1.0.0").replace('type: file\n  file: "{asset}.sha256"', "type: provider")
    assert "type: provider" in yaml_text
    write_tool(tmp_path, "widget", yaml_text)
    github_releases(router, [GitHubRelease("v2.0.0", widget_files("2.0.0"), digests=False)])
    assert run(monkeypatch, router, ["--root", str(tmp_path), "check", "--dry-run"]) == 1
    assert "reported no digest" in capsys.readouterr().out


def test_outdated_table_and_json(
    repo: GitSetup, router: Router, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(monkeypatch, router, ["--root", str(repo.work), "outdated"]) == 0
    table = capsys.readouterr().out.splitlines()
    assert table[0].split() == ["TOOL", "CURRENT", "LATEST", "STATUS"]
    assert table[1].split()[:3] == ["widget", "1.0.0", "2.0.0"] and "update available" in table[1]
    run(monkeypatch, router, ["--root", str(repo.work), "outdated", "--json"])
    assert json.loads(capsys.readouterr().out) == [
        {"tool": "widget", "current": "1.0.0", "latest": "2.0.0", "status": "update available"}
    ]


def test_central_rules_from_toolbot_yaml_apply(
    repo: GitSetup, router: Router, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo.work / "toolbot.yaml").write_text(CONTROL_YAML)
    assert run(monkeypatch, router, ["--root", str(repo.work), "outdated", "--json"]) == 0
    [row] = json.loads(capsys.readouterr().out)
    assert row["status"] == "up to date (held back: 2.0.0)"


def test_outdated_reports_failures_with_nonzero_exit(
    tmp_path: Path, router: Router, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_tool(tmp_path, "widget", widget_yaml("1.0.0"))
    assert run(monkeypatch, router, ["--root", str(tmp_path), "outdated"]) == 1
    assert "error:" in capsys.readouterr().out


def test_list_validate_verify(
    repo: GitSetup, router: Router, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(monkeypatch, router, ["--root", str(repo.work), "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["name"] == "widget"
    assert run(monkeypatch, router, ["--root", str(repo.work), "validate"]) == 0
    assert run(monkeypatch, router, ["--root", str(repo.work), "verify"]) == 0
    (repo.work / "tools/widget/widget.zip").write_text("tampered")
    assert run(monkeypatch, router, ["--root", str(repo.work), "verify"]) == 1


def test_validate_reports_control_config(
    repo: GitSetup, router: Router, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo.work / "toolbot.yaml").write_text(CONTROL_YAML)
    assert run(monkeypatch, router, ["--root", str(repo.work), "validate"]) == 0
    assert "toolbot.yaml: valid (1 host(s), 1 rule(s), 2 target set(s)" in capsys.readouterr().out
    (repo.work / "toolbot.yaml").write_text("targets:\n  nightly:\n    - host: nowhere\n      repository: a/b\n")
    assert run(monkeypatch, router, ["--root", str(repo.work), "validate"]) == 1
    assert 'unknown host "nowhere"' in capsys.readouterr().err


def test_targets_command_resolves_groups(
    tmp_path: Path, router: Router, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "toolbot.yaml"
    config.write_text(CONTROL_YAML)
    router.json(
        f"{GITLAB}/api/v4/groups/platform/projects",
        [
            {"path_with_namespace": "platform/ci-tools", "default_branch": "main"},
            {"path_with_namespace": "platform/no-tools", "default_branch": "main"},
            {"path_with_namespace": "platform/legacy-build", "default_branch": "main"},
            {"path_with_namespace": "platform/empty", "default_branch": None},
        ],
    )
    router.json(f"{GITLAB}/api/v4/projects/platform%2Fci-tools/repository/tree", [{"name": "pact"}])
    router.json(f"{GITLAB}/api/v4/projects/platform%2Fno-tools/repository/tree", [])
    environment = {"INTERNAL_GITLAB_TOKEN": "glpat"}
    assert (
        run(monkeypatch, router, ["--config", str(config), "targets", "--targets", "all", "--json"], environment) == 0
    )
    listed = json.loads(capsys.readouterr().out)
    assert [(entry["repository"], entry["set"]) for entry in listed] == [
        ("platform/ci-tools", "nightly"),
        ("team/ci-tools", "weekly"),
    ]
    assert all(request.headers["private-token"] == "glpat" for request in router.requests)


def test_several_target_sets_require_a_choice(
    tmp_path: Path, router: Router, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "toolbot.yaml"
    config.write_text(CONTROL_YAML)
    assert run(monkeypatch, router, ["--config", str(config), "check"]) == 2
    assert any("choose a target set with --targets (nightly, weekly, all)" in line for line in LAST_LOG)
    assert run(monkeypatch, router, ["--config", str(config), "check", "--targets", "hourly"]) == 2
    assert any('unknown target set "hourly"' in line for line in LAST_LOG)


def test_update_command_latest_and_explicit(repo: GitSetup, router: Router, monkeypatch: pytest.MonkeyPatch) -> None:
    assert run(monkeypatch, router, ["--root", str(repo.work), "update", "widget", "--latest"]) == 0
    assert 'version: "2.0.0"' in (repo.work / "tools/widget/tool.yaml").read_text()
    assert run(monkeypatch, router, ["--root", str(repo.work), "update", "widget", "--version", "1.0.0"]) == 0
    assert 'version: "1.0.0"' in (repo.work / "tools/widget/tool.yaml").read_text()


def test_check_requires_target_configuration(repo: GitSetup, router: Router, monkeypatch: pytest.MonkeyPatch) -> None:
    assert run(monkeypatch, router, ["--root", str(repo.work), "check"]) == 2


def test_usage_errors_exit_2(monkeypatch: pytest.MonkeyPatch, router: Router) -> None:
    with pytest.raises(SystemExit) as error:
        run(monkeypatch, router, ["update"])
    assert error.value.code == 2
