"""Repository layout and destination-path safety."""

from __future__ import annotations

import os
import re
from pathlib import Path

from toolbot.errors import ToolbotError

TOOLS_DIR = "tools"
DEFINITION_FILE = "tool.yaml"
LOCK_FILE = "tool.lock.json"

TOOL_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
# One destination path segment: no dot-files, no traversal, no separators.
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def check_destination(destination: str) -> list[str]:
    """Lexical validation of a destination from a tool definition.

    Destinations are relative to the tool's directory (``tools/<name>/``),
    use ``/``, and may only contain conservative characters. This rejects
    absolute paths, ``..``, drive letters, backslashes, hidden files and the
    definition/lock files before anything touches the filesystem.
    """
    if not destination:
        return ["must not be empty"]
    problems: list[str] = []
    if destination.startswith("/") or re.match(r"^[A-Za-z]:", destination):
        problems.append("must be relative to the tool directory, not absolute")
    if "\\" in destination:
        problems.append('must use "/" as the separator')
    segments = destination.split("/")
    if any(s in (".", "..") for s in segments):
        problems.append('must not contain "." or ".." segments (it must stay inside the tool directory)')
    elif not all(_SEGMENT.match(s) for s in segments):
        problems.append(
            'each path segment must start with a letter or digit and contain only letters, digits, ".", "_" or "-"'
        )
    if len(segments) == 1 and destination in (DEFINITION_FILE, LOCK_FILE):
        problems.append(f"must not overwrite {destination}")
    if re.search(r"\.toolbot-(tmp|backup)", destination):
        problems.append("uses a name reserved for Toolbot temporary files")
    return problems


def tool_directory(root: Path, name: str) -> Path:
    if not TOOL_NAME.match(name):
        raise ToolbotError("USAGE", f'invalid tool name "{name}"')
    return root / TOOLS_DIR / name


def repo_relative(root: Path, path: Path) -> str:
    """Repository-relative, forward-slash path for display and lock files."""
    return path.relative_to(root).as_posix()


def resolve_destination(tool_dir: Path, destination: str) -> Path:
    """Resolve a destination and prove, on the real filesystem, that it stays
    inside the tool directory.

    The lexical check cannot see symlinks: a committed ``tools/x/bin -> /etc``
    would otherwise redirect writes. The deepest existing ancestor is checked
    *before* creating directories, so ``mkdir`` can never follow a symlink out.
    """
    problems = check_destination(destination)
    if problems:
        raise ToolbotError("UNSAFE_PATH", f'destination "{destination}" ' + "; ".join(problems))
    target = tool_dir.joinpath(*destination.split("/"))
    parent = target.parent
    real_tool = tool_dir.resolve(strict=True)

    def assert_inside(directory: Path) -> None:
        real = directory.resolve(strict=True)
        if real != real_tool and not str(real).startswith(str(real_tool) + os.sep):
            raise ToolbotError(
                "UNSAFE_PATH", f'destination "{destination}" resolves outside {tool_dir} (via a symlink?)'
            )

    existing = parent
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    assert_inside(existing)
    parent.mkdir(parents=True, exist_ok=True)
    assert_inside(parent)
    return target
