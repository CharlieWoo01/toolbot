"""schema/tool.schema.json is an editor aid; the validator is authoritative. Keep them aligned."""

from __future__ import annotations

import json
from pathlib import Path

from toolbot.config.validate import ASSET_KEYS, CHECKSUM_KEYS, CHECKSUM_TYPES, SOURCE_KEYS, TOOL_KEYS
from toolbot.http import HttpClient
from toolbot.providers.source import create_source_providers
from toolbot.versions import SCHEMES

SCHEMA = json.loads((Path(__file__).resolve().parent.parent / "schema" / "tool.schema.json").read_text())


def keys(node: dict) -> list[str]:  # type: ignore[type-arg]
    return sorted(node["properties"])


def test_schema_matches_validator() -> None:
    assert keys(SCHEMA) == sorted(TOOL_KEYS)
    assert keys(SCHEMA["properties"]["source"]) == sorted(SOURCE_KEYS)
    assert keys(SCHEMA["$defs"]["asset"]) == sorted(ASSET_KEYS)
    assert keys(SCHEMA["$defs"]["checksum"]) == sorted(CHECKSUM_KEYS)
    assert SCHEMA["$defs"]["checksum"]["properties"]["type"]["enum"] == list(CHECKSUM_TYPES)
    assert SCHEMA["properties"]["versioning"]["enum"] == list(SCHEMES)
    providers = create_source_providers(HttpClient(), {})
    assert SCHEMA["properties"]["source"]["properties"]["provider"]["enum"] == providers.ids()
