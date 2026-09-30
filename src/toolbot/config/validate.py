"""Validation of raw tool definitions into ``ToolDefinition``.

Reports every problem in a file at once. Unknown keys are errors: a misspelt
``checksum`` silently disabling verification is exactly what must not happen.
``schema/tool.schema.json`` mirrors these rules for editors; a test keeps the
two in sync.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from toolbot.config.models import (
    AssetConfig,
    AssetMatcher,
    ChecksumAlgorithm,
    ChecksumConfig,
    SourceConfig,
    ToolDefinition,
)
from toolbot.config.placeholders import check_template, count_placeholder
from toolbot.errors import ConfigValidationError, ToolbotError
from toolbot.paths import TOOL_NAME, check_destination
from toolbot.providers.source import SourceProviders
from toolbot.versions import SCHEMES, check_constraint, parse_version

TOOL_KEYS = ("name", "version", "source", "tag", "versioning", "prereleases", "ignore", "checksum", "assets")
SOURCE_KEYS = ("provider", "repository", "host")
ASSET_KEYS = ("name", "pattern", "destination", "checksum")
CHECKSUM_KEYS = ("type", "file", "algorithm")
CHECKSUM_TYPES = ("none", "provider", "file")
CHECKSUM_ALGORITHMS: tuple[ChecksumAlgorithm, ...] = ("sha256", "sha512")

_ASSET_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_TAG_TEXT = re.compile(r"^[A-Za-z0-9._/@+-]*$")
# Upstream file names: printable, no path separators or glob characters.
_FILE_TEXT = re.compile(r"^[^/\\\x00-\x1f\x7f*?]+$")


def validate_definition(
    raw: object,
    file: Path,
    directory: Path,
    providers: SourceProviders,
    expected_name: str | None = None,
) -> ToolDefinition:
    problems: list[str] = []
    if not isinstance(raw, dict):
        raise ConfigValidationError(str(file), ["the file must contain a YAML mapping (key: value pairs)"])
    _unknown_keys(raw, TOOL_KEYS, "", problems)

    name = _require_str(raw, "name", problems)
    if name is not None:
        if not TOOL_NAME.match(name):
            problems.append(f'name: "{name}" must be lowercase letters, digits and "-" (max 64 chars)')
        elif expected_name is not None and name != expected_name:
            problems.append(f'name: "{name}" must match its directory name "{expected_name}"')

    versioning = "semver"
    if "versioning" in raw:
        if raw["versioning"] not in SCHEMES:
            problems.append(f"versioning: must be one of {', '.join(SCHEMES)}")
        else:
            versioning = cast(str, raw["versioning"])

    version = _validate_version(raw.get("version"), versioning, problems)
    source = _validate_source(raw.get("source"), providers, problems)

    tag = "{version}"
    if "tag" in raw:
        if not isinstance(raw["tag"], str):
            problems.append('tag: must be a string such as "v{version}"' + _quote_hint(raw["tag"]))
        else:
            tag = raw["tag"]
            tag_problems = check_template(tag, ["version"])
            if count_placeholder(tag, "version") != 1:
                tag_problems.append('must contain "{version}" exactly once')
            if not _TAG_TEXT.match(tag.replace("{version}", "")):
                tag_problems.append('may only contain letters, digits and . _ / @ + - around "{version}"')
            problems.extend(f'tag: "{tag}" {p}' for p in tag_problems)

    prereleases = False
    if "prereleases" in raw:
        if not isinstance(raw["prereleases"], bool):
            problems.append("prereleases: must be true or false")
        else:
            prereleases = raw["prereleases"]

    ignore: list[str] = []
    if "ignore" in raw:
        value = raw["ignore"]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            problems.append('ignore: must be a list of quoted versions or constraints, e.g. ["0.9.0", ">=2.0.0"]')
        else:
            for spec in value:
                problem = check_constraint(spec, versioning)
                if problem:
                    problems.append(f"ignore: {problem}")
            ignore = value

    provider = providers.get(source.provider) if source else None
    supports_digest = provider.supports_digest if provider else True
    default_checksum = (
        ChecksumConfig("none")
        if "checksum" not in raw
        else _validate_checksum(raw["checksum"], "checksum", supports_digest, problems)
    )
    assets = _validate_assets(raw.get("assets"), default_checksum, supports_digest, problems)

    if problems:
        raise ConfigValidationError(str(file), problems)
    if name is None or version is None or source is None:  # always reported as problems above
        raise ConfigValidationError(str(file), ["incomplete definition"])
    return ToolDefinition(
        name=name,
        version=version,
        source=source,
        tag=tag,
        versioning=versioning,
        prereleases=prereleases,
        ignore=tuple(ignore),
        assets=tuple(assets),
        file=file,
        dir=directory,
    )


def _validate_version(value: object, scheme: str, problems: list[str]) -> str | None:
    if value is None:
        problems.append("version: is required")
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # YAML turns 1.10 into the float 1.1; refuse rather than guess.
        problems.append(f'version: must be a quoted string, e.g. version: "{value}" (YAML read it as a number)')
        return None
    if not isinstance(value, str):
        problems.append('version: must be a string such as "1.2.3"')
        return None
    try:
        parse_version(value, scheme)
    except ToolbotError as err:
        problems.append(f"version: {err.message}")
        return None
    return value


def _validate_source(value: object, providers: SourceProviders, problems: list[str]) -> SourceConfig | None:
    if not isinstance(value, dict):
        problems.append("source: is required and must contain provider and repository")
        return None
    _unknown_keys(value, SOURCE_KEYS, "source.", problems)
    provider_id = _require_str(value, "provider", problems, "source.")
    repository = _require_str(value, "repository", problems, "source.")
    if provider_id is None:
        return None
    provider = providers.get(provider_id)
    if provider is None:
        problems.append(f'source.provider: unknown provider "{provider_id}" (supported: {", ".join(providers.ids())})')
        return None
    if repository is not None:
        problem = provider.check_repository(repository)
        if problem:
            problems.append(f'source.repository: "{repository}" {problem}')
    host = provider.default_host
    if "host" in value:
        normalised = normalise_host(value["host"]) if isinstance(value["host"], str) else None
        if normalised is None:
            problems.append("source.host: must be an https:// URL without credentials, query or fragment")
        else:
            host = normalised
    if repository is None:
        return None
    return SourceConfig(provider_id, repository, host)


def normalise_host(value: str) -> str | None:
    try:
        url = urlsplit(value)
    except ValueError:
        return None
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        return None
    netloc = url.hostname + (f":{url.port}" if url.port else "")
    return f"https://{netloc}{url.path.rstrip('/')}"


def _validate_checksum(value: object, where: str, supports_digest: bool, problems: list[str]) -> ChecksumConfig:
    if not isinstance(value, dict):
        problems.append(f'{where}: must be a mapping with a "type" ({", ".join(CHECKSUM_TYPES)})')
        return ChecksumConfig("none")
    _unknown_keys(value, CHECKSUM_KEYS, f"{where}.", problems)
    kind = value.get("type")
    if kind == "none" or kind == "provider":
        if "file" in value or "algorithm" in value:
            problems.append(f'{where}: "file" and "algorithm" have no effect with type "{kind}"')
        if kind == "provider" and not supports_digest:
            problems.append(
                f'{where}.type: "provider" is not supported by this source provider '
                '(it publishes no digests); use type "file"'
            )
        return ChecksumConfig(kind)
    if kind == "file":
        file = value.get("file")
        template = ""
        if not isinstance(file, str) or not file:
            problems.append(
                f'{where}.file: is required for type "file", e.g. "{{asset}}.sha256" or "SHA256SUMS"'
                + _quote_hint(file)
            )
        else:
            template = file
            file_problems = check_template(file, ["asset", "version", "tag"])
            if not _FILE_TEXT.match(file):
                file_problems.append('must be a plain file name (no "/", "\\", "*" or "?")')
            problems.extend(f'{where}.file: "{file}" {p}' for p in file_problems)
        algorithm = value.get("algorithm", "sha256")
        if algorithm not in CHECKSUM_ALGORITHMS:
            problems.append(
                f'{where}.algorithm: unsupported "{algorithm}" (supported: {", ".join(CHECKSUM_ALGORITHMS)})'
            )
            algorithm = "sha256"
        return ChecksumConfig("file", template, cast(ChecksumAlgorithm, algorithm))
    problems.append(f'{where}.type: unsupported checksum type "{kind}" (supported: {", ".join(CHECKSUM_TYPES)})')
    return ChecksumConfig("none")


def _validate_assets(
    value: object, default_checksum: ChecksumConfig, supports_digest: bool, problems: list[str]
) -> list[AssetConfig]:
    if not isinstance(value, dict) or not value:
        problems.append(
            "assets: at least one asset is required, e.g. assets: { linux-amd64: { name: ..., destination: ... } }"
        )
        return []
    assets: list[AssetConfig] = []
    for asset_id, entry in value.items():
        where = f"assets.{asset_id}"
        if not isinstance(asset_id, str) or not _ASSET_ID.match(asset_id):
            problems.append(f'{where}: asset id must be lowercase letters, digits and "-" (e.g. "linux-amd64")')
        if not isinstance(entry, dict):
            problems.append(f'{where}: must be a mapping with "name" (or "pattern") and "destination"')
            continue
        _unknown_keys(entry, ASSET_KEYS, f"{where}.", problems)
        matcher = _validate_matcher(entry, where, problems)
        destination = _require_str(entry, "destination", problems, f"{where}.")
        if destination is not None:
            problems.extend(f'{where}.destination: "{destination}" {p}' for p in check_destination(destination))
        checksum = (
            default_checksum
            if "checksum" not in entry
            else _validate_checksum(entry["checksum"], f"{where}.checksum", supports_digest, problems)
        )
        if matcher is not None and destination is not None:
            assets.append(AssetConfig(str(asset_id), matcher, destination, checksum))

    seen_matchers: dict[tuple[str, str], str] = {}
    seen_destinations: dict[str, str] = {}
    for asset in assets:
        key = (asset.match.kind, asset.match.template)
        if key in seen_matchers:
            problems.append(f"assets.{asset.id}: ambiguous — same {asset.match.kind} as assets.{seen_matchers[key]}")
        seen_matchers[key] = asset.id
        # Case-insensitive: a checkout on Windows or macOS would collide otherwise.
        dest = asset.destination.lower()
        for existing, owner in seen_destinations.items():
            if existing == dest:
                problems.append(
                    f'assets.{asset.id}.destination: duplicate destination "{asset.destination}" '
                    f"(also used by assets.{owner})"
                )
            elif existing.startswith(dest + "/") or dest.startswith(existing + "/"):
                problems.append(
                    f'assets.{asset.id}.destination: "{asset.destination}" conflicts with assets.{owner} '
                    "(one is inside the other)"
                )
        seen_destinations[dest] = asset.id
    return assets


def _validate_matcher(entry: dict[str, Any], where: str, problems: list[str]) -> AssetMatcher | None:
    name, pattern = entry.get("name"), entry.get("pattern")
    if name is not None and pattern is not None:
        problems.append(f'{where}: set either "name" or "pattern", not both')
        return None
    if name is None and pattern is None:
        problems.append(f'{where}: "name" (exact upstream asset name) is required')
        return None
    if name is not None:
        if not isinstance(name, str) or not name:
            problems.append(f"{where}.name: must be a non-empty string" + _quote_hint(name))
            return None
        found = check_template(name, ["version", "tag"])
        if not _FILE_TEXT.match(name):
            found.append('must be a plain file name (no "/", "\\", "*" or "?"; use "pattern" for wildcards)')
        problems.extend(f'{where}.name: "{name}" {p}' for p in found)
        return None if found else AssetMatcher("name", name)
    if not isinstance(pattern, str) or not pattern:
        problems.append(f"{where}.pattern: must be a non-empty string" + _quote_hint(pattern))
        return None
    found = check_template(pattern, ["version", "tag"])
    without_wildcards = re.sub(r"[*?]", "", pattern)
    if not _FILE_TEXT.match(without_wildcards or "x"):
        found.append('may only use "*" and "?" as wildcards and must not contain "/" or "\\"')
    if not re.search(r"[*?]", pattern):
        found.append('contains no wildcard; use "name" for exact matches')
    if "**" in pattern:
        found.append('must not contain "**"')
    literal = re.sub(r"\{(version|tag)\}", "", without_wildcards)
    if len(re.sub(r"[._-]", "", literal)) < 3:
        found.append("is too broad; include literal text identifying the platform/architecture/extension")
    problems.extend(f'{where}.pattern: "{pattern}" {p}' for p in found)
    return None if found else AssetMatcher("pattern", pattern)


def _require_str(obj: dict[str, Any], key: str, problems: list[str], prefix: str = "") -> str | None:
    value = obj.get(key)
    if value is None or value == "":
        problems.append(f"{prefix}{key}: is required")
        return None
    if not isinstance(value, str):
        problems.append(f"{prefix}{key}: must be a string")
        return None
    return value


def _unknown_keys(obj: dict[str, Any], allowed: tuple[str, ...], prefix: str, problems: list[str]) -> None:
    for key in obj:
        if key not in allowed:
            problems.append(f"{prefix}{key}: unknown key (allowed: {', '.join(allowed)})")


def _quote_hint(value: object) -> str:
    """``tag: {version}`` is a YAML mapping, not a string; say so explicitly."""
    return ' (values starting with "{" must be quoted in YAML, e.g. "{version}.zip")' if isinstance(value, dict) else ""
