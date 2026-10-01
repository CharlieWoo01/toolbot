"""``toolbot fetch``: restore files stored with ``storage: fetch``.

Such files are not committed (they are too large); the lock file pins them.
Fetching downloads them exactly the way the update did (same release,
checksum verification, archive extraction) and then requires the result to
have the SHA-256 recorded in ``tool.lock.json``, so a fetched file is
byte-for-byte the one that was reviewed in the merge request. Files already
present with the right hash are left alone, so it's cheap to run in every job.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from toolbot.assets.checksums import assert_digest, hash_file
from toolbot.assets.install import PendingFile, replace_files
from toolbot.assets.matching import resolve_assets
from toolbot.config.models import ToolDefinition
from toolbot.errors import ToolbotError
from toolbot.lock import read_lock
from toolbot.paths import repo_relative, resolve_destination
from toolbot.services.artifacts import AssetDownloader
from toolbot.services.context import Context


def fetch_tool(ctx: Context, root: Path, tool: ToolDefinition) -> list[str]:
    """Restore missing or modified ``fetch`` files of ``tool``; returns their paths."""
    lock = read_lock(tool)
    if lock is None:
        raise ToolbotError("VERIFY_FAILED", f'{tool.name}: no lock file (run "toolbot update {tool.name}")')
    needed = {
        asset_id: locked
        for asset_id, locked in lock.assets.items()
        if locked.storage == "fetch" and not _present(root.joinpath(*locked.destination.split("/")), locked.sha256)
    }
    if not needed:
        return []
    if lock.version != tool.version:
        raise ToolbotError(
            "VERIFY_FAILED",
            f"{tool.name}: tool.yaml declares {tool.version} but tool.lock.json is for {lock.version}; "
            f'run "toolbot update {tool.name}"',
        )
    configured = [asset for asset in tool.assets if asset.id in needed]
    if len(configured) != len(needed):
        raise ToolbotError("VERIFY_FAILED", f'{tool.name}: lock file and tool.yaml disagree; run "toolbot update"')

    provider = ctx.providers.require(tool.source.provider)
    release = provider.get_release_or_tag(tool.source, lock.tag)
    values = {"version": lock.version, "tag": lock.tag}
    release_label = f"{provider.id}:{tool.source.repository}@{lock.tag}"
    temp_dir = Path(tempfile.mkdtemp(prefix="toolbot-fetch-", dir=ctx.temp_root))
    try:
        downloader = AssetDownloader(ctx, tool, provider, release, values, temp_dir)
        pending: list[PendingFile] = []
        for asset in resolve_assets(configured, release.assets, values, release_label):
            locked = needed[asset.config.id]
            destination = resolve_destination(tool.dir, asset.config.destination)
            if repo_relative(root, destination) != locked.destination:
                raise ToolbotError(
                    "VERIFY_FAILED", f'{tool.name}: asset "{asset.config.id}" destination changed; run "toolbot update"'
                )
            file, _verified_by = downloader.fetch(asset)
            assert_digest(hash_file(file, "sha256"), locked.sha256, f'"{locked.destination}"', "tool.lock.json")
            pending.append(PendingFile(destination=destination, source_file=file, executable=asset.config.executable))
            ctx.log(f"toolbot: fetched {locked.destination} ({locked.size} bytes, sha256 matches tool.lock.json)")
        replace_files(pending)
        return [locked.destination for locked in needed.values()]
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _present(path: Path, sha256: str) -> bool:
    return path.is_file() and hash_file(path, "sha256") == sha256
