from __future__ import annotations

import base64
from pathlib import Path

import pytest
from conftest import GitSetup, commit_all, git

from toolbot.errors import ToolbotError
from toolbot.git import Git, GitAuth, GitIdentity, Repository, check_branch_name

IDENTITY = GitIdentity("Toolbot", "toolbot@example.com")


@pytest.fixture
def repo(git_setup: GitSetup) -> Repository:
    (git_setup.work / "README.md").write_text("hi\n")
    commit_all(git_setup.work)
    return Repository(git_setup.url, IDENTITY)


def test_worktree_commit_push_and_lease(repo: Repository, git_setup: GitSetup) -> None:
    base = repo.fetch_branch("main")
    with repo.worktree(base) as wt:
        wt.reset("toolbot/x/1.0.0", base)
        (wt.path / "tools").mkdir()
        (wt.path / "tools" / "f").write_text("data")
        commit = wt.commit(["tools"], "chore: x\n\nToolbot-Tool: x\nToolbot-Version: 1.0.0\n")
        assert commit is not None
        repo.push(wt.path, "toolbot/x/1.0.0", expected=None)
        # A concurrent run producing a *different* commit must be refused:
        # the branch now exists, so the "must not exist" lease fails.
        (wt.path / "tools" / "f").write_text("other run")
        wt.commit(["tools"], "chore: x (second run)\n\nToolbot-Tool: x\n")
        with pytest.raises(ToolbotError, match="push"):
            repo.push(wt.path, "toolbot/x/1.0.0", expected=None)
        # With the correct lease the update goes through.
        repo.push(wt.path, "toolbot/x/1.0.0", expected=git_setup.head("toolbot/x/1.0.0"))
    assert git_setup.show("toolbot/x/1.0.0", "tools/f") == "other run"
    assert "toolbot/x/1.0.0" in git_setup.branches()
    assert repo.trailers(repo.fetch_branch("toolbot/x/1.0.0")) == {"Toolbot-Tool": "x"}
    assert repo.trailers(repo.fetch_branch("toolbot/x/1.0.0", depth=2) + "~1") == {
        "Toolbot-Tool": "x",
        "Toolbot-Version": "1.0.0",
    }
    author = git(git_setup.remote, "log", "-1", "--format=%an <%ae>", "toolbot/x/1.0.0")
    assert author == "Toolbot <toolbot@example.com>"


def test_callers_checkout_is_never_touched(repo: Repository, git_setup: GitSetup) -> None:
    (git_setup.work / "README.md").write_text("uncommitted local edit\n")
    before_branch = git(git_setup.work, "rev-parse", "--abbrev-ref", "HEAD")
    base = repo.fetch_branch("main")
    with repo.worktree(base) as wt:
        wt.reset("toolbot/x/1.0.0", base)
        (wt.path / "new").write_text("x")
        wt.commit(["new"], "msg")
    repo.cleanup()
    assert not repo.root.exists()  # private temporary repository removed
    assert (git_setup.work / "README.md").read_text() == "uncommitted local edit\n"
    assert git(git_setup.work, "rev-parse", "--abbrev-ref", "HEAD") == before_branch
    assert git(git_setup.work, "for-each-ref", "refs/toolbot/") == ""
    assert git(git_setup.work, "worktree", "list").count("\n") == 0


def test_delete_branch_with_lease(repo: Repository, git_setup: GitSetup) -> None:
    base = repo.fetch_branch("main")
    with repo.worktree(base) as wt:
        wt.reset("toolbot/x/1.0.0", base)
        (wt.path / "f").write_text("x")
        wt.commit(["f"], "msg")
        repo.push(wt.path, "toolbot/x/1.0.0", None)
    head = git_setup.head("toolbot/x/1.0.0")
    with pytest.raises(ToolbotError):
        repo.delete_branch("toolbot/x/1.0.0", "0" * 40)  # stale lease
    repo.delete_branch("toolbot/x/1.0.0", head)
    assert git_setup.branches() == ["main"]


def test_remote_head(repo: Repository) -> None:
    assert repo.remote_head("main") is not None
    assert repo.remote_head("nope") is None


def test_nothing_to_commit(repo: Repository) -> None:
    base = repo.fetch_branch("main")
    with repo.worktree(base) as wt:
        wt.reset("b", base)
        assert wt.commit(["."], "msg") is None


def test_credentials_go_through_env_config_not_argv(tmp_path: Path) -> None:
    auth = GitAuth("https://gitlab.example.com/team/ci-tools.git", "oauth2", "glpat-secret")
    config = auth.config()
    [(key, value)] = config.items()
    assert key == "http.https://gitlab.example.com/.extraHeader"
    assert value == "Authorization: Basic " + base64.b64encode(b"oauth2:glpat-secret").decode()
    runner = Git(tmp_path, config=config, secrets=["glpat-secret"])
    assert runner._env["GIT_CONFIG_KEY_0"] == key
    assert "glpat-secret" not in " ".join(runner._env.get("GIT_CONFIG_KEY_0", ""))
    # Secrets are redacted from error output.
    assert runner._redact("fatal: glpat-secret rejected") == "fatal: *** rejected"


def test_git_errors_are_reported(tmp_path: Path) -> None:
    with pytest.raises(ToolbotError) as err:
        Git(tmp_path).run("rev-parse", "--show-toplevel")
    assert err.value.code == "GIT_ERROR"


@pytest.mark.parametrize("branch", ["toolbot/x/1..2", "toolbot/x/1.0.lock", "toolbot/x/a b", "toolbot/x/@{1}"])
def test_invalid_branch_names(branch: str) -> None:
    with pytest.raises(ToolbotError):
        check_branch_name(branch)


def test_identity_rejects_injection() -> None:
    with pytest.raises(ToolbotError):
        GitIdentity("Toolbot\nX-Evil: 1", "a@b")


def test_personal_git_config_cannot_break_unattended_commits(
    repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A developer's global config with forced signing and a failing global hook must not affect Toolbot."""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\necho blocked by global hook >&2\nexit 1\n", newline="\n")
    hook.chmod(0o755)
    global_config = tmp_path / "gitconfig"
    global_config.write_text(f"[commit]\n\tgpgSign = true\n[core]\n\thooksPath = {hooks.as_posix()}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    hardened = Repository(repo.remote_url, IDENTITY)
    try:
        base = hardened.fetch_branch("main")
        with hardened.worktree(base) as wt:
            wt.reset("toolbot/x/1.0.0", base)
            (wt.path / "f").write_text("x")
            assert wt.commit(["f"], "msg") is not None
    finally:
        hardened.cleanup()
