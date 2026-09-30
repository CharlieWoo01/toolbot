"""The source-provider contract.

A source provider answers, for one platform:

* which releases exist (newest first), with their tag, draft/prerelease
  flags, web URL, notes and assets;
* one release by tag;
* how to download a given asset (URL + credentials).

Choosing *which* release to install (tag parsing, version ordering,
prerelease and ignore rules) is shared logic in ``toolbot.services.discovery``,
so providers stay thin and the rest of Toolbot never sees platform APIs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field

from toolbot.config.models import SourceConfig
from toolbot.http import HttpRequest


@dataclass(frozen=True)
class Digest:
    algorithm: str
    hex: str


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    """Upstream file name; used only for matching, never as a local path."""
    url: str
    """Provider-specific download location, passed back to ``download_request``."""
    size: int | None = None
    digest: Digest | None = None


@dataclass(frozen=True)
class Release:
    tag: str
    url: str
    """Human-facing release page."""
    assets: tuple[ReleaseAsset, ...] = ()
    draft: bool = False
    """Not published (GitHub drafts, GitLab upcoming releases). Always skipped."""
    prerelease: bool = False
    """Marked as prerelease by the platform (in addition to version-based detection)."""
    notes: str = field(default="", repr=False)
    published_at: str | None = None


class SourceProvider(ABC):
    id: str
    """Value of ``source.provider`` in tool definitions."""
    default_host: str
    token_env: str
    """Environment variable holding an optional read-only token."""
    supports_digest: bool
    """Whether assets carry digests usable by ``checksum.type: provider``."""

    @abstractmethod
    def check_repository(self, repository: str) -> str | None:
        """Problem description if ``repository`` is malformed, else None."""

    @abstractmethod
    def list_releases(self, source: SourceConfig) -> Iterator[Release]:
        """Releases, newest first (paginated lazily)."""

    @abstractmethod
    def get_release(self, source: SourceConfig, tag: str) -> Release:
        """One release by tag, or ``RELEASE_NOT_FOUND``."""

    @abstractmethod
    def download_request(self, source: SourceConfig, asset: ReleaseAsset) -> HttpRequest:
        """URL and headers for downloading ``asset``; credentials only for the provider's own origin."""

    def auth_hint(self) -> str:
        return f"set {self.token_env} to a read-only token if the source is private"
