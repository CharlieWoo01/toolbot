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
_HIDDEN_SEGMENT = re.compile(r"^\.?[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def is_repository_relative(path: str) -> bool:
    """A leading ``/`` means "from the repository root" (like ``.gitignore``)."""
    return path.startswith("/")


def check_destination(destination: str, allow_hidden: bool = False) -> list[str]:
    """Lexical validation of a path from a tool definition (asset destinations,
    version files).

    Paths are relative to the tool's directory (``tools/<name>/``), or to the
    repository root when they start with ``/`` (for binaries that already live
    elsewhere, e.g. ``/bin/terraform``). They use ``/`` and conservative
    characters only. This rejects ``..``, empty segments, drive letters,
    backslashes, ``.git``, other tools' directories and the definition/lock
    files before anything touches the filesystem. Hidden files are refused for
    downloaded assets; ``allow_hidden`` permits them for version files
    (``.terraform-version``, ``.gitlab-ci.yml``).
    """
    relative = destination[1:] if is_repository_relative(destination) else destination
    if not relative:
        return ["must not be empty"]
    problems: list[str] = []
    if relative.startswith("/") or re.match(r"^[A-Za-z]:", relative):
        problems.append('must be relative to the tool directory, or start with a single "/" for the repository root')
    if "\\" in relative:
        problems.append('must use "/" as the separator')
    segments = relative.split("/")
    segment_pattern = _HIDDEN_SEGMENT if allow_hidden else _SEGMENT
    if any(segment in (".", "..") for segment in segments):
        problems.append('must not contain "." or ".." segments')
    elif any(segment.lower() == ".git" for segment in segments):
        problems.append("must not point into .git")
    elif not all(segment_pattern.match(segment) for segment in segments):
        start = 'an optional "." and a letter or digit' if allow_hidden else "a letter or digit"
        problems.append(f'each path segment must start with {start} and contain only letters, digits, ".", "_" or "-"')
    if is_repository_relative(destination) and segments[0] == TOOLS_DIR:
        problems.append(
            f'must not point into {TOOLS_DIR}/ from the repository root; use a path without the leading "/"'
        )
    if not is_repository_relative(destination) and len(segments) == 1 and relative in (DEFINITION_FILE, LOCK_FILE):
        problems.append(f"must not overwrite {relative}")
    if re.search(r"\.toolbot-(tmp|backup)", relative):
        problems.append("uses a name reserved for Toolbot temporary files")
    return problems


def destination_path(tool_dir: Path, destination: str) -> Path:
    """Where a validated destination points (lexically; see ``resolve_destination``)."""
    base, relative = _base_and_relative(tool_dir, destination)
    return base.joinpath(*relative.split("/"))


def _base_and_relative(tool_dir: Path, destination: str) -> tuple[Path, str]:
    if is_repository_relative(destination):
        return tool_dir.parent.parent, destination[1:]
    return tool_dir, destination


def tool_directory(root: Path, name: str) -> Path:
    if not TOOL_NAME.match(name):
        raise ToolbotError("USAGE", f'invalid tool name "{name}"')
    return root / TOOLS_DIR / name


def repo_relative(root: Path, path: Path) -> str:
    """Repository-relative, forward-slash path for display and lock files."""
    return path.relative_to(root).as_posix()


def resolve_destination(
    tool_dir: Path, destination: str, create_parents: bool = True, allow_hidden: bool = False
) -> Path:
    """Resolve a destination and prove, on the real filesystem, that it stays
    inside its base (the tool directory, or the repository root for ``/…``).

    The lexical check cannot see symlinks: a committed ``tools/x/bin -> /etc``
    would otherwise redirect writes. The deepest existing ancestor is checked
    *before* creating directories, so ``mkdir`` can never follow a symlink out.
    A destination that is itself a symlink is refused too.
    """
    problems = check_destination(destination, allow_hidden)
    if problems:
        raise ToolbotError("UNSAFE_PATH", f'"{destination}" ' + "; ".join(problems))
    base, _relative = _base_and_relative(tool_dir, destination)
    target = destination_path(tool_dir, destination)
    parent = target.parent
    real_base = base.resolve(strict=True)

    def assert_inside(directory: Path) -> None:
        real = directory.resolve(strict=True)
        if real != real_base and not str(real).startswith(str(real_base) + os.sep):
            raise ToolbotError("UNSAFE_PATH", f'"{destination}" resolves outside {base} (via a symlink?)')

    existing = parent
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    assert_inside(existing)
    if target.is_symlink():
        raise ToolbotError("UNSAFE_PATH", f'"{destination}" is a symlink; Toolbot only replaces regular files')
    if create_parents:
        parent.mkdir(parents=True, exist_ok=True)
        assert_inside(parent)
    return target
