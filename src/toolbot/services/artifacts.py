"""Applying a version of a tool to a working tree.

Resolves the release's assets, downloads every one into a private temp
directory, verifies checksums (of the archive, for ``extract``), extracts
archive members, and only then replaces the committed artifacts, version
pins in ``version-files``, the lock file and (if the version changed)
``tool.yaml`` in one rollback-safe step. Any failure before that leaves the
tree untouched; temp files are always removed.

No Git and no target platform here: this is what ``toolbot update`` runs
locally and what ``toolbot check`` runs inside its temporary worktree.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from toolbot.assets.archives import extract_member
from toolbot.assets.checksums import assert_digest, find_expected_digest, hash_file
from toolbot.assets.install import PendingFile, replace_files
from toolbot.assets.matching import ResolvedAsset, file_name_from_url, resolve_assets
from toolbot.config.loader import load_tool, with_version
from toolbot.config.models import ToolDefinition
from toolbot.config.placeholders import render
from toolbot.errors import ToolbotError
from toolbot.http import HttpRequest
from toolbot.lock import LockedAsset, LockFile, describe_verification, lock_path, read_lock
from toolbot.paths import TOOLS_DIR, repo_relative, resolve_destination
from toolbot.providers.source.base import Release, SourceProvider
from toolbot.services.context import Context
from toolbot.version_files import bump_version_files
from toolbot.versions import parse_version, tag_for

GIT_LFS_POINTER = b"version https://git-lfs.github.com/spec/"


@dataclass(frozen=True)
class AppliedAsset:
    id: str
    upstream: str
    """Release asset name, URL or source-repository path (``!member`` for archives)."""
    destination: str
    size: int
    sha256: str
    verified_by: str
    executable: bool = False


@dataclass
class ApplyResult:
    tool: ToolDefinition
    previous_version: str
    version: str
    tag: str
    release: Release
    assets: list[AppliedAsset]
    changed: list[str] = field(default_factory=list)
    """Repository-relative files whose content changed."""
    version_files: list[str] = field(default_factory=list)
    """Repository-relative version files that were rewritten."""

    @property
    def paths(self) -> list[str]:
        """Everything this update may touch, repository-relative (what gets committed)."""
        touched = [f"{TOOLS_DIR}/{self.tool.name}"]
        touched += [asset.destination for asset in self.assets if not asset.destination.startswith(touched[0] + "/")]
        return touched + self.version_files

    @property
    def executable_paths(self) -> list[str]:
        return [asset.destination for asset in self.assets if asset.executable]


@dataclass(frozen=True)
class ChecksumSource:
    label: str
    """What the MR and lock file show, e.g. ``checksums.txt`` or a URL."""
    request: HttpRequest


def asset_download_request(
    ctx: Context, provider: SourceProvider, tool: ToolDefinition, release: Release, asset: ResolvedAsset
) -> HttpRequest:
    if asset.by_url:
        return HttpRequest(asset.upstream.url, ctx.providers.hosts.auth_headers(asset.upstream.url))
    if asset.from_repository:
        return provider.file_request(tool.source, asset.upstream.url, release.tag)
    return provider.download_request(tool.source, asset.upstream)


def checksum_source(
    ctx: Context,
    provider: SourceProvider,
    tool: ToolDefinition,
    release: Release,
    asset: ResolvedAsset,
    values: dict[str, str],
) -> ChecksumSource:
    """Where the checksum file for ``asset`` comes from (type ``file`` only)."""
    checksum = asset.config.checksum
    placeholders = {**values, "asset": asset.upstream.name}
    if checksum.url:
        url = render(checksum.url, placeholders)
        return ChecksumSource(url, HttpRequest(url, ctx.providers.hosts.auth_headers(url)))
    if checksum.path:
        path = render(checksum.path, placeholders).removeprefix("./")
        return ChecksumSource(path, provider.file_request(tool.source, path, release.tag))
    checksum_name = render(checksum.file, placeholders)
    checksum_asset = next((candidate for candidate in release.assets if candidate.name == checksum_name), None)
    if checksum_asset is None:
        raise ToolbotError(
            "CHECKSUM_UNAVAILABLE",
            f'checksum file "{checksum_name}" for "{asset.upstream.name}" is not attached to release {values["tag"]}',
        )
    return ChecksumSource(checksum_name, provider.download_request(tool.source, checksum_asset))


def apply_version(
    ctx: Context, root: Path, name: str, version: str | None = None, release: Release | None = None
) -> ApplyResult:
    tool = load_tool(root, name, ctx.providers)
    target_version = version or tool.version
    parse_version(target_version, tool.versioning)  # validates the requested version
    provider = ctx.providers.require(tool.source.provider)
    tag = tag_for(target_version, tool.tag)
    release_label = f"{provider.id}:{tool.source.repository}@{tag}"
    ctx.log(f"toolbot: {tool.name} {tool.version} -> {target_version} ({release_label})")

    if release is None:
        release = provider.get_release_or_tag(tool.source, tag)
    values = {"version": target_version, "tag": tag}
    resolved_assets = resolve_assets(tool.assets, release.assets, values, release_label)
    # Resolve and safety-check every destination and version file before downloading anything.
    destinations = {
        asset.config.id: resolve_destination(tool.dir, asset.config.destination) for asset in resolved_assets
    }
    version_file_changes = bump_version_files(tool, target_version)

    temp_dir = Path(tempfile.mkdtemp(prefix="toolbot-", dir=ctx.temp_root))
    try:
        checksum_cache: dict[str, str] = {}
        download_cache: dict[str, Path] = {}  # several assets may extract from one archive
        downloaded: list[tuple[Path, Path, AppliedAsset]] = []
        for index, asset in enumerate(resolved_assets):
            request = asset_download_request(ctx, provider, tool, release, asset)
            what = f'asset "{asset.config.id}" ({asset.upstream.name}) of {release_label}'
            download = download_cache.get(request.url)
            if download is None:
                download = temp_dir / f"download-{index}"
                ctx.http.download(request, download, what, ctx.max_download_bytes, provider.auth_hint(tool.source))
                if asset.from_repository:
                    _reject_lfs_pointer(download, what)
                download_cache[request.url] = download
            verified_by = _verify(ctx, tool, provider, release, asset, download, values, checksum_cache)
            committed_file = download
            if asset.extract:
                committed_file = temp_dir / f"extracted-{index}"
                extract_member(download, asset.extract, committed_file, ctx.max_download_bytes, what)
            destination = destinations[asset.config.id]
            applied = AppliedAsset(
                id=asset.config.id,
                upstream=asset.upstream_label,
                destination=repo_relative(root, destination),
                size=committed_file.stat().st_size,
                sha256=hash_file(committed_file, "sha256"),
                verified_by=verified_by,
                executable=asset.config.executable,
            )
            downloaded.append((committed_file, destination, applied))
            ctx.log(f"  {applied.id}: {applied.upstream} ({applied.size} bytes, verified: {verified_by})")

        try:
            previous_lock = read_lock(tool)
        except ToolbotError:
            previous_lock = None
        lock = LockFile(
            tool=tool.name,
            version=target_version,
            tag=tag,
            source={"provider": tool.source.provider, "repository": tool.source.repository, "host": tool.source.host},
            assets={
                applied.id: LockedAsset(
                    applied.upstream, applied.destination, applied.size, applied.sha256, applied.verified_by
                )
                for _temp, _destination, applied in downloaded
            },
        )
        pending_files = [
            PendingFile(destination=destination, source_file=temp_file, executable=applied.executable)
            for temp_file, destination, applied in downloaded
        ]
        pending_files += [
            PendingFile(destination=change.path, content=change.content) for change in version_file_changes
        ]
        pending_files.append(PendingFile(destination=lock_path(tool), content=lock.serialise()))
        if target_version != tool.version:
            definition_text = tool.file.read_text(encoding="utf-8")
            pending_files.append(
                PendingFile(
                    destination=tool.file, content=with_version(definition_text, str(tool.file), target_version)
                )
            )
        replace_files(pending_files)

        changed: list[str] = []
        if target_version != tool.version:
            changed.append(repo_relative(root, tool.file))
        if previous_lock is None or previous_lock.serialise() != lock.serialise():
            changed.append(repo_relative(root, lock_path(tool)))
        for _temp, _destination, applied in downloaded:
            previous_asset = previous_lock.assets.get(applied.id) if previous_lock else None
            if previous_asset is None or previous_asset.sha256 != applied.sha256:
                changed.append(applied.destination)
        changed += [change.display for change in version_file_changes]
        ctx.log(f"toolbot: updated {', '.join(changed)}" if changed else "toolbot: already up to date")
        applied_assets = [applied for _temp, _destination, applied in downloaded]
        return ApplyResult(
            tool,
            tool.version,
            target_version,
            tag,
            release,
            applied_assets,
            changed,
            [change.display for change in version_file_changes],
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _reject_lfs_pointer(downloaded_file: Path, what: str) -> None:
    """A Git LFS pointer is a small text file; committing it instead of the binary must not happen."""
    with downloaded_file.open("rb") as handle:
        if handle.read(len(GIT_LFS_POINTER)) == GIT_LFS_POINTER:
            raise ToolbotError(
                "ASSET_NOT_FOUND",
                f"{what} is a Git LFS pointer, not the file. GitHub's contents API does not serve LFS content; "
                'download it by "url" (e.g. a media.githubusercontent.com URL) or publish it as a release asset.',
            )


def _verify(
    ctx: Context,
    tool: ToolDefinition,
    provider: SourceProvider,
    release: Release,
    asset: ResolvedAsset,
    downloaded_file: Path,
    values: dict[str, str],
    checksum_cache: dict[str, str],
) -> str:
    checksum = asset.config.checksum
    description = f'"{asset.upstream.name}"'
    if checksum.type == "none":
        ctx.warn(f"{tool.name}/{asset.config.id}: no checksum verification configured")
        return describe_verification(checksum)
    if checksum.type == "provider":
        digest = asset.upstream.digest
        if digest is None or digest.algorithm not in ("sha256", "sha512"):
            raise ToolbotError(
                "CHECKSUM_UNAVAILABLE",
                f'{provider.id} reported no usable digest for {description}; use checksum type "file" or "none"',
            )
        assert_digest(hash_file(downloaded_file, digest.algorithm), digest.hex, description, f"{provider.id} digest")
        return describe_verification(checksum)
    source = checksum_source(ctx, provider, tool, release, asset, values)
    if source.label not in checksum_cache:
        checksum_cache[source.label] = ctx.http.get_text(
            source.request, f"checksum file {source.label}", provider.auth_hint(tool.source)
        )
    expected = find_expected_digest(
        checksum_cache[source.label], asset.upstream.name, checksum.algorithm, file_name_from_url(source.label)
    )
    assert_digest(hash_file(downloaded_file, checksum.algorithm), expected, description, source.label)
    return describe_verification(checksum, source.label)
