"""The JSON schemas are editor aids; the validators are authoritative. Keep them aligned."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from toolbot.config.control import CONTROL_KEYS, DEFAULTS_KEYS, HOST_KEYS, RULE_KEYS, TARGET_KEYS
from toolbot.config.validate import (
    ASSET_KEYS,
    CHECKSUM_KEYS,
    CHECKSUM_TYPES,
    SOURCE_KEYS,
    TOOL_KEYS,
    VERSION_FILE_KEYS,
    VERSION_SOURCES,
)
from toolbot.hosts import HOST_TYPES, HostRegistry
from toolbot.http import HttpClient
from toolbot.providers.source import create_source_providers
from toolbot.versions import SCHEMES

SCHEMA_DIRECTORY = Path(__file__).resolve().parent.parent / "schema"


def load(name: str) -> Any:
    return json.loads((SCHEMA_DIRECTORY / name).read_text())


def keys(node: dict[str, Any]) -> list[str]:
    return sorted(node["properties"])


def test_tool_schema_matches_validator() -> None:
    schema = load("tool.schema.json")
    assert keys(schema) == sorted(TOOL_KEYS)
    assert keys(schema["properties"]["source"]) == sorted(SOURCE_KEYS)
    assert keys(schema["$defs"]["asset"]) == sorted(ASSET_KEYS)
    assert keys(schema["$defs"]["checksum"]) == sorted(CHECKSUM_KEYS)
    assert schema["$defs"]["checksum"]["properties"]["type"]["enum"] == list(CHECKSUM_TYPES)
    assert schema["properties"]["versioning"]["enum"] == list(SCHEMES)
    assert schema["properties"]["source"]["properties"]["versions"]["enum"] == list(VERSION_SOURCES)
    assert sorted(schema["properties"]["version-files"]["items"]["properties"]) == sorted(VERSION_FILE_KEYS)
    providers = create_source_providers(HttpClient(), HostRegistry([], {}))
    assert schema["properties"]["source"]["properties"]["provider"]["enum"] == providers.ids()


def test_control_schema_matches_validator() -> None:
    schema = load("toolbot.schema.json")
    assert keys(schema) == sorted(CONTROL_KEYS)
    assert sorted(schema["properties"]["hosts"]["additionalProperties"]["properties"]) == sorted(HOST_KEYS)
    assert schema["properties"]["hosts"]["additionalProperties"]["properties"]["type"]["enum"] == list(HOST_TYPES)
    assert keys(schema["properties"]["defaults"]) == sorted(DEFAULTS_KEYS)
    assert sorted(schema["properties"]["rules"]["items"]["properties"]) == sorted(RULE_KEYS)
    target_items = schema["properties"]["targets"]["additionalProperties"]["items"]
    assert sorted(target_items["properties"]) == sorted(TARGET_KEYS)
