"""Running `toolbot check` across several target repositories (control-repository mode)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import (
    FakeTarget,
    GitHubRelease,
    Router,
    commit_all,
    git,
    github_releases,
    make_context,
    widget_files,
    widget_yaml,
    write_tool,
)

from toolbot.config.control import ControlConfig, Defaults, Rule
from toolbot.errors import ToolbotError
from toolbot.hosts import Host
from toolbot.providers.target.base import TargetProvider
from toolbot.services.artifacts import apply_version
from toolbot.services.context import Context, build_host_registry
from toolbot.services.runner import check_repositories
from toolbot.services.targets import TargetRepository, environment_target

HOST = Host("internal", "gitlab", "https://gitlab.example.com", "INTERNAL_TOKEN")


@dataclass
class Remote:
    path: str
    remote: Path
    target: FakeTarget


def make_remote(tmp_path: Path, router: Router, repository_path: str) -> Remote:
    safe_name = repository_path.replace("/", "-")
    remote = tmp_path / f"{safe_name}.git"
    work = tmp_path / safe_name
    git(tmp_path, "init", "--quiet", "--bare", "--initial-branch=main", str(remote))
    git(tmp_path, "clone", "--quiet", remote.as_uri(), str(work))
    git(work, "checkout", "--quiet", "-b", "main")
    write_tool(work, "widget", widget_yaml("1.0.0"))
    apply_version(make_context(router), work, "widget")
    commit_all(work)
    return Remote(repository_path, remote, FakeTarget(remote.as_uri(), repository=repository_path))


@pytest.fixture
def remotes(tmp_path: Path, router: Router) -> dict[str, Remote]:
    github_releases(router, [GitHubRelease("v1.0.0", widget_files("1.0.0"))])
    created = {path: make_remote(tmp_path, router, path) for path in ("platform/ci-tools", "team/build-images")}
    github_releases(
        router, [GitHubRelease("v2.0.0", widget_files("2.0.0")), GitHubRelease("v1.0.0", widget_files("1.0.0"))]
    )
    return created


def factory(remotes: dict[str, Remote], broken: set[str] | None = None):  # type: ignore[no-untyped-def]
    def open_fake_target(_ctx: Context, repository: TargetRepository) -> TargetProvider:
        if broken and repository.path in broken:
            raise ToolbotError("AUTH_FAILED", f"no access to {repository.path}")
        return remotes[repository.path].target

    return open_fake_target


def repositories(*paths: str) -> list[TargetRepository]:
    return [TargetRepository(HOST, path, target_set="nightly") for path in paths]


def test_every_target_repository_gets_its_own_update(remotes: dict[str, Remote], router: Router) -> None:
    ctx = make_context(router)
    results = check_repositories(ctx, repositories(*remotes), target_factory=factory(remotes))
    assert [result.error for result in results] == [None, None]
    for result in results:
        [outcome] = result.outcomes
        assert outcome.status == "created"
        target = remotes[result.repository.path].target
        assert [request.source_branch for request in target.requests] == ["toolbot/widget/2.0.0"]
        branches = git(remotes[result.repository.path].remote, "for-each-ref", "--format=%(refname:short)")
        assert "toolbot/widget/2.0.0" in branches


def test_rules_can_target_specific_repositories(remotes: dict[str, Remote], router: Router) -> None:
    control = ControlConfig(rules=(Rule(tools=("widget",), repositories=("team/*",), enabled=False),))
    ctx = make_context(router, control=control)
    results = check_repositories(ctx, repositories(*remotes), target_factory=factory(remotes))
    statuses = {result.repository.path: result.outcomes[0].status for result in results}
    assert statuses == {"platform/ci-tools": "created", "team/build-images": "disabled"}


def test_one_unreachable_repository_does_not_stop_the_others(remotes: dict[str, Remote], router: Router) -> None:
    ctx = make_context(router)
    results = check_repositories(
        ctx, repositories(*remotes), target_factory=factory(remotes, broken={"platform/ci-tools"})
    )
    assert results[0].error == "no access to platform/ci-tools"
    assert results[1].outcomes[0].status == "created"


def test_dry_run_changes_nothing_anywhere(remotes: dict[str, Remote], router: Router) -> None:
    ctx = make_context(router)
    before = {path: git(remote.remote, "for-each-ref") for path, remote in remotes.items()}
    results = check_repositories(ctx, repositories(*remotes), dry_run=True, target_factory=factory(remotes))
    for result in results:
        [outcome] = result.outcomes
        assert outcome.status == "would-create"
        assert outcome.branch == "toolbot/widget/2.0.0"
        assert any("widget-2.0.0-windows-amd64.zip" in line for line in outcome.details)
        assert remotes[result.repository.path].target.requests == []
    assert {path: git(remote.remote, "for-each-ref") for path, remote in remotes.items()} == before
    # The fixture downloaded 1.0.0 to build the remotes; the dry run downloads nothing of 2.0.0.
    assert not [
        request for request in router.to("https://release-assets.githubusercontent.com") if "v2.0.0" in str(request.url)
    ]


def test_defaults_from_toolbot_yaml_and_environment_overrides(remotes: dict[str, Remote], router: Router) -> None:
    control = ControlConfig(
        defaults=Defaults(labels=("deps",), git_author_name="Platform Bot", git_author_email="bot@example.com")
    )
    ctx = make_context(router, {"TOOLBOT_GIT_AUTHOR_EMAIL": "override@example.com"}, control=control)
    check_repositories(ctx, repositories("platform/ci-tools"), target_factory=factory(remotes))
    remote = remotes["platform/ci-tools"]
    assert remote.target.labels[1] == ("deps",)
    author = git(remote.remote, "log", "-1", "--format=%an <%ae>", "toolbot/widget/2.0.0")
    assert author == "Platform Bot <override@example.com>"


class TestEnvironmentTarget:
    def test_gitlab_ci(self) -> None:
        environment = {
            "GITLAB_CI": "true",
            "CI_SERVER_URL": "https://gitlab.example.com",
            "CI_PROJECT_PATH": "team/tools",
        }
        repository = environment_target(environment, build_host_registry(ControlConfig(), environment))
        assert (repository.host.type, repository.host.url, repository.path) == (
            "gitlab",
            "https://gitlab.example.com",
            "team/tools",
        )
        assert repository.host.token_env == "TOOLBOT_GITLAB_TOKEN"

    def test_github_actions(self) -> None:
        environment = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": "acme/tools",
            "GITHUB_SERVER_URL": "https://github.com",
        }
        repository = environment_target(environment, build_host_registry(ControlConfig(), environment))
        assert (repository.host.type, repository.path, repository.host.token_env) == (
            "github",
            "acme/tools",
            "TOOLBOT_GITHUB_TOKEN",
        )

    def test_missing_configuration(self) -> None:
        with pytest.raises(ToolbotError, match="TOOLBOT_GITLAB_URL"):
            environment_target({}, build_host_registry(ControlConfig(), {}))
