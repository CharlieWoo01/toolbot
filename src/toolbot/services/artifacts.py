"""Applying a version of a tool to a working tree.

Resolves the release's assets, downloads every one into a private temp
directory, verifies checksums, and only then replaces the committed
artifacts, the lock file and (if the version changed) ``tool.yaml`` in one
rollback-safe step. Any failure before that leaves the tree untouched; temp
files are always removed.

No Git and no target platform here: this is what ``toolbot update`` runs
locally and what ``toolbot check`` runs inside its temporary worktree.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from toolbot.assets.checksums import assert_digest, find_expected_digest, hash_file
from toolbot.assets.install import PendingFile, replace_files
from toolbot.assets.matching import ResolvedAsset, resolve_assets
from toolbot.config.loader import load_tool, with_version
from toolbot.config.models import ToolDefinition
from toolbot.config.placeholders import render
from toolbot.errors import ToolbotError
from toolbot.lock import LockedAsset, LockFile, describe_verification, lock_path, read_lock
from toolbot.paths import repo_relative, resolve_destination
from toolbot.providers.source.base import Release, ReleaseAsset, SourceProvider
from toolbot.services.context import Context
from toolbot.versions import parse_version, tag_for


@dataclass(frozen=True)
class AppliedAsset:
    id: str
    upstream: str
    destination: str
    size: int
    sha256: str
    verified_by: str


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


def apply_version(
    ctx: Context, root: Path, name: str, version: str | None = None, release: Release | None = None
) -> ApplyResult:
    tool = load_tool(root, name, ctx.providers)
    target = version or tool.version
    parse_version(target, tool.versioning)  # validates the requested version
    provider = ctx.providers.require(tool.source.provider)
    tag = tag_for(target, tool.tag)
    label = f"{provider.id}:{tool.source.repository}@{tag}"
    ctx.log(f"toolbot: {tool.name} {tool.version} -> {target} ({label})")

    if release is None:
        release = provider.get_release(tool.source, tag)
    resolved = resolve_assets(tool.assets, release.assets, {"version": target, "tag": tag}, label)
    # Resolve and safety-check every destination before downloading anything.
    destinations = {r.config.id: resolve_destination(tool.dir, r.config.destination) for r in resolved}

    temp_dir = Path(tempfile.mkdtemp(prefix="toolbot-", dir=ctx.temp_root))
    try:
        checksum_files: dict[str, str] = {}
        downloaded: list[tuple[Path, Path, AppliedAsset]] = []
        for index, asset in enumerate(resolved):
            temp_file = temp_dir / f"asset-{index}"
            what = f'asset "{asset.config.id}" ({asset.upstream.name}) of {label}'
            size = ctx.http.download(
                provider.download_request(tool.source, asset.upstream),
                temp_file,
                what,
                ctx.max_download_bytes,
                provider.auth_hint(),
            )
            verified_by = _verify(ctx, tool, provider, release, asset, temp_file, target, tag, checksum_files)
            destination = destinations[asset.config.id]
            applied = AppliedAsset(
                id=asset.config.id,
                upstream=asset.upstream.name,
                destination=repo_relative(root, destination),
                size=size,
                sha256=hash_file(temp_file, "sha256"),
                verified_by=verified_by,
            )
            downloaded.append((temp_file, destination, applied))
            ctx.log(f"  {applied.id}: {applied.upstream} ({size} bytes, verified: {verified_by})")

        try:
            previous_lock = read_lock(tool)
        except ToolbotError:
            previous_lock = None
        lock = LockFile(
            tool=tool.name,
            version=target,
            tag=tag,
            source={"provider": tool.source.provider, "repository": tool.source.repository, "host": tool.source.host},
            assets={
                a.id: LockedAsset(a.upstream, a.destination, a.size, a.sha256, a.verified_by) for _, _, a in downloaded
            },
        )
        pending = [PendingFile(destination=dest, source_file=temp) for temp, dest, _ in downloaded]
        pending.append(PendingFile(destination=lock_path(tool), content=lock.serialise()))
        if target != tool.version:
            text = tool.file.read_text(encoding="utf-8")
            pending.append(PendingFile(destination=tool.file, content=with_version(text, str(tool.file), target)))
        replace_files(pending)

        changed: list[str] = []
        if target != tool.version:
            changed.append(repo_relative(root, tool.file))
        if previous_lock is None or previous_lock.serialise() != lock.serialise():
            changed.append(repo_relative(root, lock_path(tool)))
        for _, _, applied in downloaded:
            before = previous_lock.assets.get(applied.id) if previous_lock else None
            if before is None or before.sha256 != applied.sha256:
                changed.append(applied.destination)
        ctx.log(f"toolbot: updated {', '.join(changed)}" if changed else "toolbot: already up to date")
        return ApplyResult(tool, tool.version, target, tag, release, [a for _, _, a in downloaded], changed)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _verify(
    ctx: Context,
    tool: ToolDefinition,
    provider: SourceProvider,
    release: Release,
    asset: ResolvedAsset,
    file: Path,
    version: str,
    tag: str,
    checksum_files: dict[str, str],
) -> str:
    checksum = asset.config.checksum
    what = f'"{asset.upstream.name}"'
    if checksum.type == "none":
        ctx.warn(f"{tool.name}/{asset.config.id}: no checksum verification configured")
        return describe_verification(checksum)
    if checksum.type == "provider":
        digest = asset.upstream.digest
        if digest is None or digest.algorithm not in ("sha256", "sha512"):
            raise ToolbotError(
                "CHECKSUM_UNAVAILABLE",
                f'{provider.id} reported no usable digest for {what}; use checksum type "file" or "none"',
            )
        assert_digest(hash_file(file, digest.algorithm), digest.hex, what, f"{provider.id} API digest")
        return describe_verification(checksum)
    checksum_name = render(checksum.file, {"version": version, "tag": tag, "asset": asset.upstream.name})
    checksum_asset = next((a for a in release.assets if a.name == checksum_name), None)
    if checksum_asset is None:
        raise ToolbotError(
            "CHECKSUM_UNAVAILABLE", f'checksum file "{checksum_name}" for {what} is not attached to release {tag}'
        )
    content = _checksum_content(ctx, tool, provider, checksum_asset, checksum_files)
    expected = find_expected_digest(content, asset.upstream.name, checksum.algorithm, checksum_name)
    assert_digest(hash_file(file, checksum.algorithm), expected, what, checksum_name)
    return describe_verification(checksum, checksum_name)


def _checksum_content(
    ctx: Context, tool: ToolDefinition, provider: SourceProvider, asset: ReleaseAsset, cache: dict[str, str]
) -> str:
    if asset.name not in cache:
        cache[asset.name] = ctx.http.get_text(
            provider.download_request(tool.source, asset), f'checksum file "{asset.name}"', provider.auth_hint()
        )
    return cache[asset.name]
