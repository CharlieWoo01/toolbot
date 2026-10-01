"""Reading one file out of a downloaded archive (``extract:`` on an asset).

Supports zip and tar (plain, gzip, bzip2, xz), detected from the content, not
the file name. Only the configured member is read, and it is streamed into a
file Toolbot names itself: paths inside the archive never become local paths,
so "zip slip" entries, absolute paths, symlinks and device files cannot write
anywhere. The member must be a regular file, and its size is capped like any
download to defeat decompression bombs.

The archive itself is what upstream checksums cover, so it is verified before
anything is extracted.
"""

from __future__ import annotations

import lzma
import stat
import tarfile
import zipfile
import zlib
from pathlib import Path
from typing import IO

from toolbot.errors import ToolbotError

LISTED_MEMBERS = 15


def extract_member(archive: Path, member: str, output: Path, max_bytes: int, label: str) -> int:
    """Write ``member`` of ``archive`` to ``output`` (must not exist). Returns its size."""
    wanted = _normalise(member)
    try:
        if zipfile.is_zipfile(archive):
            return _from_zip(archive, wanted, output, max_bytes, label)
        if tarfile.is_tarfile(archive):
            return _from_tar(archive, wanted, output, max_bytes, label)
    except (
        zipfile.BadZipFile,
        tarfile.TarError,
        EOFError,
        RuntimeError,
        ValueError,
        OSError,
        lzma.LZMAError,
        zlib.error,
    ) as error:
        # Corrupt or encrypted archives, bad compression streams.
        raise ToolbotError("ARCHIVE_INVALID", f"{label}: cannot read archive ({error})") from None
    raise ToolbotError(
        "ARCHIVE_INVALID",
        f'{label} is not a zip or tar archive, but "extract" is set; remove "extract" for plain files',
    )


def _normalise(name: str) -> str:
    return name.replace("\\", "/").removeprefix("./").lstrip("/")


def _from_zip(archive: Path, wanted: str, output: Path, max_bytes: int, label: str) -> int:
    with zipfile.ZipFile(archive) as zip_file:
        entries = {_normalise(info.filename): info for info in zip_file.infolist()}
        info = entries.get(wanted)
        if info is None:
            files = [name for name, entry in entries.items() if not entry.is_dir()]
            raise _member_not_found(wanted, files, label)
        # Unix type bits, when present (zips made on Windows often have none); symlinks are refused.
        file_type = stat.S_IFMT(info.external_attr >> 16)
        if info.is_dir() or file_type not in (0, stat.S_IFREG):
            raise ToolbotError("ARCHIVE_INVALID", f'{label}: "{wanted}" is not a regular file (directory or symlink?)')
        _check_size(info.file_size, max_bytes, wanted, label)
        with zip_file.open(info) as source:
            return _copy_capped(source, output, max_bytes, wanted, label)


def _from_tar(archive: Path, wanted: str, output: Path, max_bytes: int, label: str) -> int:
    with tarfile.open(archive, "r:*") as tar_file:
        members = {_normalise(entry.name): entry for entry in tar_file.getmembers()}
        entry = members.get(wanted)
        if entry is None:
            files = [name for name, candidate in members.items() if candidate.isfile()]
            raise _member_not_found(wanted, files, label)
        if not entry.isfile():
            raise ToolbotError(
                "ARCHIVE_INVALID", f'{label}: "{wanted}" is not a regular file (directory, link or device?)'
            )
        _check_size(entry.size, max_bytes, wanted, label)
        source = tar_file.extractfile(entry)
        if source is None:  # pragma: no cover - isfile() members always have data
            raise ToolbotError("ARCHIVE_INVALID", f'{label}: cannot read "{wanted}"')
        with source:
            return _copy_capped(source, output, max_bytes, wanted, label)


def _member_not_found(wanted: str, files: list[str], label: str) -> ToolbotError:
    listed = ", ".join(sorted(files)[:LISTED_MEMBERS]) or "(no files)"
    more = f" (+{len(files) - LISTED_MEMBERS} more)" if len(files) > LISTED_MEMBERS else ""
    return ToolbotError("ASSET_NOT_FOUND", f'{label} has no file "{wanted}".\n  files: {listed}{more}')


def _check_size(size: int, max_bytes: int, wanted: str, label: str) -> None:
    if size > max_bytes:
        raise ToolbotError("ARCHIVE_INVALID", f'{label}: "{wanted}" is {size} bytes, over the {max_bytes} byte limit')


def _copy_capped(source: IO[bytes], output: Path, max_bytes: int, wanted: str, label: str) -> int:
    """Copy, enforcing the limit on actual bytes (declared sizes can lie)."""
    written = 0
    with output.open("xb") as destination:
        while chunk := source.read(1024 * 1024):
            written += len(chunk)
            if written > max_bytes:
                raise ToolbotError("ARCHIVE_INVALID", f'{label}: "{wanted}" exceeds the {max_bytes} byte limit')
            destination.write(chunk)
    return written
