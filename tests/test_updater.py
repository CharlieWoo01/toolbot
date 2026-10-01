"""The `toolbot check` lifecycle against a real git remote and a fake target."""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from conftest import (
    FakeTarget,
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

from toolbot.errors import ToolbotError
from toolbot.git import GitIdentity, Repository
from toolbot.services.artifacts import apply_version
from toolbot.services.updater import Outcome, Updater, UpdateSettings

OTHER_API = "https://api.github.com/repos/acme/other"
OTHER_YAML = """\
name: {name}
version: "{version}"
tag: v{{version}}
source: {{provider: github, repository: acme/{repo}}}
checksum: {{type: provider}}
assets:
  linux: {{name: "{repo}-{{version}}.tar.gz", destination: {repo}.tar.gz}}
"""


@dataclass
class Env:
    setup: GitSetup
    router: Router
    target: FakeTarget
    releases: dict[str, list[GitHubRelease]]

    def publish(self, tool: str, version: str) -> None:
        """Upstream publishes a release (all releases so far stay listed)."""
        if tool == "widget":
            self.releases[tool].insert(0, GitHubRelease(f"v{version}", widget_files(version)))
            github_releases(self.router, self.releases[tool])
        else:
            self.releases[tool].insert(
                0, GitHubRelease(f"v{version}", {f"{tool}-{version}.tar.gz": f"{tool}{version}"})
            )
            github_releases(self.router, self.releases[tool], f"https://api.github.com/repos/acme/{tool}")

    def check(self, only: list[str] | None = None) -> dict[str, Outcome]:
        ctx = make_context(self.router)
        repo = Repository(self.setup.url, GitIdentity("Toolbot", "toolbot@example.com"))
        outcomes = Updater(ctx, self.target, repo, UpdateSettings("main", ("toolbot", "tool-update"))).run(only)
        return {o.tool: o for o in outcomes}

    def branches(self) -> list[str]:
        return self.setup.branches()


@pytest.fixture
def env(git_setup: GitSetup, router: Router) -> Env:
    """main has widget 1.0.0 installed and committed."""
    e = Env(git_setup, router, FakeTarget(git_setup.url), {"widget": []})
    write_tool(git_setup.work, "widget", widget_yaml("1.0.0"))
    e.publish("widget", "1.0.0")
    apply_version(make_context(router), git_setup.work, "widget")
    commit_all(git_setup.work)
    return e


def add_other_tool(env: Env, name: str = "other") -> None:
    env.releases[name] = []
    write_tool(env.setup.work, name, OTHER_YAML.format(name=name, repo=name, version="1.0.0"))
    env.publish(name, "1.0.0")
    apply_version(make_context(env.router), env.setup.work, name)
    commit_all(env.setup.work, f"add {name}")


def test_no_update(env: Env) -> None:
    result = env.check()
    assert result["widget"].status == "up-to-date"
    assert env.branches() == ["main"]
    assert env.target.requests == []


def test_update_creates_branch_commit_and_merge_request(env: Env) -> None:
    main_before = env.setup.head("main")
    env.publish("widget", "2.0.0")
    outcome = env.check()["widget"]

    assert outcome.status == "created", outcome.message
    assert env.branches() == ["main", "toolbot/widget/2.0.0"]
    assert env.setup.head("main") == main_before
    branch = "toolbot/widget/2.0.0"
    assert 'version: "2.0.0"' in env.setup.show(branch, "tools/widget/tool.yaml")
    assert env.setup.show(branch, "tools/widget/widget.zip") == "zip-2.0.0"
    assert '"version": "2.0.0"' in env.setup.show(branch, "tools/widget/tool.lock.json")
    message = git(env.setup.remote, "log", "-1", "--format=%B", branch)
    assert message.startswith("chore(tools): update widget to 2.0.0")
    assert "Toolbot-Tool: widget" in message and "Toolbot-Version: 2.0.0" in message
    changed = git(env.setup.remote, "diff", "--name-only", "main", branch).split()
    assert sorted(changed) == ["tools/widget/tool.lock.json", "tools/widget/tool.yaml", "tools/widget/widget.zip"]

    [request] = env.target.requests
    assert request.title == "chore(tools): update widget to 2.0.0"
    assert request.source_branch == branch and request.target_branch == "main"
    assert env.target.labels[request.id] == ("toolbot", "tool-update")
    description = env.target.descriptions[request.id]
    for expected in (
        "| Previous | `1.0.0` |",
        "| New | `2.0.0` |",
        "acme/widget",
        "releases/tag/v2.0.0",
        "widget-2.0.0-windows-amd64.zip",
        "✅ SHA256",
        '<!-- toolbot: {"tool": "widget"',
    ):
        assert expected in description


def test_rerun_does_not_duplicate(env: Env) -> None:
    env.publish("widget", "2.0.0")
    env.check()
    head = env.setup.head("toolbot/widget/2.0.0")
    outcome = env.check()["widget"]
    assert outcome.status == "exists"
    assert len(env.target.requests) == 1
    assert env.setup.head("toolbot/widget/2.0.0") == head


def test_newer_release_supersedes_open_request(env: Env) -> None:
    env.publish("widget", "2.0.0")
    env.check()
    env.publish("widget", "3.0.0")
    outcome = env.check()["widget"]

    assert outcome.status == "superseded", outcome.message
    old, new = env.target.requests
    assert old.state == "closed" and new.state == "open"
    assert new.source_branch == "toolbot/widget/3.0.0"
    assert env.target.comments[old.id] == ["Superseded by !2 (widget 3.0.0)."]
    assert "Supersedes !1." in env.target.descriptions[new.id]
    assert env.branches() == ["main", "toolbot/widget/3.0.0"]  # old branch deleted
    # The new update is relative to main, not stacked on the old branch.
    assert 'version: "3.0.0"' in env.setup.show("toolbot/widget/3.0.0", "tools/widget/tool.yaml")


def test_human_changes_block_superseding(env: Env) -> None:
    env.publish("widget", "2.0.0")
    env.check()
    env.setup.push_human_commit("toolbot/widget/2.0.0")
    env.publish("widget", "3.0.0")
    outcome = env.check()["widget"]
    assert outcome.status == "blocked"
    assert "commits not made by Toolbot" in outcome.message
    assert [r.state for r in env.target.requests] == ["open"]
    assert env.branches() == ["main", "toolbot/widget/2.0.0"]


def test_closed_request_means_rejected(env: Env) -> None:
    env.publish("widget", "2.0.0")
    env.check()
    env.target.set_state(1, "closed")
    outcome = env.check()["widget"]
    assert outcome.status == "rejected"
    assert "closed without merging" in outcome.message
    assert len(env.target.requests) == 1
    # A newer release is still proposed.
    env.publish("widget", "2.1.0")
    assert env.check()["widget"].status == "created"


def test_merged_but_reverted_is_not_reproposed(env: Env) -> None:
    env.publish("widget", "2.0.0")
    env.check()
    env.target.set_state(1, "merged")  # main still has 1.0.0 (reverted)
    assert env.check()["widget"].status == "reverted"
    assert len(env.target.requests) == 1


def test_ignored_version(env: Env) -> None:
    (env.setup.work / "tools/widget/tool.yaml").write_text(widget_yaml("1.0.0", 'ignore: ["2.0.0"]\n'))
    commit_all(env.setup.work, "ignore 2.0.0")
    env.publish("widget", "2.0.0")
    outcome = env.check()["widget"]
    assert outcome.status == "up-to-date" and "held back by ignore/allow: 2.0.0" in outcome.message
    assert env.target.requests == []


def test_leftover_toolbot_branch_is_reused(env: Env) -> None:
    env.publish("widget", "2.0.0")
    env.target.fail_create = True
    first = env.check()["widget"]
    assert first.status == "failed" and "was pushed" in first.message
    assert "toolbot/widget/2.0.0" in env.branches()
    env.target.fail_create = False
    second = env.check()["widget"]
    assert second.status == "created"
    assert len(env.target.requests) == 1


def test_existing_branch_with_human_commits_blocks(env: Env) -> None:
    env.publish("widget", "2.0.0")
    env.target.fail_create = True
    env.check()
    env.setup.push_human_commit("toolbot/widget/2.0.0")
    env.target.fail_create = False
    outcome = env.check()["widget"]
    assert outcome.status == "blocked"
    assert env.target.requests == []


def test_push_failure_does_not_stop_other_tools(env: Env) -> None:
    add_other_tool(env, "broken")
    add_other_tool(env, "other")
    hook = env.setup.remote / "hooks" / "pre-receive"
    hook.write_text(
        '#!/bin/sh\nwhile read old new ref; do case "$ref" in refs/heads/toolbot/broken/*) '
        'echo "rejected by policy" >&2; exit 1;; esac; done\nexit 0\n',
        newline="\n",
    )
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    for tool in ("widget", "broken", "other"):
        env.publish(tool, "2.0.0")
    result = env.check()
    assert result["broken"].status == "failed" and "push" in result["broken"].message
    assert result["widget"].status == "created"
    assert result["other"].status == "created"
    assert "toolbot/broken/2.0.0" not in env.branches()


def test_unavailable_upstream_does_not_stop_other_tools(env: Env) -> None:
    add_other_tool(env)
    env.publish("widget", "2.0.0")
    env.router.routes[("GET", f"{OTHER_API}/releases")] = lambda _r: httpx.Response(503)
    result = env.check()
    assert result["other"].status == "failed"
    assert result["widget"].status == "created"


def test_obsolete_request_closed_when_base_catches_up(env: Env) -> None:
    env.publish("widget", "2.0.0")
    env.check()
    # Someone updates main by other means (e.g. merged a manual change).
    apply_version(make_context(env.router), env.setup.work, "widget", "2.0.0")
    commit_all(env.setup.work, "manual update")
    outcome = env.check()["widget"]
    assert outcome.status == "up-to-date"
    assert env.target.requests[0].state == "closed"
    assert "Obsolete" in env.target.comments[1][0]
    assert env.branches() == ["main"]


def test_definitions_come_from_base_branch_not_local_edits(env: Env) -> None:
    (env.setup.work / "tools/widget/tool.yaml").write_text("garbage: [")  # uncommitted local edit
    env.publish("widget", "2.0.0")
    assert env.check()["widget"].status == "created"
    assert (env.setup.work / "tools/widget/tool.yaml").read_text() == "garbage: ["


def test_selected_tools_only(env: Env) -> None:
    add_other_tool(env)
    env.publish("widget", "2.0.0")
    env.publish("other", "2.0.0")
    result = env.check(["other"])
    assert list(result) == ["other"]
    assert env.branches() == ["main", "toolbot/other/2.0.0"]


def test_worktrees_and_refs_are_cleaned_up(env: Env) -> None:
    env.publish("widget", "2.0.0")
    env.check()
    assert git(env.setup.work, "for-each-ref", "refs/toolbot/") == ""
    assert len(git(env.setup.work, "worktree", "list").splitlines()) == 1


def test_invalid_definition_on_base_is_reported(env: Env, tmp_path: Path) -> None:
    write_tool(env.setup.work, "bad", "name: bad\n")
    commit_all(env.setup.work, "bad tool")
    env.publish("widget", "2.0.0")
    result = env.check()
    assert result["bad"].status == "failed"
    assert result["widget"].status == "created"


def test_private_repository_removed_even_if_base_fetch_fails(env: Env) -> None:
    ctx = make_context(env.router)
    repo = Repository(env.setup.url, GitIdentity("Toolbot", "toolbot@example.com"))
    updater = Updater(ctx, env.target, repo, UpdateSettings("no-such-branch"))
    with pytest.raises(ToolbotError):
        updater.run()
    assert not repo.root.exists()
