"""Finding the newest acceptable release of a tool.

Never trusts a platform's "latest" flag. Every published release (or every
git tag, with ``source.versions: tags``) is considered, and it is a
candidate only if:

* it is not a draft / upcoming release;
* its tag follows the tool's ``tag`` template (other tags, e.g. of other
  components in a monorepo, are ignored);
* the version parses under the tool's ``versioning`` scheme;
* it is stable (neither flagged as prerelease by the platform nor carrying a
  prerelease version such as ``1.0.0-rc.1``), unless prereleases are enabled;
* it is not excluded by an ``ignore`` rule and satisfies every ``allow`` rule.

The highest candidate by version precedence wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from toolbot.config.models import ToolDefinition
from toolbot.errors import ToolbotError
from toolbot.providers.source.base import Release
from toolbot.services.context import Context
from toolbot.versions import Version, matches, parse_version, version_from_tag

Status = Literal["update-available", "up-to-date", "ahead", "no-releases"]


@dataclass(frozen=True)
class Candidate:
    version: Version
    release: Release


@dataclass
class Discovery:
    tool: ToolDefinition
    current: Version
    latest: Candidate | None
    held_back: list[str] = field(default_factory=list)
    """Newer versions excluded by ``ignore``/``allow``, newest first (for reporting)."""

    def held_back_summary(self, shown: int = 3) -> str:
        """e.g. ``4.3.0, 4.2.4, 4.2.3 (+12 more)``."""
        listed = ", ".join(self.held_back[:shown])
        remaining = len(self.held_back) - shown
        return f"{listed} (+{remaining} more)" if remaining > 0 else listed

    @property
    def status(self) -> Status:
        if self.latest is None:
            return "no-releases"
        if self.latest.version > self.current:
            return "update-available"
        if self.latest.version == self.current:
            return "up-to-date"
        return "ahead"


def is_acceptable(tool: ToolDefinition, version: Version) -> bool:
    """True if ``ignore`` and ``allow`` permit proposing ``version``."""
    if any(matches(version, constraint) for constraint in tool.ignore):
        return False
    return all(matches(version, constraint) for constraint in tool.allow)


def discover(ctx: Context, tool: ToolDefinition) -> Discovery:
    current = parse_version(tool.version, tool.versioning)
    provider = ctx.providers.require(tool.source.provider)
    best: Candidate | None = None
    held_back: list[Version] = []
    tag_prefix = tool.tag.split("{version}", 1)[0]
    for release in provider.list_releases_or_tags(tool.source, tag_prefix):
        if release.draft:
            continue
        version_text = version_from_tag(release.tag, tool.tag)
        if version_text is None:
            continue
        try:
            version = parse_version(version_text, tool.versioning)
        except ToolbotError:
            continue
        if (release.prerelease or version.prerelease) and not tool.prereleases:
            continue
        if not is_acceptable(tool, version):
            if version > current:
                held_back.append(version)
            continue
        if best is None or version > best.version:
            best = Candidate(version, release)
    held_back_newest_first = [version.raw for version in sorted(held_back, reverse=True)]
    return Discovery(tool, current, best, held_back_newest_first)
