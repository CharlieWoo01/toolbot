"""Git operations behind a small service.

* Every command is an argument list passed to ``subprocess.run``; no shell,
  no string interpolation.
* Credentials never appear in argv, URLs, or logs: they are injected as a
  URL-scoped ``http.<origin>/.extraHeader`` through git's environment-based
  configuration (``GIT_CONFIG_COUNT``/``GIT_CONFIG_KEY_n``/``GIT_CONFIG_VALUE_n``).
* All work happens in a private temporary repository and worktree owned by
  Toolbot, so an existing checkout (a developer's working copy, or the CI
  clone) is never read from, switched, reset or modified.
* Pushes use ``--force-with-lease`` with an explicit expected value, so a
  concurrent change to a branch is never overwritten.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from toolbot.errors import ToolbotError

REF_NAMESPACE = "refs/toolbot"

# Command-level overrides of user/system git config for Toolbot's own
# repository: no signing prompts, no global hooks, no credential helpers
# (e.g. Git Credential Manager popping up). Proxy and CA settings still apply.
SAFE_CONFIG = {
    "commit.gpgSign": "false",
    "tag.gpgSign": "false",
    "core.hooksPath": os.devnull,
    "credential.helper": "",
    "core.fsmonitor": "false",
    "advice.detachedHead": "false",
}
_TRAILER = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):\s*(.*)$")


@dataclass(frozen=True)
class GitIdentity:
    name: str
    email: str

    def __post_init__(self) -> None:
        for value in (self.name, self.email):
            if not value or any(c in value for c in "\n\r<>\0"):
                raise ToolbotError("USAGE", "git author name/email must be non-empty single-line values")


@dataclass(frozen=True)
class GitAuth:
    """HTTP basic credentials for one origin, delivered via environment config."""

    url: str
    username: str
    token: str

    def config(self) -> dict[str, str]:
        parts = urlsplit(self.url)
        origin = f"{parts.scheme}://{parts.netloc}/"
        basic = base64.b64encode(f"{self.username}:{self.token}".encode()).decode()
        return {f"http.{origin}.extraHeader": f"Authorization: Basic {basic}"}


class Git:
    """Runs git in ``cwd`` with a controlled environment."""

    def __init__(
        self,
        cwd: Path,
        *,
        config: Mapping[str, str] | None = None,
        env: Mapping[str, str] | None = None,
        secrets: Sequence[str] = (),
        timeout: float = 600,
    ) -> None:
        self.cwd = cwd
        self._secrets = [s for s in secrets if s]
        self._timeout = timeout
        base = dict(os.environ)
        base.update(env or {})
        base.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
        base.pop("GIT_ASKPASS", None)
        if config:
            count = int(base.get("GIT_CONFIG_COUNT", "0") or "0")
            for offset, (key, value) in enumerate(config.items()):
                base[f"GIT_CONFIG_KEY_{count + offset}"] = key
                base[f"GIT_CONFIG_VALUE_{count + offset}"] = value
            base["GIT_CONFIG_COUNT"] = str(count + len(config))
        self._env = base

    def run(self, *args: str, stdin: str | None = None, ok_codes: tuple[int, ...] = (0,)) -> str:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=self.cwd,
                env=self._env,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except FileNotFoundError:
            raise ToolbotError("GIT_ERROR", "git is not installed or not on PATH") from None
        except subprocess.TimeoutExpired:
            raise ToolbotError("GIT_ERROR", f"git {args[0]} timed out") from None
        if proc.returncode not in ok_codes:
            detail = self._redact((proc.stderr or proc.stdout).strip()) or f"exit code {proc.returncode}"
            raise ToolbotError("GIT_ERROR", f"git {args[0]} failed: {detail}")
        return proc.stdout

    def _redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text


def check_branch_name(branch: str) -> None:
    """Reject anything git itself would not accept as a branch name."""
    proc = subprocess.run(["git", "check-ref-format", "--branch", branch], capture_output=True, text=True, check=False)
    if proc.returncode != 0 or proc.stdout.strip() != branch:
        raise ToolbotError("USAGE", f'"{branch}" is not a valid branch name')


class Repository:
    """The target repository, reached through ``remote_url``.

    Toolbot works in its own private, temporary bare repository: it fetches
    only what it needs (shallow) and deletes everything in ``cleanup``. It
    therefore never touches an existing checkout, needs no write access to
    the CI clone (which runners often create as another user), and behaves
    identically in CI and on a laptop.
    """

    def __init__(self, remote_url: str, identity: GitIdentity, auth: GitAuth | None = None) -> None:
        self.remote_url = remote_url
        self.identity = identity
        self._auth = auth
        self.root = Path(tempfile.mkdtemp(prefix="toolbot-repo-"))
        self.git = self._git(self.root)
        self.git.run("init", "--quiet", "--bare")

    def _git(self, cwd: Path) -> Git:
        return Git(
            cwd,
            config={**SAFE_CONFIG, **(self._auth.config() if self._auth else {})},
            env={
                "GIT_AUTHOR_NAME": self.identity.name,
                "GIT_AUTHOR_EMAIL": self.identity.email,
                "GIT_COMMITTER_NAME": self.identity.name,
                "GIT_COMMITTER_EMAIL": self.identity.email,
            },
            secrets=[self._auth.token] if self._auth else [],
        )

    def fetch_branch(self, branch: str, depth: int | None = 1) -> str:
        """Fetch ``branch`` from the remote into ``refs/toolbot/remote/<branch>``; return its commit."""
        local = f"{REF_NAMESPACE}/remote/{branch}"
        args = ["fetch", "--no-tags", "--quiet", "--no-write-fetch-head"]
        if depth:
            args.append(f"--depth={depth}")
        self.git.run(*args, self.remote_url, f"+refs/heads/{branch}:{local}")
        return self.git.run("rev-parse", local).strip()

    def remote_head(self, branch: str) -> str | None:
        out = self.git.run("ls-remote", "--heads", self.remote_url, f"refs/heads/{branch}").strip()
        return out.split()[0] if out else None

    def trailers(self, commit: str) -> dict[str, str]:
        out = self.git.run("log", "-1", "--format=%(trailers:only,unfold)", commit)
        result: dict[str, str] = {}
        for line in out.splitlines():
            if m := _TRAILER.match(line.strip()):
                result[m.group(1)] = m.group(2).strip()
        return result

    @contextmanager
    def worktree(self, commit: str) -> Iterator[Worktree]:
        path = Path(tempfile.mkdtemp(prefix="toolbot-worktree-"))
        path.rmdir()  # git creates it
        self.git.run("worktree", "add", "--detach", "--quiet", str(path), commit)
        try:
            yield Worktree(self, path)
        finally:
            try:
                self.git.run("worktree", "remove", "--force", str(path))
            except ToolbotError:
                _remove_tree(path)
                self.git.run("worktree", "prune")

    def push(self, cwd: Path, branch: str, expected: str | None) -> None:
        """Push HEAD of ``cwd`` to ``branch``, only if the remote still points at ``expected``
        (``None``: the branch must not exist)."""
        lease = f"--force-with-lease=refs/heads/{branch}:{expected or ''}"
        self._git(cwd).run("push", "--quiet", lease, self.remote_url, f"HEAD:refs/heads/{branch}")

    def delete_branch(self, branch: str, expected: str) -> None:
        lease = f"--force-with-lease=refs/heads/{branch}:{expected}"
        self.git.run("push", "--quiet", lease, self.remote_url, f":refs/heads/{branch}")

    def cleanup(self) -> None:
        _remove_tree(self.root)


def _remove_tree(path: Path) -> None:
    """rmtree that also removes read-only files (git marks objects read-only on Windows)."""

    def retry_writable(func: Callable[[str], object], target: str, *_exc: object) -> None:
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry_writable)
    else:  # pragma: no cover - Python 3.11
        shutil.rmtree(path, onerror=retry_writable)


class Worktree:
    def __init__(self, repo: Repository, path: Path) -> None:
        self.repo = repo
        self.path = path
        self.git = repo._git(path)

    def reset(self, branch: str, commit: str) -> None:
        """Start ``branch`` at ``commit`` with a pristine tree (this worktree is Toolbot's own)."""
        self.git.run("checkout", "--quiet", "--force", "-B", branch, commit)
        self.git.run("clean", "-ffdxq")

    def commit(self, paths: Sequence[str], message: str) -> str | None:
        """Stage ``paths`` and commit. Returns the commit, or None if nothing changed."""
        self.git.run("add", "--all", "--", *paths)
        if not self.git.run("diff", "--cached", "--name-only").strip():
            return None
        self.git.run("commit", "--quiet", "--file=-", stdin=message)
        return self.git.run("rev-parse", "HEAD").strip()
