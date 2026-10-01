"""Validated, normalised tool definitions.

Everything downstream of the loader works with these types only; raw YAML
never leaves ``toolbot.config``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ChecksumAlgorithm = Literal["sha256", "sha512"]
VersionSource = Literal["releases", "tags"]
MatcherKind = Literal["name", "pattern", "url", "path"]
RELEASE_ASSET_KINDS = ("name", "pattern")
"""Matcher kinds that need a platform release (``versions: releases``)."""


@dataclass(frozen=True)
class SourceConfig:
    provider: str
    """Source provider id: ``github`` or ``gitlab``."""
    repository: str
    """Provider-specific project, e.g. ``owner/repo`` or ``group/sub/project``."""
    host: str
    """Normalised base URL of the provider instance, without trailing slash."""
    versions: VersionSource = "releases"
    """Where versions come from: platform releases, or plain git tags (projects
    that tag but never create releases)."""


@dataclass(frozen=True)
class ChecksumConfig:
    type: Literal["none", "provider", "file"]
    """none: no verification; provider: digest reported by the provider API;
    file: a checksum file."""
    file: str = ""
    """For ``file``: template naming a *release asset* holding checksums."""
    url: str = ""
    """For ``file``: template of a *URL* holding checksums."""
    path: str = ""
    """For ``file``: template of a checksum file *committed in the source
    repository* at the release tag."""
    algorithm: ChecksumAlgorithm = "sha256"


@dataclass(frozen=True)
class AssetMatcher:
    kind: MatcherKind
    """name: exact release asset name; pattern: glob matching one release
    asset; url: download from this URL; path: a file committed in the source
    repository, read at the release tag."""
    template: str


@dataclass(frozen=True)
class AssetConfig:
    id: str
    match: AssetMatcher
    destination: str
    """Relative to the tool directory, or to the repository root with a leading ``/``."""
    checksum: ChecksumConfig
    extract: str = ""
    """If set, the download is an archive (zip, tar, tar.gz/xz/bz2) and only
    this member is committed. The checksum is verified on the archive."""
    executable: bool = False
    """Commit with the executable bit (``git add --chmod=+x``)."""


@dataclass(frozen=True)
class VersionFile:
    """A file in the target repository that pins the tool's version, e.g.
    ``.terraform-version`` or a Dockerfile ``ARG``. Toolbot replaces the text
    rendered for the current version with the text for the new version."""

    path: str
    """Relative to the tool directory, or to the repository root with a leading ``/``."""
    template: str
    """Text containing ``{version}`` (or ``{tag}``), e.g. ``ARG TERRAFORM_VERSION={version}``."""


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
    """Versions never proposed: exact (``0.9.0``) or constraints (``>=2.0.0``)."""
    allow: tuple[str, ...]
    """Constraints every proposed version must satisfy, e.g. ``<2.0.0`` or ``==1.4.2``."""
    assets: tuple[AssetConfig, ...]
    file: Path
    dir: Path
    version_files: tuple[VersionFile, ...] = ()

    @property
    def root(self) -> Path:
        """Repository root (definitions live in ``<root>/tools/<name>/``)."""
        return self.dir.parent.parent
