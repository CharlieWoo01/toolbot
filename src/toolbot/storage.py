"""How large files are stored: plain git, Git LFS, or not committed at all.

GitHub rejects files over 100 MB and every platform limits pushes, so a tool
like Terraform (~120 MB) can't simply be committed. ``storage: auto`` (the
default) commits files up to ``defaults.max-file-mb`` normally and handles
bigger ones with ``defaults.large-files``:

``lfs``
    Git LFS. Toolbot adds ``/<file> filter=lfs …`` to the ``.gitattributes``
    next to the file, commits the LFS pointer and pushes the object itself
    (the object id is the file's SHA-256, already recorded in the lock file).
    Checkouts with git-lfs get the real file, as usual.
``fetch``
    Not committed. ``/<file>`` goes into the ``.gitignore`` next to it, the
    lock file pins its SHA-256 and ``toolbot fetch`` downloads, verifies and
    restores it, e.g. as the first step of a CI job.

The marker lines live in the file's own directory, so tools in different
directories never edit the same ``.gitattributes``/``.gitignore``. Only lines
in exactly Toolbot's format are ever added or removed; everything else in
those files is preserved.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from toolbot.config.control import Defaults
from toolbot.config.models import StorageMode, StoredAs

LFS_ATTRIBUTES = "filter=lfs diff=lfs merge=lfs -text"
GIT_LFS_POINTER = b"version https://git-lfs.github.com/spec/"
ATTRIBUTES_FILE = ".gitattributes"
IGNORE_FILE = ".gitignore"


def choose_storage(configured: StorageMode, size: int, defaults: Defaults) -> StoredAs:
    if configured != "auto":
        return configured
    return "git" if size <= defaults.max_file_mb * 1024 * 1024 else defaults.large_files


def is_lfs_pointer(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(len(GIT_LFS_POINTER)) == GIT_LFS_POINTER
    except OSError:
        return False


def attributes_line(file: Path) -> str:
    return f"/{file.name} {LFS_ATTRIBUTES}"


def ignore_line(file: Path) -> str:
    return f"/{file.name}"


@dataclass(frozen=True)
class MarkerFile:
    path: Path
    content: str | None
    """New content, or None to delete a file that only held Toolbot's lines."""


def marker_updates(previous: Mapping[Path, StoredAs], current: Mapping[Path, StoredAs]) -> list[MarkerFile]:
    """``.gitattributes``/``.gitignore`` edits so markers match ``current``.

    ``previous`` is what the old lock file recorded for this tool; its
    markers are removed when a file changes storage or is no longer
    configured.
    """
    wanted: dict[Path, set[str]] = {}
    owned: dict[Path, set[str]] = {}
    for file in [*previous, *current]:
        for marker_name, line in ((ATTRIBUTES_FILE, attributes_line(file)), (IGNORE_FILE, ignore_line(file))):
            owned.setdefault(file.parent / marker_name, set()).add(line)
    for file, storage in current.items():
        if storage == "lfs":
            wanted.setdefault(file.parent / ATTRIBUTES_FILE, set()).add(attributes_line(file))
        elif storage == "fetch":
            wanted.setdefault(file.parent / IGNORE_FILE, set()).add(ignore_line(file))

    updates: list[MarkerFile] = []
    for marker, our_lines in sorted(owned.items()):
        existing = marker.read_text(encoding="utf-8") if marker.is_file() else None
        lines = existing.splitlines() if existing else []
        keep = [line for line in lines if line.strip() not in our_lines or line.strip() in wanted.get(marker, set())]
        present = {line.strip() for line in keep}
        keep += sorted(line for line in wanted.get(marker, set()) if line not in present)
        if keep == lines:
            continue  # unchanged (line endings of the existing file are left alone)
        if existing is not None and not any(line.strip() for line in keep):
            updates.append(MarkerFile(marker, None))
        elif keep:
            updates.append(MarkerFile(marker, "\n".join(keep) + "\n"))
    return updates
