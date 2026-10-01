"""Replacing a set of files as close to atomically as a filesystem allows."""

from __future__ import annotations

import os
import secrets
import shutil
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

EXECUTE_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH


@dataclass(frozen=True)
class PendingFile:
    destination: Path
    source_file: Path | None = None
    content: str | None = None
    executable: bool = False
    """Set the executable bits (POSIX; git records it via ``git add --chmod``)."""


def replace_files(files: Sequence[PendingFile], before_place: Callable[[Path], None] | None = None) -> None:
    """Replace every destination, or none of them.

    1. Each new file is copied next to its destination under a temporary name
       (same directory, so the final rename is atomic).
    2. Each existing destination is moved to a backup, then the staged file
       is renamed into place.
    3. On any failure, completed steps are rolled back in reverse order and
       staged files removed, leaving the previous files exactly as they were.
    4. Backups are deleted only after every file is in place.

    Callers pass only files that are already downloaded and verified.
    ``before_place`` is a test hook for injecting failures.
    """
    suffix = secrets.token_hex(6)
    staged: list[tuple[PendingFile, Path]] = []
    done: list[tuple[Path, Path | None, bool]] = []  # destination, backup, placed
    try:
        for file in files:
            staged_path = file.destination.with_name(f".{file.destination.name}.toolbot-tmp-{suffix}")
            staged.append((file, staged_path))
            if file.source_file is not None:
                shutil.copyfile(file.source_file, staged_path)
            else:
                with staged_path.open("x", encoding="utf-8", newline="\n") as handle:
                    handle.write(file.content or "")
            if file.executable:
                staged_path.chmod(staged_path.stat().st_mode | EXECUTE_BITS)
        for file, staged_path in staged:
            backup: Path | None = None
            if file.destination.exists():
                backup = file.destination.with_name(f".{file.destination.name}.toolbot-backup-{suffix}")
                os.replace(file.destination, backup)
            done.append((file.destination, backup, False))
            if before_place is not None:
                before_place(file.destination)
            os.replace(staged_path, file.destination)
            done[-1] = (file.destination, backup, True)
    except BaseException:
        for destination, backup, placed in reversed(done):
            try:
                if placed:
                    destination.unlink(missing_ok=True)
                if backup is not None:
                    os.replace(backup, destination)
            except OSError:
                pass  # best effort; a leftover backup keeps the original bytes recoverable
        for _, staged_path in staged:
            staged_path.unlink(missing_ok=True)
        raise
    for _, backup, _ in done:
        if backup is not None:
            backup.unlink(missing_ok=True)
