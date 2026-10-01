"""``tools/<name>/tool.lock.json``: exactly what is committed.

Records which upstream release and asset each file came from, how it was
verified, and its SHA-256. It makes MR diffs reviewable and lets
``toolbot verify`` prove in CI that committed binaries match the declared
version. Deterministic (no timestamps): re-running an update for the same
version produces no diff.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from toolbot.config.models import ChecksumConfig, ToolDefinition
from toolbot.errors import ToolbotError
from toolbot.paths import LOCK_FILE


@dataclass(frozen=True)
class LockedAsset:
    upstream: str
    destination: str
    size: int
    sha256: str
    verifiedBy: str


@dataclass(frozen=True)
class LockFile:
    tool: str
    version: str
    tag: str
    source: dict[str, str]
    assets: dict[str, LockedAsset]
    lockfileVersion: int = 1

    def serialise(self) -> str:
        data: dict[str, Any] = {
            "lockfileVersion": self.lockfileVersion,
            "tool": self.tool,
            "version": self.version,
            "tag": self.tag,
            "source": self.source,
            "assets": {asset_id: asdict(asset) for asset_id, asset in sorted(self.assets.items())},
        }
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def lock_path(tool: ToolDefinition) -> Path:
    return tool.dir / LOCK_FILE


def read_lock(tool: ToolDefinition) -> LockFile | None:
    path = lock_path(tool)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
        if data.get("lockfileVersion") != 1:
            raise ValueError("unsupported lockfileVersion")
        return LockFile(
            tool=data["tool"],
            version=data["version"],
            tag=data["tag"],
            source=dict(data["source"]),
            assets={asset_id: LockedAsset(**asset) for asset_id, asset in data["assets"].items()},
        )
    except (ValueError, KeyError, TypeError):
        raise ToolbotError("VERIFY_FAILED", f'{path} is corrupt; run "toolbot update {tool.name}"') from None


def describe_verification(checksum: ChecksumConfig, checksum_file: str | None = None) -> str:
    if checksum.type == "none":
        return "none"
    if checksum.type == "provider":
        return "provider-digest"
    return f"{checksum.algorithm}:{checksum_file or checksum.file}"
