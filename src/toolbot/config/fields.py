"""Small helpers shared by the tool-definition and control-config validators.

Each helper appends human-readable problems to a list instead of raising, so a
file's problems are all reported at once.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from toolbot.config.placeholders import check_template
from toolbot.hosts import normalise_url

Problems = list[str]


def unknown_keys(mapping: dict[str, Any], allowed: Sequence[str], prefix: str, problems: Problems) -> None:
    for key in mapping:
        if key not in allowed:
            problems.append(f"{prefix}{key}: unknown key (allowed: {', '.join(allowed)})")


def require_string(mapping: dict[str, Any], key: str, problems: Problems, prefix: str = "") -> str | None:
    value = mapping.get(key)
    if value is None or value == "":
        problems.append(f"{prefix}{key}: is required")
        return None
    if not isinstance(value, str):
        problems.append(f"{prefix}{key}: must be a string" + quote_hint(value))
        return None
    return value


def string_list(value: object, where: str, problems: Problems, example: str) -> list[str] | None:
    """A list of strings; a single string is accepted as a one-item list."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    problems.append(f"{where}: must be a list of quoted strings, e.g. {example}")
    return None


def optional_bool(mapping: dict[str, Any], key: str, where: str, problems: Problems) -> bool | None:
    if key not in mapping:
        return None
    value = mapping[key]
    if not isinstance(value, bool):
        problems.append(f"{where}{key}: must be true or false")
        return None
    return value


def check_url_template(template: str, allowed_placeholders: Sequence[str]) -> list[str]:
    """Problems with a download URL template such as ``https://example.com/{version}/tool.zip``."""
    found = check_template(template, allowed_placeholders)
    sample = template
    for name in allowed_placeholders:
        sample = sample.replace(f"{{{name}}}", "0")
    if normalise_url(sample.split("?", 1)[0]) is None:
        found.append("must be an https:// URL without credentials or fragment")
    return found


def quote_hint(value: object) -> str:
    """``tag: {version}`` is a YAML mapping, not a string; say so explicitly."""
    if isinstance(value, dict):
        return ' (values starting with "{" must be quoted in YAML, e.g. "{version}.zip")'
    return ""
