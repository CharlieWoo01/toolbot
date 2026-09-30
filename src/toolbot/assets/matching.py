"""Deterministic mapping of configured assets to upstream release assets.

Exact names must exist verbatim; patterns must match exactly one asset. Zero
matches, several matches, or two configured assets resolving to the same
upstream file are hard errors, so a pattern that is too broad can never
silently pick the wrong platform or architecture.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from toolbot.config.models import AssetConfig
from toolbot.config.placeholders import render
from toolbot.errors import ToolbotError
from toolbot.providers.source.base import ReleaseAsset


@dataclass(frozen=True)
class ResolvedAsset:
    config: AssetConfig
    upstream: ReleaseAsset


def resolve_assets(
    configs: Sequence[AssetConfig],
    available: Sequence[ReleaseAsset],
    values: Mapping[str, str],
    release_label: str,
) -> list[ResolvedAsset]:
    resolved: list[ResolvedAsset] = []
    claimed_by: dict[str, str] = {}
    names = [a.name for a in available]
    for config in configs:
        wanted = render(config.match.template, values)
        if config.match.kind == "name":
            matches = [a for a in available if a.name == wanted]
        else:
            regex = glob_to_regex(wanted)
            matches = [a for a in available if regex.fullmatch(a.name)]
        if not matches:
            verb = "named" if config.match.kind == "name" else "matches"
            raise ToolbotError(
                "ASSET_NOT_FOUND",
                f'asset "{config.id}": no upstream asset {verb} "{wanted}" in {release_label}.\n'
                f"  available: {', '.join(names) if names else '(none)'}",
            )
        if len(matches) > 1:
            raise ToolbotError(
                "ASSET_AMBIGUOUS",
                f'asset "{config.id}": pattern "{wanted}" matches {len(matches)} assets in {release_label}: '
                f'{", ".join(m.name for m in matches)}. Make the pattern more specific or use "name".',
            )
        match = matches[0]
        if match.name in claimed_by:
            raise ToolbotError(
                "ASSET_AMBIGUOUS",
                f'assets "{claimed_by[match.name]}" and "{config.id}" both resolve to "{match.name}" '
                f"in {release_label}",
            )
        claimed_by[match.name] = config.id
        resolved.append(ResolvedAsset(config, match))
    return resolved


def glob_to_regex(glob: str) -> re.Pattern[str]:
    """``*`` = any run of characters, ``?`` = one character; everything else literal."""
    parts = [".*" if c == "*" else "." if c == "?" else re.escape(c) for c in glob]
    return re.compile("".join(parts), re.DOTALL)
