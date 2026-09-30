"""Validated, normalised tool definitions.

Everything downstream of the loader works with these types only; raw YAML
never leaves ``toolbot.config``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ChecksumAlgorithm = Literal["sha256", "sha512"]


@dataclass(frozen=True)
class SourceConfig:
    provider: str
    """Source provider id, e.g. ``github`` or ``gitlab``."""
    repository: str
    """Provider-specific project, e.g. ``owner/repo`` or ``group/sub/project``."""
    host: str
    """Normalised base URL of the provider instance, without trailing slash."""


@dataclass(frozen=True)
class ChecksumConfig:
    type: Literal["none", "provider", "file"]
    """none: no verification; provider: digest reported by the provider API;
    file: a checksum file attached to the same release."""
    file: str = ""
    """For ``file``: template (``{asset}``, ``{version}``, ``{tag}``) naming the checksum asset."""
    algorithm: ChecksumAlgorithm = "sha256"


@dataclass(frozen=True)
class AssetMatcher:
    kind: Literal["name", "pattern"]
    template: str


@dataclass(frozen=True)
class AssetConfig:
    id: str
    match: AssetMatcher
    destination: str
    """Relative to the tool directory."""
    checksum: ChecksumConfig


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    version: str
    source: SourceConfig
    tag: str
    """Tag template containing ``{version}`` exactly once, e.g. ``v{version}``."""
    versioning: str
    """``semver`` (default) or ``pep440``."""
    prereleases: bool
    ignore: tuple[str, ...]
    """Exact versions or constraints (``>=2.0.0``) never proposed as updates."""
    assets: tuple[AssetConfig, ...]
    file: Path
    dir: Path
