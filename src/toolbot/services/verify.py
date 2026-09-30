"""Offline consistency check: definition ⇄ lock file ⇄ committed artifacts.

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
from toolbot.paths import repo_relative
from toolbot.versions import tag_for


def verify_tool(root: Path, tool: ToolDefinition) -> list[str]:
    hint = f'run "toolbot update {tool.name}"'
    try:
        lock = read_lock(tool)
    except ToolbotError as err:
        return [f"{tool.name}: {err.message}"]
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

    configured = {a.id for a in tool.assets}
    problems.extend(
        f'lock has asset "{i}" that is no longer configured ({hint})' for i in lock.assets if i not in configured
    )
    for asset in tool.assets:
        locked = lock.assets.get(asset.id)
        if locked is None:
            problems.append(f'asset "{asset.id}" has not been fetched ({hint})')
            continue
        expected = repo_relative(root, tool.dir.joinpath(*asset.destination.split("/")))
        if locked.destination != expected:
            problems.append(f'asset "{asset.id}" destination changed to {expected} ({hint})')
            continue
        file = root.joinpath(*locked.destination.split("/"))
        if not file.is_file():
            problems.append(f'asset "{asset.id}": {locked.destination} is missing ({hint})')
        elif hash_file(file, "sha256") != locked.sha256:
            problems.append(f'asset "{asset.id}": {locked.destination} does not match the lock file sha256 ({hint})')
    return [f"{tool.name}: {p}" for p in problems]
