"""Validation of raw tool definitions (``tools/<name>/tool.yaml``) into ``ToolDefinition``.

Reports every problem in a file at once. Unknown keys are errors: a misspelt
``checksum`` silently disabling verification is exactly what must not happen.
``schema/tool.schema.json`` mirrors these rules for editors; a test keeps the
two in sync.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

from toolbot.config.fields import (
    Problems,
    check_url_template,
    optional_bool,
    quote_hint,
    require_string,
    string_list,
    unknown_keys,
)
from toolbot.config.models import (
    RELEASE_ASSET_KINDS,
    AssetConfig,
    AssetMatcher,
    ChecksumAlgorithm,
    ChecksumConfig,
    SourceConfig,
    ToolDefinition,
    VersionFile,
    VersionSource,
)
from toolbot.config.placeholders import check_template, count_placeholder
from toolbot.errors import ConfigValidationError, ToolbotError
from toolbot.hosts import HostRegistry, normalise_url
from toolbot.paths import TOOL_NAME, check_destination
from toolbot.providers.source import SourceProviders
from toolbot.versions import SCHEMES, check_constraint, parse_version

TOOL_KEYS = (
    "name",
    "version",
    "source",
    "tag",
    "versioning",
    "prereleases",
    "ignore",
    "allow",
    "checksum",
    "assets",
    "version-files",
)
SOURCE_KEYS = ("provider", "repository", "host", "versions")
VERSION_SOURCES: tuple[VersionSource, ...] = ("releases", "tags")
ASSET_KEYS = ("name", "pattern", "url", "path", "extract", "destination", "executable", "checksum")
MATCHER_KEYS = ("name", "pattern", "url", "path")
CHECKSUM_KEYS = ("type", "file", "url", "path", "algorithm")
CHECKSUM_TYPES = ("none", "provider", "file")
CHECKSUM_ALGORITHMS: tuple[ChecksumAlgorithm, ...] = ("sha256", "sha512")
VERSION_FILE_KEYS = ("path", "template")

ASSET_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
TAG_TEXT = re.compile(r"^[A-Za-z0-9._/@+-]*$")
# Upstream file names: printable, no path separators or glob characters.
FILE_NAME_TEXT = re.compile(r"^[^/\\\x00-\x1f\x7f*?]+$")
# Destinations without an extension (terraform, bin/kubectl) are executables by default.
SCRIPT_SUFFIXES = (".sh", ".bash", ".py")


def validate_definition(
    raw: object,
    file: Path,
    directory: Path,
    providers: SourceProviders,
    hosts: HostRegistry,
    expected_name: str | None = None,
) -> ToolDefinition:
    problems: Problems = []
    if not isinstance(raw, dict):
        raise ConfigValidationError(str(file), ["the file must contain a YAML mapping (key: value pairs)"])
    unknown_keys(raw, TOOL_KEYS, "", problems)

    name = require_string(raw, "name", problems)
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
    source = _validate_source(raw.get("source"), providers, hosts, problems)
    tag = _validate_tag(raw, problems)
    prereleases = optional_bool(raw, "prereleases", "", problems) or False
    ignore = _validate_constraints(raw, "ignore", versioning, problems, '["0.9.0", ">=2.0.0"]')
    allow = _validate_constraints(raw, "allow", versioning, problems, '"<2.0.0"')

    provider = providers.get(source.provider) if source else None
    uses_tags = source is not None and source.versions == "tags"
    supports_digest = (provider.supports_digest if provider else True) and not uses_tags
    default_checksum = (
        ChecksumConfig("none")
        if "checksum" not in raw
        else _validate_checksum(raw["checksum"], "checksum", supports_digest, problems)
    )
    version_files = _validate_version_files(raw.get("version-files"), problems)
    assets = _validate_assets(raw.get("assets"), default_checksum, supports_digest, bool(version_files), problems)
    if uses_tags:
        _check_tag_source(assets, problems)

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
        allow=tuple(allow),
        assets=tuple(assets),
        file=file,
        dir=directory,
        version_files=tuple(version_files),
    )


def _validate_version(value: object, scheme: str, problems: Problems) -> str | None:
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
    except ToolbotError as error:
        problems.append(f"version: {error.message}")
        return None
    return value


def _validate_tag(raw: dict[str, Any], problems: Problems) -> str:
    if "tag" not in raw:
        return "{version}"
    tag = raw["tag"]
    if not isinstance(tag, str):
        problems.append('tag: must be a string such as "v{version}"' + quote_hint(tag))
        return "{version}"
    tag_problems = check_template(tag, ["version"])
    if count_placeholder(tag, "version") != 1:
        tag_problems.append('must contain "{version}" exactly once')
    if not TAG_TEXT.match(tag.replace("{version}", "")):
        tag_problems.append('may only contain letters, digits and . _ / @ + - around "{version}"')
    problems.extend(f'tag: "{tag}" {problem}' for problem in tag_problems)
    return tag


def _validate_constraints(raw: dict[str, Any], key: str, scheme: str, problems: Problems, example: str) -> list[str]:
    if key not in raw:
        return []
    constraints = string_list(raw[key], key, problems, example) or []
    for constraint in constraints:
        problem = check_constraint(constraint, scheme)
        if problem:
            problems.append(f"{key}: {problem}")
    return constraints


def _validate_source(
    value: object, providers: SourceProviders, hosts: HostRegistry, problems: Problems
) -> SourceConfig | None:
    if not isinstance(value, dict):
        problems.append("source: is required and must contain provider (or host) and repository")
        return None
    unknown_keys(value, SOURCE_KEYS, "source.", problems)
    repository = require_string(value, "repository", problems, "source.")

    # `host` is either a host name from toolbot.yaml or an https:// URL.
    host_value = value.get("host")
    named_host = hosts.get(host_value) if isinstance(host_value, str) else None
    provider_id = value.get("provider")
    if provider_id is None and named_host is not None:
        provider_id = named_host.type
    if not isinstance(provider_id, str) or not provider_id:
        problems.append("source.provider: is required (or set source.host to a host name from toolbot.yaml)")
        return None
    provider = providers.get(provider_id)
    if provider is None:
        problems.append(f'source.provider: unknown provider "{provider_id}" (supported: {", ".join(providers.ids())})')
        return None
    if named_host is not None and named_host.type != provider_id:
        problems.append(f'source.host: host "{named_host.name}" is a {named_host.type} host, not {provider_id}')

    host_url = provider.default_host
    if named_host is not None:
        host_url = named_host.url
    elif host_value is not None:
        normalised = normalise_url(host_value) if isinstance(host_value, str) else None
        if normalised is None:
            known = ", ".join(hosts.names())
            problems.append(f"source.host: must be a host name ({known}) or an https:// URL without credentials")
        else:
            host_url = normalised
    if repository is not None:
        problem = provider.check_repository(repository)
        if problem:
            problems.append(f'source.repository: "{repository}" {problem}')
    versions = value.get("versions", "releases")
    if versions not in VERSION_SOURCES:
        problems.append(
            f'source.versions: must be "releases" (platform releases) or "tags" (plain git tags), not "{versions}"'
        )
        versions = "releases"
    if repository is None:
        return None
    return SourceConfig(provider_id, repository, host_url, cast(VersionSource, versions))


def _check_tag_source(assets: list[AssetConfig], problems: Problems) -> None:
    """Plain git tags have no release assets and no digests."""
    for asset in assets:
        if asset.match.kind in RELEASE_ASSET_KINDS:
            problems.append(
                f'assets.{asset.id}.{asset.match.kind}: needs a release, but source.versions is "tags"; '
                'use "url" or "path" (a file committed in the source repository)'
            )
        if asset.checksum.type == "file" and asset.checksum.file:
            problems.append(
                f'assets.{asset.id}: checksum "file" names a release asset, but source.versions is "tags"; '
                'use checksum "url" or "path"'
            )


def _validate_checksum(value: object, where: str, supports_digest: bool, problems: Problems) -> ChecksumConfig:
    if not isinstance(value, dict):
        problems.append(f'{where}: must be a mapping with a "type" ({", ".join(CHECKSUM_TYPES)})')
        return ChecksumConfig("none")
    unknown_keys(value, CHECKSUM_KEYS, f"{where}.", problems)
    checksum_type = value.get("type")
    if checksum_type in ("none", "provider"):
        if any(key in value for key in ("file", "url", "path", "algorithm")):
            problems.append(
                f'{where}: "file", "url", "path" and "algorithm" have no effect with type "{checksum_type}"'
            )
        if checksum_type == "provider" and not supports_digest:
            problems.append(
                f'{where}.type: "provider" needs digests published with release assets (GitHub releases only); '
                'use type "file"'
            )
        return ChecksumConfig(cast(Any, checksum_type))
    if checksum_type != "file":
        problems.append(
            f'{where}.type: unsupported checksum type "{checksum_type}" (supported: {", ".join(CHECKSUM_TYPES)})'
        )
        return ChecksumConfig("none")

    sources = {key: value[key] for key in ("file", "url", "path") if value.get(key) is not None}
    if len(sources) != 1:
        problems.append(
            f'{where}: type "file" needs exactly one of "file" (a release asset, e.g. "{{asset}}.sha256"), '
            '"url" (a download URL) or "path" (a file committed in the source repository)'
        )
    placeholders = ["asset", "version", "tag"]
    for key, template in sources.items():
        if not isinstance(template, str) or not template:
            problems.append(f"{where}.{key}: must be a non-empty string" + quote_hint(template))
            sources[key] = ""
            continue
        if key == "file":
            found = check_template(template, placeholders)
            if not FILE_NAME_TEXT.match(template):
                found.append('must be a plain file name (no "/", "\\", "*" or "?"); use "url" for URLs')
        elif key == "url":
            found = check_url_template(template, placeholders)
        else:
            found = _repository_path_problems(template, placeholders)
        problems.extend(f'{where}.{key}: "{template}" {problem}' for problem in found)

    algorithm = value.get("algorithm", "sha256")
    if algorithm not in CHECKSUM_ALGORITHMS:
        problems.append(f'{where}.algorithm: unsupported "{algorithm}" (supported: {", ".join(CHECKSUM_ALGORITHMS)})')
        algorithm = "sha256"
    return ChecksumConfig(
        "file",
        file=str(sources.get("file", "")),
        url=str(sources.get("url", "")),
        path=str(sources.get("path", "")),
        algorithm=cast(ChecksumAlgorithm, algorithm),
    )


def _repository_path_problems(template: str, placeholders: list[str]) -> list[str]:
    """A path inside an upstream repository or archive, e.g. ``dist/linux/tool`` or ``tool_{version}/bin/tool``."""
    found = check_template(template, placeholders)
    segments = template.removeprefix("./").split("/")
    if template.startswith("/") or any(segment in ("", ".", "..") for segment in segments):
        found.append('must be a relative path without empty, "." or ".." segments')
    elif not all(FILE_NAME_TEXT.match(segment) for segment in segments):
        found.append('must not contain "\\", "*", "?" or control characters')
    return found


def _validate_assets(
    value: object,
    default_checksum: ChecksumConfig,
    supports_digest: bool,
    has_version_files: bool,
    problems: Problems,
) -> list[AssetConfig]:
    if value is None and has_version_files:
        return []  # only version pins are updated, nothing is downloaded
    if not isinstance(value, dict) or not value:
        problems.append(
            "assets: at least one asset is required (or version-files), "
            "e.g. assets: { linux-amd64: { name: ..., destination: ... } }"
        )
        return []
    assets: list[AssetConfig] = []
    for asset_id, entry in value.items():
        where = f"assets.{asset_id}"
        if not isinstance(asset_id, str) or not ASSET_ID.match(asset_id):
            problems.append(f'{where}: asset id must be lowercase letters, digits and "-" (e.g. "linux-amd64")')
        if not isinstance(entry, dict):
            problems.append(f'{where}: must be a mapping with "name" (or "pattern", "url" or "path") and "destination"')
            continue
        unknown_keys(entry, ASSET_KEYS, f"{where}.", problems)
        matcher = _validate_matcher(entry, where, problems)
        destination = require_string(entry, "destination", problems, f"{where}.")
        if destination is not None:
            problems.extend(
                f'{where}.destination: "{destination}" {problem}' for problem in check_destination(destination)
            )
        checksum = (
            default_checksum
            if "checksum" not in entry
            else _validate_checksum(entry["checksum"], f"{where}.checksum", supports_digest, problems)
        )
        if matcher is not None and matcher.kind not in RELEASE_ASSET_KINDS and checksum.type == "provider":
            problems.append(
                f'{where}: checksum type "provider" only works for release assets; '
                'use type "file" with a checksum "url" or "path"'
            )
        extract = ""
        if "extract" in entry:
            member = entry["extract"]
            if not isinstance(member, str) or not member:
                problems.append(f'{where}.extract: must be the path of one file inside the archive, e.g. "terraform"')
            else:
                problems.extend(
                    f'{where}.extract: "{member}" {problem}'
                    for problem in _repository_path_problems(member, ["version", "tag"])
                )
                extract = member.removeprefix("./")
        executable = optional_bool(entry, "executable", f"{where}.", problems)
        if executable is None and destination is not None:
            executable = _looks_executable(destination)
        if matcher is not None and destination is not None:
            assets.append(AssetConfig(str(asset_id), matcher, destination, checksum, extract, bool(executable)))
    _check_asset_collisions(assets, problems)
    return assets


def _looks_executable(destination: str) -> bool:
    file_name = destination.rsplit("/", 1)[-1]
    return "." not in file_name or file_name.endswith(SCRIPT_SUFFIXES)


def _validate_version_files(value: object, problems: Problems) -> list[VersionFile]:
    if value is None:
        return []
    if not isinstance(value, list) or not value:
        problems.append('version-files: must be a list, e.g. [{ path: "/.terraform-version", template: "{version}" }]')
        return []
    version_files: list[VersionFile] = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(value):
        where = f"version-files[{index}]"
        if not isinstance(entry, dict):
            problems.append(f'{where}: must be a mapping with "path" and "template"')
            continue
        unknown_keys(entry, VERSION_FILE_KEYS, f"{where}.", problems)
        path = require_string(entry, "path", problems, f"{where}.")
        template = require_string(entry, "template", problems, f"{where}.")
        if path is not None:
            problems.extend(
                f'{where}.path: "{path}" {problem}' for problem in check_destination(path, allow_hidden=True)
            )
        if template is not None:
            found = check_template(template, ["version", "tag"])
            if "{version}" not in template and "{tag}" not in template:
                found.append('must contain "{version}" or "{tag}"')
            if "\n" in template or "\r" in template:
                found.append("must be a single line")
            problems.extend(f'{where}.template: "{template}" {problem}' for problem in found)
        if path is None or template is None:
            continue
        if (path, template) in seen:
            problems.append(f"{where}: duplicate entry")
        seen.add((path, template))
        version_files.append(VersionFile(path, template))
    return version_files


def _check_asset_collisions(assets: list[AssetConfig], problems: Problems) -> None:
    seen_matchers: dict[tuple[str, str, str], str] = {}
    seen_destinations: dict[str, str] = {}
    for asset in assets:
        # The same archive may be listed several times to extract different members.
        matcher_key = (asset.match.kind, asset.match.template, asset.extract)
        if matcher_key in seen_matchers:
            problems.append(
                f"assets.{asset.id}: ambiguous — same {asset.match.kind} as assets.{seen_matchers[matcher_key]}"
            )
        seen_matchers[matcher_key] = asset.id
        # Case-insensitive: a checkout on Windows or macOS would collide otherwise.
        destination = asset.destination.lower()
        for existing, owner in seen_destinations.items():
            if existing == destination:
                problems.append(
                    f'assets.{asset.id}.destination: duplicate destination "{asset.destination}" '
                    f"(also used by assets.{owner})"
                )
            elif existing.startswith(destination + "/") or destination.startswith(existing + "/"):
                problems.append(
                    f'assets.{asset.id}.destination: "{asset.destination}" conflicts with assets.{owner} '
                    "(one is inside the other)"
                )
        seen_destinations[destination] = asset.id


def _validate_matcher(entry: dict[str, Any], where: str, problems: Problems) -> AssetMatcher | None:
    given = [key for key in MATCHER_KEYS if entry.get(key) is not None]
    if len(given) > 1:
        problems.append(f'{where}: set only one of "name", "pattern", "url" or "path" (found {", ".join(given)})')
        return None
    if not given:
        problems.append(
            f'{where}: one of "name" (exact release asset name), "pattern", "url" or "path" '
            "(a file committed in the source repository) is required"
        )
        return None
    kind = given[0]
    template = entry[kind]
    if not isinstance(template, str) or not template:
        problems.append(f"{where}.{kind}: must be a non-empty string" + quote_hint(template))
        return None
    if kind == "name":
        found = check_template(template, ["version", "tag"])
        if not FILE_NAME_TEXT.match(template):
            found.append('must be a plain file name (no "/", "\\", "*" or "?"; use "pattern" for wildcards)')
    elif kind == "url":
        found = check_url_template(template, ["version", "tag"])
    elif kind == "path":
        found = _repository_path_problems(template, ["version", "tag"])
    else:
        found = _pattern_problems(template)
    problems.extend(f'{where}.{kind}: "{template}" {problem}' for problem in found)
    return None if found else AssetMatcher(cast(Any, kind), template)


def _pattern_problems(pattern: str) -> list[str]:
    found = check_template(pattern, ["version", "tag"])
    without_wildcards = re.sub(r"[*?]", "", pattern)
    if not FILE_NAME_TEXT.match(without_wildcards or "x"):
        found.append('may only use "*" and "?" as wildcards and must not contain "/" or "\\"')
    if not re.search(r"[*?]", pattern):
        found.append('contains no wildcard; use "name" for exact matches')
    if "**" in pattern:
        found.append('must not contain "**"')
    literal_text = re.sub(r"\{(version|tag)\}", "", without_wildcards)
    if len(re.sub(r"[._-]", "", literal_text)) < 3:
        found.append("is too broad; include literal text identifying the platform/architecture/extension")
    return found
