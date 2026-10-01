"""The source-provider contract.

A source provider answers, for one platform:

* which releases exist (newest first), with their tag, draft/prerelease
  flags, web URL, notes and assets;
* one release by tag;
* how to download a given asset (URL + credentials).

Choosing *which* release to install (tag parsing, version ordering,
prerelease/ignore/allow rules) is shared logic in ``toolbot.services.discovery``,
so providers stay thin and the rest of Toolbot never sees platform APIs.
Credentials come from the host registry: a token is only sent to URLs of
the host it belongs to.
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
    """Where to download it (passed back to ``download_request``)."""
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
    def list_tags(self, source: SourceConfig, prefix: str) -> Iterator[Release]:
        """Git tags starting with ``prefix`` as asset-less releases (``versions: tags``)."""

    @abstractmethod
    def get_tag(self, source: SourceConfig, tag: str) -> Release:
        """One git tag as an asset-less release, or ``RELEASE_NOT_FOUND``."""

    @abstractmethod
    def download_request(self, source: SourceConfig, asset: ReleaseAsset) -> HttpRequest:
        """URL and headers for downloading ``asset``."""

    @abstractmethod
    def file_request(self, source: SourceConfig, path: str, ref: str) -> HttpRequest:
        """URL and headers for the raw content of ``path`` committed in the source repository at ``ref``."""

    @abstractmethod
    def auth_hint(self, source: SourceConfig) -> str:
        """What to do when access is denied, e.g. which token variable to set."""

    def list_releases_or_tags(self, source: SourceConfig, tag_prefix: str) -> Iterator[Release]:
        """Version candidates according to ``source.versions``."""
        if source.versions == "tags":
            return self.list_tags(source, tag_prefix)
        return self.list_releases(source)

    def get_release_or_tag(self, source: SourceConfig, tag: str) -> Release:
        if source.versions == "tags":
            return self.get_tag(source, tag)
        return self.get_release(source, tag)
