from __future__ import annotations

import json
from pathlib import Path

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
from toolbot.services.artifacts import apply_version

GITLAB = "https://gitlab.example.com"
PROJECT_API = f"{GITLAB}/api/v4/projects/team%2Fci-tools"


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


def run(monkeypatch: pytest.MonkeyPatch, router: Router, args: list[str], env: dict[str, str] | None = None) -> int:
    monkeypatch.setattr(cli, "create_context", lambda: make_context(router, env or {}))
    return cli.main(args)


def state(setup: GitSetup) -> tuple[str, str, str, dict[str, bytes]]:
    files = {
        str(p.relative_to(setup.work)): p.read_bytes() for p in sorted((setup.work / "tools").rglob("*")) if p.is_file()
    }
    return (
        git(setup.work, "status", "--porcelain", "--ignored"),
        git(setup.work, "for-each-ref"),
        git(setup.remote, "for-each-ref"),
        files,
    )


def test_dry_run_changes_nothing(
    repo: GitSetup, router: Router, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = state(repo)
    already = len(router.requests)
    router.json(PROJECT_API, {"default_branch": "main"})
    router.json(f"{PROJECT_API}/merge_requests", [])
    env = {"TOOLBOT_GITLAB_TOKEN": "t", "TOOLBOT_GITLAB_URL": GITLAB, "TOOLBOT_GITLAB_PROJECT": "team/ci-tools"}
    code = run(monkeypatch, router, ["--root", str(repo.work), "check", "--dry-run"], env)
    out = capsys.readouterr().out

    assert code == 0
    assert "Current: 1.0.0" in out and "Latest:  2.0.0" in out and "Update available." in out
    assert "✓ widget-2.0.0-windows-amd64.zip" in out
    assert "Would create: toolbot/widget/2.0.0" in out
    assert state(repo) == before  # no files, branches, refs, commits
    during = router.requests[already:]
    assert [r for r in during if r.method != "GET"] == []  # no merge requests, labels, comments
    assert not [r for r in during if r.url.host == "release-assets.githubusercontent.com"]  # nothing downloaded


def test_dry_run_without_target_configuration(
    repo: GitSetup, router: Router, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(monkeypatch, router, ["--root", str(repo.work), "check", "--dry-run"]) == 0
    assert "existing merge requests not checked" in capsys.readouterr().out


def test_dry_run_reports_existing_request(
    repo: GitSetup, router: Router, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    router.json(PROJECT_API, {"default_branch": "main"})
    router.json(
        f"{PROJECT_API}/merge_requests",
        [
            {
                "iid": 7,
                "state": "opened",
                "source_branch": "toolbot/widget/2.0.0",
                "target_branch": "main",
                "web_url": "u",
                "title": "t",
            }
        ],
    )
    env = {"TOOLBOT_GITLAB_TOKEN": "t", "TOOLBOT_GITLAB_URL": GITLAB, "TOOLBOT_GITLAB_PROJECT": "team/ci-tools"}
    run(monkeypatch, router, ["--root", str(repo.work), "check", "--dry-run"], env)
    assert "merge request !7 is already open" in capsys.readouterr().out


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


def test_update_command_latest_and_explicit(repo: GitSetup, router: Router, monkeypatch: pytest.MonkeyPatch) -> None:
    assert run(monkeypatch, router, ["--root", str(repo.work), "update", "widget", "--latest"]) == 0
    assert 'version: "2.0.0"' in (repo.work / "tools/widget/tool.yaml").read_text()
    assert run(monkeypatch, router, ["--root", str(repo.work), "update", "widget", "--version", "1.0.0"]) == 0
    assert 'version: "1.0.0"' in (repo.work / "tools/widget/tool.yaml").read_text()


def test_check_requires_target_configuration(repo: GitSetup, router: Router, monkeypatch: pytest.MonkeyPatch) -> None:
    assert run(monkeypatch, router, ["--root", str(repo.work), "check"]) == 2


def test_usage_errors_exit_2(monkeypatch: pytest.MonkeyPatch, router: Router) -> None:
    with pytest.raises(SystemExit) as err:
        run(monkeypatch, router, ["update"])
    assert err.value.code == 2


def test_dry_run_fails_like_the_real_run_when_provider_digest_is_missing(
    tmp_path: Path, router: Router, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    yaml_text = widget_yaml("1.0.0").replace('type: file\n  file: "{asset}.sha256"', "type: provider")
    assert "type: provider" in yaml_text
    write_tool(tmp_path, "widget", yaml_text)
    github_releases(router, [GitHubRelease("v2.0.0", widget_files("2.0.0"), digests=False)])
    assert run(monkeypatch, router, ["--root", str(tmp_path), "check", "--dry-run"]) == 1
    assert "reported no digest" in capsys.readouterr().out
