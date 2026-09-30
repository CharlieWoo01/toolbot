"""Finding the newest acceptable release of a tool.

Never trusts a platform's "latest" flag. Every published release is
considered, and a release is a candidate only if:

* it is not a draft / upcoming release;
* its tag follows the tool's ``tag`` template (other tags, e.g. of other
  components in a monorepo, are ignored);
* the version parses under the tool's ``versioning`` scheme;
* it is stable (neither flagged as prerelease by the platform nor carrying a
  prerelease version such as ``1.0.0-rc.1``), unless ``prereleases: true``;
* it is not excluded by an ``ignore`` rule.

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
    ignored: list[str] = field(default_factory=list)
    """Newer versions skipped because of ``ignore`` rules (for reporting)."""

    @property
    def status(self) -> Status:
        if self.latest is None:
            return "no-releases"
        if self.latest.version > self.current:
            return "update-available"
        if self.latest.version == self.current:
            return "up-to-date"
        return "ahead"


def discover(ctx: Context, tool: ToolDefinition) -> Discovery:
    current = parse_version(tool.version, tool.versioning)
    provider = ctx.providers.require(tool.source.provider)
    best: Candidate | None = None
    ignored: list[str] = []
    for release in provider.list_releases(tool.source):
        if release.draft:
            continue
        text = version_from_tag(release.tag, tool.tag)
        if text is None:
            continue
        try:
            version = parse_version(text, tool.versioning)
        except ToolbotError:
            continue
        if (release.prerelease or version.prerelease) and not tool.prereleases:
            continue
        if any(matches(version, rule) for rule in tool.ignore):
            if version > current:
                ignored.append(version.raw)
            continue
        if best is None or version > best.version:
            best = Candidate(version, release)
    return Discovery(tool, current, best, ignored)
