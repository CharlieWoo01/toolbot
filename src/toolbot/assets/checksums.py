"""Checksum files and digests."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from toolbot.errors import ToolbotError

_HEX_LENGTH = {"sha256": 64, "sha512": 128}


def hash_file(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_expected_digest(content: str, asset_name: str, algorithm: str, checksum_file: str) -> str:
    """Expected digest of ``asset_name`` from a checksum file.

    Supported formats (the ones real projects publish):

    * a single bare digest (``<hex>``)
    * ``sha256sum``/``shasum`` text or binary mode (``<hex>  name``, ``<hex> *name``)
    * BSD style (``SHA256 (name) = <hex>``)

    Entries match the exact file name (a leading ``./`` is ignored), so
    ``tool.zip`` never matches ``tool.zip.sig``. A missing or conflicting
    entry is an error, never a guess.
    """
    length = _HEX_LENGTH[algorithm]
    lines = [line.strip() for line in content.splitlines()]
    lines = [line for line in lines if line and not line.startswith("#")]
    if len(lines) == 1 and re.fullmatch(rf"[0-9a-fA-F]{{{length}}}", lines[0]):
        return lines[0].lower()

    gnu = re.compile(rf"^([0-9a-fA-F]{{{length}}}) [ *](.+)$")
    bsd = re.compile(rf"^{algorithm.upper()} \((.+)\) = ([0-9a-fA-F]{{{length}}})$", re.IGNORECASE)
    found: list[str] = []
    for line in lines:
        if m := gnu.match(line):
            hex_digest, name = m.group(1), m.group(2)
        elif m := bsd.match(line):
            name, hex_digest = m.group(1), m.group(2)
        else:
            continue
        if name.removeprefix("./") == asset_name:
            found.append(hex_digest.lower())
    if not found:
        raise ToolbotError("CHECKSUM_UNAVAILABLE", f'{checksum_file} has no {algorithm} entry for "{asset_name}"')
    if len(set(found)) > 1:
        raise ToolbotError("CHECKSUM_MISMATCH", f'{checksum_file} lists conflicting digests for "{asset_name}"')
    return found[0]


def assert_digest(actual: str, expected: str, what: str, source: str) -> None:
    if actual.lower() != expected.lower():
        raise ToolbotError(
            "CHECKSUM_MISMATCH",
            f"checksum mismatch for {what} (verified against {source}):\n"
            f"  expected {expected}\n  actual   {actual}\n"
            "The download was discarded and the existing file was left untouched.",
        )
