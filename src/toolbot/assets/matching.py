"""Deterministic mapping of configured assets to what will be downloaded.

* ``name``: the release asset with exactly this name must exist.
* ``pattern``: exactly one release asset must match the glob.
* ``url``: download from this URL (Terraform, Helm, kubectl… publish
  binaries on their own download sites rather than as release assets).
* ``path``: a file committed in the source repository, read at the release
  tag (tools whose binaries live inside a repository).

Zero matches, several matches, or two configured assets resolving to the same
file (and the same archive member) are hard errors, so a pattern that is too
broad can never silently pick the wrong platform or architecture.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from toolbot.config.models import AssetConfig
from toolbot.config.placeholders import render
from toolbot.errors import ToolbotError
from toolbot.providers.source.base import ReleaseAsset


@dataclass(frozen=True)
class ResolvedAsset:
    config: AssetConfig
    upstream: ReleaseAsset
    """For ``url`` assets ``url`` is the download URL; for ``path`` assets it is
    the path inside the source repository."""
    extract: str = ""
    """Rendered archive member to commit, if any."""

    @property
    def by_url(self) -> bool:
        return self.config.match.kind == "url"

    @property
    def from_repository(self) -> bool:
        return self.config.match.kind == "path"

    @property
    def upstream_label(self) -> str:
        """What the lock file and MR show: release asset name, URL or repository path,
        plus ``!member`` for archives."""
        base = self.upstream.name if self.config.match.kind in ("name", "pattern") else self.upstream.url
        return f"{base}!{self.extract}" if self.extract else base


def resolve_assets(
    configs: Sequence[AssetConfig],
    available: Sequence[ReleaseAsset],
    values: Mapping[str, str],
    release_label: str,
) -> list[ResolvedAsset]:
    resolved: list[ResolvedAsset] = []
    claimed_by: dict[str, str] = {}
    available_names = [asset.name for asset in available]
    for config in configs:
        wanted = render(config.match.template, values)
        if config.match.kind == "url":
            match = ReleaseAsset(name=file_name_from_url(wanted), url=wanted)
        elif config.match.kind == "path":
            match = ReleaseAsset(name=wanted.rsplit("/", 1)[-1], url=wanted.removeprefix("./"))
        else:
            match = _match_release_asset(config, wanted, available, available_names, release_label)
        asset = ResolvedAsset(config, match, render(config.extract, values) if config.extract else "")
        identity = asset.upstream_label
        if identity in claimed_by:
            raise ToolbotError(
                "ASSET_AMBIGUOUS",
                f'assets "{claimed_by[identity]}" and "{config.id}" both resolve to "{identity}" in {release_label}',
            )
        claimed_by[identity] = config.id
        resolved.append(asset)
    return resolved


def _match_release_asset(
    config: AssetConfig,
    wanted: str,
    available: Sequence[ReleaseAsset],
    available_names: list[str],
    release_label: str,
) -> ReleaseAsset:
    if config.match.kind == "name":
        matches = [asset for asset in available if asset.name == wanted]
    else:
        pattern = glob_to_regex(wanted)
        matches = [asset for asset in available if pattern.fullmatch(asset.name)]
    if not matches:
        verb = "named" if config.match.kind == "name" else "matches"
        raise ToolbotError(
            "ASSET_NOT_FOUND",
            f'asset "{config.id}": no upstream asset {verb} "{wanted}" in {release_label}.\n'
            f"  available: {', '.join(available_names) if available_names else '(none)'}",
        )
    if len(matches) > 1:
        raise ToolbotError(
            "ASSET_AMBIGUOUS",
            f'asset "{config.id}": pattern "{wanted}" matches {len(matches)} assets in {release_label}: '
            f'{", ".join(asset.name for asset in matches)}. Make the pattern more specific or use "name".',
        )
    return matches[0]


def glob_to_regex(glob: str) -> re.Pattern[str]:
    """``*`` = any run of characters, ``?`` = one character; everything else literal."""
    parts = [".*" if character == "*" else "." if character == "?" else re.escape(character) for character in glob]
    return re.compile("".join(parts), re.DOTALL)


def file_name_from_url(url: str) -> str:
    """Last path segment, e.g. ``terraform_1.9.0_linux_amd64.zip``; used to find checksum entries."""
    return unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]) or url
