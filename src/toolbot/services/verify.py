"""Offline consistency check: definition ⇄ lock file ⇄ committed artifacts ⇄ version pins.

Runs on every merge request. It proves the committed binaries are exactly the
ones recorded for the declared version, catching hand edits, corrupted
files, or a version bump without matching artifacts.
"""

from __future__ import annotations

from pathlib import Path

from toolbot.assets.checksums import hash_file
from toolbot.config.models import ToolDefinition
from toolbot.errors import ToolbotError
from toolbot.lock import read_lock
from toolbot.paths import destination_path, repo_relative
from toolbot.storage import ATTRIBUTES_FILE, attributes_line, is_lfs_pointer
from toolbot.version_files import check_version_files
from toolbot.versions import tag_for


def verify_tool(root: Path, tool: ToolDefinition) -> list[str]:
    hint = f'run "toolbot update {tool.name}"'
    try:
        lock = read_lock(tool)
    except ToolbotError as error:
        return [f"{tool.name}: {error.message}"]
    if lock is None:
        return [f"{tool.name}: no lock file; artifacts have never been fetched ({hint})"]

    problems: list[str] = []
    if lock.version != tool.version:
        problems.append(
            f"definition declares version {tool.version} but committed artifacts are for {lock.version} ({hint})"
        )
    elif lock.tag != tag_for(tool.version, tool.tag):
        problems.append(
            f"lock was produced for tag {lock.tag} but the definition now resolves to "
            f"{tag_for(tool.version, tool.tag)} ({hint})"
        )
    source = {"provider": tool.source.provider, "repository": tool.source.repository, "host": tool.source.host}
    if lock.source != source:
        problems.append(f"source changed since the artifacts were fetched ({hint})")

    configured = {asset.id for asset in tool.assets}
    problems.extend(
        f'lock has asset "{asset_id}" that is no longer configured ({hint})'
        for asset_id in lock.assets
        if asset_id not in configured
    )
    for asset in tool.assets:
        locked = lock.assets.get(asset.id)
        if locked is None:
            problems.append(f'asset "{asset.id}" has not been fetched ({hint})')
            continue
        expected_destination = repo_relative(root, destination_path(tool.dir, asset.destination))
        if locked.destination != expected_destination:
            problems.append(f'asset "{asset.id}" destination changed to {expected_destination} ({hint})')
            continue
        committed_file = root.joinpath(*locked.destination.split("/"))
        if locked.storage == "lfs" and not _tracked_by_lfs(committed_file):
            problems.append(f'asset "{asset.id}": {locked.destination} is not tracked by Git LFS ({hint})')
        if locked.storage == "fetch" and not committed_file.is_file():
            continue  # not committed; `toolbot fetch` restores and verifies it
        if not committed_file.is_file():
            problems.append(f'asset "{asset.id}": {locked.destination} is missing ({hint})')
        elif locked.storage == "lfs" and is_lfs_pointer(committed_file):
            problems.append(
                f'asset "{asset.id}": {locked.destination} is a Git LFS pointer; fetch LFS content first '
                '("git lfs pull", or actions/checkout with lfs: true)'
            )
        elif hash_file(committed_file, "sha256") != locked.sha256:
            problems.append(f'asset "{asset.id}": {locked.destination} does not match the lock file sha256 ({hint})')
    problems.extend(check_version_files(tool))
    return [f"{tool.name}: {problem}" for problem in problems]


def _tracked_by_lfs(file: Path) -> bool:
    """The ``.gitattributes`` line Toolbot writes next to an LFS-stored file is present."""
    attributes = file.parent / ATTRIBUTES_FILE
    try:
        lines = {line.strip() for line in attributes.read_text(encoding="utf-8").splitlines()}
    except OSError:
        return False
    return attributes_line(file) in lines
