"""The update lifecycle for one repository: ``toolbot check``.

For each tool, independently (one tool failing never stops the others):

1. **Discover** the newest acceptable release (``services.discovery``), after
   applying the central rules from toolbot.yaml.
2. **Identify** the update by its deterministic branch
   ``toolbot/<tool>/<version>`` and look up change requests from that branch:

   * an **open** one exists → nothing to do (idempotent re-runs);
   * a **closed** (not merged) one exists → the version was rejected; skip it.
     Newer versions are still proposed;
   * a **merged** one exists but the base branch doesn't have it (reverted)
     → skip, don't fight the revert.

3. **One active update per tool.** Other open Toolbot change requests for the
   same tool are superseded after the new one is created: closed with a
   comment and their branches deleted, but only if their head commit is still
   Toolbot's own. If a person pushed to such a branch the tool is *blocked*.
4. **Apply** the update in a temporary worktree based on the latest base
   branch: download, verify, replace artifacts, bump ``version``, commit.
5. **Push** with ``--force-with-lease``, then **create** the change request
   and supersede older ones.

In dry-run mode the same decisions are made from real data (definitions from
the base branch, existing change requests), but nothing is downloaded,
written, pushed, created or closed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from toolbot.assets.matching import resolve_assets
from toolbot.config.control import Rule, apply_rules
from toolbot.config.loader import LoadResult, load_all
from toolbot.config.models import ToolDefinition
from toolbot.errors import ToolbotError
from toolbot.git import Repository, Worktree
from toolbot.providers.source.base import Release
from toolbot.providers.target.base import ChangeRequest, ChangeRequestSpec, TargetProvider
from toolbot.services import messages
from toolbot.services.artifacts import apply_version, asset_download_request, checksum_source
from toolbot.services.context import Context
from toolbot.services.discovery import Discovery, discover
from toolbot.version_files import bump_version_files
from toolbot.versions import parse_version

OutcomeStatus = Literal[
    "up-to-date",
    "ahead",
    "no-releases",
    "disabled",
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
    repository_path: str = ""
    """Used to match central rules (``rules[].repositories``)."""
    dry_run: bool = False


class Updater:
    def __init__(
        self,
        ctx: Context,
        target: TargetProvider,
        repository: Repository,
        settings: UpdateSettings,
        rules: tuple[Rule, ...] = (),
    ) -> None:
        self.ctx = ctx
        self.target = target
        self.repository = repository
        self.settings = settings
        self.rules = rules

    def run(self, only: Sequence[str] | None = None) -> list[Outcome]:
        outcomes: list[Outcome] = []
        try:
            base_commit = self.repository.fetch_branch(self.settings.base_branch)
            with self.repository.worktree(base_commit) as worktree:
                # Definitions come from the base branch itself, not from any local checkout.
                loaded = load_all(worktree.path, self.ctx.providers, list(only) if only else None)
                outcomes.extend(load_failures(loaded))
                for definition in loaded.tools:
                    try:
                        tool = apply_rules(definition, self.rules, self.settings.repository_path)
                        if tool is None:
                            outcomes.append(Outcome(definition.name, "disabled", "disabled by a rule in toolbot.yaml"))
                            continue
                        outcomes.append(self._update_tool(tool, worktree, base_commit))
                    except ToolbotError as error:
                        outcomes.append(Outcome(definition.name, "failed", error.message, current=definition.version))
        finally:
            self.repository.cleanup()
        return outcomes

    def _update_tool(self, tool: ToolDefinition, worktree: Worktree, base_commit: str) -> Outcome:
        found = discover(self.ctx, tool)
        base_branch = self.settings.base_branch
        open_requests = self.target.list_open(messages.branch_prefix(tool.name), base_branch)

        if found.status != "update-available" or found.latest is None:
            self._close_obsolete(tool, open_requests)
            return not_outdated(found)
        version = found.latest.version.raw
        branch = messages.branch_name(tool.name, version)
        outcome = Outcome(tool.name, "created", current=tool.version, latest=version, branch=branch)

        existing = self.target.find_by_branch(branch)
        if decided := decide_from_existing(existing, outcome, base_branch, self.target.kind):
            return decided

        # Other open Toolbot requests for this tool get superseded, unless a person touched them.
        superseded = [request for request in open_requests if request.source_branch != branch]
        superseded_heads: dict[int, str] = {}
        for request in superseded:
            head = self._toolbot_head(tool.name, request.source_branch)
            if head is None:
                outcome.status = "blocked"
                outcome.message = (
                    f"{request.ref} ({request.source_branch}) has commits not made by Toolbot; not superseding it. "
                    f"Merge or close it, and Toolbot will propose {version} on the next run."
                )
                outcome.url = request.url
                return outcome
            superseded_heads[request.id] = head

        # A branch without a change request is left over from an interrupted run.
        expected_head = self.repository.remote_head(branch)
        if expected_head is not None and self._toolbot_head(tool.name, branch) is None:
            outcome.status = "blocked"
            outcome.message = f"branch {branch} exists with commits not made by Toolbot; delete or merge it"
            return outcome

        if self.settings.dry_run:
            outcome.status = "would-create"
            outcome.url = found.latest.release.url
            outcome.details.extend(preview_assets(self.ctx, tool, found.latest.release, version))
            if superseded:
                outcome.details.append("would supersede: " + ", ".join(request.ref for request in superseded))
            return outcome

        worktree.reset(branch, base_commit)
        result = apply_version(self.ctx, worktree.path, tool.name, version, found.latest.release)
        commit = worktree.commit(
            result.paths,
            messages.commit_message(tool.name, tool.version, version),
            executable=result.executable_paths,
            lfs=[asset.destination for asset in result.lfs_assets],
            untrack=result.fetched_paths,
        )
        if commit is None:
            return Outcome(tool.name, "up-to-date", "no changes to commit", tool.version, version)
        # LFS objects first: the branch must never point at pointers the server can't resolve.
        self.repository.push_lfs_objects(worktree.path, [asset.sha256 for asset in result.lfs_assets])
        self.repository.push(worktree.path, branch, expected_head)

        spec = ChangeRequestSpec(
            source_branch=branch,
            target_branch=base_branch,
            title=messages.title(tool.name, version),
            description=messages.description(tool, result, [request.ref for request in superseded]),
            labels=self.settings.labels,
        )
        try:
            request = self.target.create(spec)
        except ToolbotError as error:
            raise ToolbotError(
                error.code,
                f"branch {branch} was pushed but creating the {self.target.kind} failed: {error.message}. "
                "The next run will retry.",
            ) from None
        outcome.url = request.url
        outcome.message = f"{self.target.kind} {request.ref}"
        for old_request in superseded:
            self._retire(
                old_request,
                superseded_heads[old_request.id],
                f"Superseded by {request.ref} ({tool.name} {version}).",
            )
            outcome.status = "superseded"
            outcome.details.append(f"closed {old_request.ref} ({old_request.source_branch})")
        outcome.details.extend(f"{asset.id}: {asset.upstream} ({asset.verified_by})" for asset in result.assets)
        return outcome

    def _close_obsolete(self, tool: ToolDefinition, open_requests: list[ChangeRequest]) -> None:
        current = parse_version(tool.version, tool.versioning)
        for request in open_requests:
            branch_version = messages.version_from_branch(tool.name, request.source_branch)
            try:
                version = parse_version(branch_version, tool.versioning) if branch_version else None
            except ToolbotError:
                version = None
            if version is None or version > current:
                continue
            head = self._toolbot_head(tool.name, request.source_branch)
            if head is None:
                self.ctx.warn(f"{tool.name}: {request.ref} is obsolete but has non-Toolbot commits; leaving it open")
                continue
            if self.settings.dry_run:
                self.ctx.log(f"toolbot: would close obsolete {request.ref} ({request.source_branch})")
                continue
            self._retire(
                request, head, f"Obsolete: `{self.settings.base_branch}` already has {tool.name} {tool.version}."
            )

    def _retire(self, request: ChangeRequest, head: str, comment: str) -> None:
        try:
            self.target.close(request, comment)
            self.repository.delete_branch(request.source_branch, head)
        except ToolbotError as error:
            self.ctx.warn(f"could not close {request.ref}: {error.message}")

    def _toolbot_head(self, tool: str, branch: str) -> str | None:
        """Head commit of ``branch`` if it was made by Toolbot for ``tool``, else None.

        Only the head commit is inspected: Toolbot branches carry exactly one
        Toolbot commit, so anything pushed on top by a person shows up here.
        GitLab's "Rebase" button keeps the commit message and trailers, so a
        rebased Toolbot branch still counts as Toolbot's.
        """
        head = self.repository.fetch_branch(branch)
        trailers = self.repository.trailers(head)
        return head if trailers.get(messages.TRAILER_TOOL) == tool else None


def decide_from_existing(
    existing: list[ChangeRequest], outcome: Outcome, base_branch: str, kind: str
) -> Outcome | None:
    """Existing change requests for the exact tool + version decide the outcome, if any."""
    for request in existing:
        if request.state == "open":
            outcome.url = request.url
            outcome.status = "exists"
            outcome.message = f"{kind} {request.ref} is already open"
            return outcome
    for request in existing:
        if request.state == "merged":
            outcome.url = request.url
            outcome.status = "reverted"
            outcome.message = (
                f"{request.ref} for {outcome.latest} was merged, but {base_branch} has {outcome.current} "
                "(reverted?). Not proposing it again."
            )
            return outcome
    for request in existing:
        if request.state == "closed":
            outcome.url = request.url
            outcome.status = "rejected"
            outcome.message = (
                f"{outcome.latest} was rejected ({request.ref} closed without merging). Reopen it to proceed, "
                f'or add "{outcome.latest}" to ignore to make this explicit.'
            )
            return outcome
    return None


def not_outdated(found: Discovery) -> Outcome:
    latest = found.latest.version.raw if found.latest else None
    status: OutcomeStatus
    if found.status == "up-to-date":
        status, message = "up-to-date", "up to date"
    elif found.status == "ahead":
        status, message = "ahead", "current version is newer than any acceptable release"
    else:
        status, message = "no-releases", "no acceptable releases found (check tag, prereleases, ignore and allow)"
    if found.held_back:
        message = f"{message}; held back by ignore/allow: {found.held_back_summary()}"
    return Outcome(found.tool.name, status, message, found.tool.version, latest)


def load_failures(loaded: LoadResult) -> list[Outcome]:
    return [Outcome(name, "failed", error.message) for name, error in loaded.errors.items()]


def preview_assets(ctx: Context, tool: ToolDefinition, release: Release, version: str) -> list[str]:
    """What would be downloaded and how it would be verified, without downloading.

    Fails the same way the real update would if an asset or checksum source is
    missing.
    """
    provider = ctx.providers.require(tool.source.provider)
    values = {"version": version, "tag": release.tag}
    release_label = f"{tool.source.provider}:{tool.source.repository}@{release.tag}"
    lines: list[str] = []
    for asset in resolve_assets(tool.assets, release.assets, values, release_label):
        # Release assets are known to exist from the listing; URLs and repository files are probed.
        if (asset.by_url or asset.from_repository) and not ctx.http.exists(
            asset_download_request(ctx, provider, tool, release, asset),
            f'asset "{asset.config.id}"',
            provider.auth_hint(tool.source),
        ):
            raise ToolbotError(
                "ASSET_NOT_FOUND", f'asset "{asset.config.id}": {asset.upstream.url} does not exist at {release.tag}'
            )
        checksum = asset.config.checksum
        if checksum.type == "file":
            source = checksum_source(ctx, provider, tool, release, asset, values)
            if (checksum.url or checksum.path) and not ctx.http.exists(
                source.request, "checksum file", provider.auth_hint(tool.source)
            ):
                raise ToolbotError("CHECKSUM_UNAVAILABLE", f"checksum file {source.label} does not exist")
            verification = f"{checksum.algorithm} via {source.label}"
        elif checksum.type == "provider":
            if asset.upstream.digest is None:
                raise ToolbotError(
                    "CHECKSUM_UNAVAILABLE", f'{tool.source.provider} reported no digest for "{asset.upstream.name}"'
                )
            verification = "provider digest"
        else:
            verification = "not verified"
        extracted = f" (extract {asset.extract})" if asset.extract else ""
        lines.append(f"✓ {asset.upstream.name}{extracted} → {asset.config.destination} [{verification}]")
    # Version pins are checked against the definitions read (base branch or local checkout).
    lines += [
        f"✓ {change.display}: {change.replacements} version pin(s)" for change in bump_version_files(tool, version)
    ]
    return lines


def plan_local(ctx: Context, root: Path, only: Sequence[str] | None) -> list[Outcome]:
    """``toolbot check --dry-run`` without a target: from the local checkout, read-only.

    Reads definitions from ``root``, applies central rules, discovers
    releases and checks every asset and checksum source exists. Never
    downloads, writes, runs git or contacts a target.
    """
    loaded = load_all(root, ctx.providers, list(only) if only else None)
    outcomes = load_failures(loaded)
    for definition in loaded.tools:
        try:
            tool = apply_rules(definition, ctx.control.rules, "")
            if tool is None:
                outcomes.append(Outcome(definition.name, "disabled", "disabled by a rule in toolbot.yaml"))
                continue
            found = discover(ctx, tool)
            if found.status != "update-available" or found.latest is None:
                outcomes.append(not_outdated(found))
                continue
            version = found.latest.version.raw
            outcome = Outcome(
                tool.name,
                "would-create",
                current=tool.version,
                latest=version,
                url=found.latest.release.url,
                branch=messages.branch_name(tool.name, version),
            )
            outcome.details.extend(preview_assets(ctx, tool, found.latest.release, version))
            outcome.details.append("(no target configured: existing change requests not checked)")
            outcomes.append(outcome)
        except ToolbotError as error:
            outcomes.append(Outcome(definition.name, "failed", error.message, current=definition.version))
    return outcomes
