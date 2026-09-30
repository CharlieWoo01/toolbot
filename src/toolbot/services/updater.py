"""The update lifecycle: ``toolbot check``.

For each tool, independently (one tool failing never stops the others):

1. **Discover** the newest acceptable release (``services.discovery``).
2. **Identify** the update by its deterministic branch
   ``toolbot/<tool>/<version>`` and look up change requests from that branch:

   * an **open** one exists → nothing to do (idempotent re-runs);
   * a **closed** (not merged) one exists → the version was rejected; skip it.
     Newer versions are still proposed;
   * a **merged** one exists but the base branch doesn't have it (reverted)
     → skip, don't fight the revert.

3. **One active update per tool.** Other open Toolbot change requests for the
   same tool (older or now-ignored versions) are superseded: after the new
   request is created, they are closed with a comment and their branches
   deleted, but only if their head commit is still Toolbot's own. If a human
   pushed to such a branch, Toolbot stops and reports the tool as *blocked*
   rather than discard their work.
4. **Apply** the update in a temporary worktree based on the latest base
   branch: download, verify, replace artifacts, bump ``version``, commit.
5. **Push** with ``--force-with-lease`` (new branch: must not exist; existing
   Toolbot-only branch from an interrupted run: must be unchanged).
6. **Create** the change request (labels ensured), then supersede.

When a tool is already up to date, open Toolbot requests for versions the
base branch already has are closed as obsolete.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from toolbot.assets.matching import resolve_assets
from toolbot.config.loader import LoadResult, load_all
from toolbot.config.models import ToolDefinition
from toolbot.config.placeholders import render
from toolbot.errors import ToolbotError
from toolbot.git import Repository, Worktree
from toolbot.providers.target.base import ChangeRequest, ChangeRequestSpec, TargetProvider
from toolbot.services import messages
from toolbot.services.artifacts import apply_version
from toolbot.services.context import Context
from toolbot.services.discovery import Discovery, discover
from toolbot.versions import parse_version

OutcomeStatus = Literal[
    "up-to-date",
    "ahead",
    "no-releases",
    "created",
    "superseded",
    "exists",
    "rejected",
    "reverted",
    "blocked",
    "failed",
    "would-create",
]
FAILURE_STATUSES = frozenset({"failed"})


@dataclass
class Outcome:
    tool: str
    status: OutcomeStatus
    message: str = ""
    current: str | None = None
    latest: str | None = None
    url: str | None = None
    branch: str | None = None
    details: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class UpdateSettings:
    base_branch: str
    labels: tuple[str, ...] = ("toolbot", "tool-update")


class Updater:
    def __init__(self, ctx: Context, target: TargetProvider, repo: Repository, settings: UpdateSettings) -> None:
        self.ctx = ctx
        self.target = target
        self.repo = repo
        self.settings = settings

    def run(self, only: Sequence[str] | None = None) -> list[Outcome]:
        outcomes: list[Outcome] = []
        try:
            base_commit = self.repo.fetch_branch(self.settings.base_branch)
            with self.repo.worktree(base_commit) as worktree:
                # Definitions come from the base branch itself, not from the caller's checkout.
                loaded = load_all(worktree.path, self.ctx.providers, list(only) if only else None)
                outcomes.extend(_load_failures(loaded))
                for tool in loaded.tools:
                    try:
                        outcomes.append(self._update_tool(tool, worktree, base_commit))
                    except ToolbotError as err:
                        outcomes.append(Outcome(tool.name, "failed", err.message, current=tool.version))
        finally:
            self.repo.cleanup()
        return outcomes

    def _update_tool(self, tool: ToolDefinition, worktree: Worktree, base_commit: str) -> Outcome:
        found = discover(self.ctx, tool)
        base = self.settings.base_branch
        prefix = messages.branch_prefix(tool.name)
        open_requests = self.target.list_open(prefix, base)

        if found.status != "update-available":
            self._close_obsolete(tool, open_requests)
            return _not_outdated(found)
        latest = found.latest
        if latest is None:  # implied by status; keeps the type checker honest
            return _not_outdated(found)
        version = latest.version.raw
        branch = messages.branch_name(tool.name, version)
        outcome = Outcome(tool.name, "created", current=tool.version, latest=version, branch=branch)

        existing = self.target.find_by_branch(branch)
        if blocked := _existing_blocks(existing, outcome, base, self.target.kind):
            return blocked

        # Other open Toolbot requests for this tool get superseded, unless a human touched them.
        others = [r for r in open_requests if r.source_branch != branch]
        other_heads: dict[int, str] = {}
        for request in others:
            head = self._toolbot_head(tool.name, request.source_branch)
            if head is None:
                outcome.status = "blocked"
                outcome.message = (
                    f"{request.ref} ({request.source_branch}) has commits not made by Toolbot; not superseding it. "
                    f"Merge or close it, and Toolbot will propose {version} on the next run."
                )
                outcome.url = request.url
                return outcome
            other_heads[request.id] = head

        # A branch without a change request is left over from an interrupted run.
        expected = self.repo.remote_head(branch)
        if expected is not None and self._toolbot_head(tool.name, branch) is None:
            outcome.status = "blocked"
            outcome.message = f"branch {branch} exists with commits not made by Toolbot; delete or merge it"
            return outcome

        worktree.reset(branch, base_commit)
        result = apply_version(self.ctx, worktree.path, tool.name, version, latest.release)
        commit = worktree.commit([f"tools/{tool.name}"], messages.commit_message(tool.name, tool.version, version))
        if commit is None:
            return Outcome(tool.name, "up-to-date", "no changes to commit", tool.version, version)
        self.repo.push(worktree.path, branch, expected)

        superseding = [r.ref for r in others]
        spec = ChangeRequestSpec(
            source_branch=branch,
            target_branch=base,
            title=messages.title(tool.name, version),
            description=messages.description(tool, result, superseding),
            labels=self.settings.labels,
        )
        try:
            request = self.target.create(spec)
        except ToolbotError as err:
            raise ToolbotError(
                err.code,
                f"branch {branch} was pushed but creating the {self.target.kind} failed: {err.message}. "
                "The next run will retry.",
            ) from None
        outcome.url = request.url
        outcome.message = f"{self.target.kind} {request.ref}"
        for old in others:
            self._retire(old, other_heads[old.id], f"Superseded by {request.ref} ({tool.name} {version}).")
            outcome.status = "superseded"
            outcome.details.append(f"closed {old.ref} ({old.source_branch})")
        outcome.details.extend(f"{a.id}: {a.upstream} ({a.verified_by})" for a in result.assets)
        return outcome

    def _close_obsolete(self, tool: ToolDefinition, open_requests: list[ChangeRequest]) -> None:
        current = parse_version(tool.version, tool.versioning)
        for request in open_requests:
            text = messages.version_from_branch(tool.name, request.source_branch)
            try:
                version = parse_version(text, tool.versioning) if text else None
            except ToolbotError:
                version = None
            if version is None or version > current:
                continue
            head = self._toolbot_head(tool.name, request.source_branch)
            if head is None:
                self.ctx.warn(f"{tool.name}: {request.ref} is obsolete but has non-Toolbot commits; leaving it open")
                continue
            self._retire(
                request, head, f"Obsolete: `{self.settings.base_branch}` already has {tool.name} {tool.version}."
            )

    def _retire(self, request: ChangeRequest, head: str, comment: str) -> None:
        try:
            self.target.close(request, comment)
            self.repo.delete_branch(request.source_branch, head)
        except ToolbotError as err:
            self.ctx.warn(f"could not close {request.ref}: {err.message}")

    def _toolbot_head(self, tool: str, branch: str) -> str | None:
        """Head commit of ``branch`` if it was made by Toolbot for ``tool``, else None.

        Only the head commit is inspected: Toolbot branches carry exactly one
        Toolbot commit, so anything pushed on top by a person shows up here.
        GitLab's "Rebase" button keeps the commit message and trailers, so a
        rebased Toolbot branch still counts as Toolbot's.
        """
        head = self.repo.fetch_branch(branch)
        trailers = self.repo.trailers(head)
        return head if trailers.get(messages.TRAILER_TOOL) == tool else None


def _existing_blocks(existing: list[ChangeRequest], outcome: Outcome, base: str, kind: str) -> Outcome | None:
    for request in existing:
        outcome.url = request.url
        if request.state == "open":
            outcome.status = "exists"
            outcome.message = f"{kind} {request.ref} is already open"
            return outcome
    for request in existing:
        if request.state == "merged":
            outcome.url = request.url
            outcome.status = "reverted"
            outcome.message = (
                f"{request.ref} for {outcome.latest} was merged, but {base} has {outcome.current} "
                "(reverted?). Not proposing it again."
            )
            return outcome
    for request in existing:
        if request.state == "closed":
            outcome.url = request.url
            outcome.status = "rejected"
            outcome.message = (
                f"{outcome.latest} was rejected ({request.ref} closed without merging). Reopen it to proceed, "
                f'or add "{outcome.latest}" to ignore in tool.yaml to make this explicit.'
            )
            return outcome
    return None


def _not_outdated(found: Discovery) -> Outcome:
    latest = found.latest.version.raw if found.latest else None
    note = f"ignored newer: {', '.join(found.ignored)}" if found.ignored else ""
    status: OutcomeStatus = (
        "up-to-date" if found.status == "up-to-date" else ("ahead" if found.status == "ahead" else "no-releases")
    )
    message = {
        "up-to-date": "up to date",
        "ahead": "current version is newer than any acceptable release",
        "no-releases": "no acceptable releases found (check tag, prereleases and ignore settings)",
    }[status]
    return Outcome(found.tool.name, status, message + (f"; {note}" if note else ""), found.tool.version, latest)


def _load_failures(loaded: LoadResult) -> list[Outcome]:
    return [Outcome(name, "failed", err.message) for name, err in loaded.errors.items()]


def plan(
    ctx: Context,
    root: Path,
    only: Sequence[str] | None,
    target: TargetProvider | None,
    base_branch: str | None,
) -> list[Outcome]:
    """``toolbot check --dry-run``: everything up to the point of changing anything.

    Reads definitions from ``root``, discovers releases, resolves assets and
    checksum files against the release (no downloads), and, if a target is
    configured, looks up existing change requests read-only. Never writes
    files, runs git, pushes or creates change requests.
    """
    loaded = load_all(root, ctx.providers, list(only) if only else None)
    outcomes = _load_failures(loaded)
    for tool in loaded.tools:
        try:
            found = discover(ctx, tool)
            if found.status != "update-available":
                outcomes.append(_not_outdated(found))
                continue
            if found.latest is None:
                outcomes.append(_not_outdated(found))
                continue
            version = found.latest.version.raw
            release = found.latest.release
            branch = messages.branch_name(tool.name, version)
            outcome = Outcome(
                tool.name, "would-create", current=tool.version, latest=version, url=release.url, branch=branch
            )
            values = {"version": version, "tag": release.tag}
            label = f"{tool.source.provider}:{tool.source.repository}@{release.tag}"
            for resolved in resolve_assets(tool.assets, release.assets, values, label):
                check = resolved.config.checksum
                if check.type == "file":
                    name = render(check.file, {**values, "asset": resolved.upstream.name})
                    verification = f"{check.algorithm} via {name}"
                    if not any(a.name == name for a in release.assets):
                        raise ToolbotError(
                            "CHECKSUM_UNAVAILABLE", f'checksum file "{name}" is not attached to release {release.tag}'
                        )
                elif check.type == "provider":
                    if resolved.upstream.digest is None:
                        raise ToolbotError(
                            "CHECKSUM_UNAVAILABLE",
                            f'{tool.source.provider} reported no digest for "{resolved.upstream.name}"',
                        )
                    verification = "provider digest"
                else:
                    verification = "not verified"
                outcome.details.append(f"✓ {resolved.upstream.name} → {resolved.config.destination} [{verification}]")
            if target is not None and base_branch is not None:
                existing = target.find_by_branch(branch)
                if blocked := _existing_blocks(existing, outcome, base_branch, target.kind):
                    outcomes.append(blocked)
                    continue
                others = [
                    r
                    for r in target.list_open(messages.branch_prefix(tool.name), base_branch)
                    if r.source_branch != branch
                ]
                if others:
                    outcome.details.append("would supersede: " + ", ".join(r.ref for r in others))
            else:
                outcome.details.append("(target not configured: existing merge requests not checked)")
            outcomes.append(outcome)
        except ToolbotError as err:
            outcomes.append(Outcome(tool.name, "failed", err.message, current=tool.version))
    return outcomes
