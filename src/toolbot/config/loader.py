"""Loading tool definitions from ``tools/<name>/tool.yaml``."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from toolbot.config.models import ToolDefinition
from toolbot.config.validate import validate_definition
from toolbot.errors import ConfigValidationError, ToolbotError
from toolbot.paths import DEFINITION_FILE, TOOL_NAME, TOOLS_DIR, tool_directory
from toolbot.providers.source import SourceProviders


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that also rejects duplicate keys and aliases.

    Aliases are legal YAML but add nothing to tool definitions and enable
    "billion laughs" expansion; duplicate keys usually hide a mistake.
    """

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            event = self.peek_event()  # type: ignore[no-untyped-call]
            raise yaml.constructor.ConstructorError(
                None, None, "YAML aliases (*name) are not allowed", event.start_mark
            )
        return super().compose_node(parent, index)

    def construct_mapping(self, node: Any, deep: bool = False) -> Any:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark, f'duplicate key "{key}"', key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def parse_yaml(text: str, source: str) -> Any:
    try:
        return yaml.load(text, Loader=_StrictLoader)  # noqa: S506 - _StrictLoader derives from SafeLoader
    except yaml.YAMLError as err:
        problem = getattr(err, "problem", None) or (str(err).splitlines() or ["invalid YAML"])[0]
        mark = getattr(err, "problem_mark", None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark is not None else ""
        raise ConfigValidationError(source, [f"{problem}{where}"]) from None


def load_tool(root: Path, name: str, providers: SourceProviders) -> ToolDefinition:
    directory = tool_directory(root, name)
    file = directory / DEFINITION_FILE
    try:
        text = file.read_text(encoding="utf-8")
    except OSError:
        raise ToolbotError(
            "CONFIG_NOT_FOUND", f'no definition for tool "{name}" (expected {TOOLS_DIR}/{name}/{DEFINITION_FILE})'
        ) from None
    return validate_definition(parse_yaml(text, str(file)), file, directory, providers, expected_name=name)


def list_tool_names(root: Path) -> list[str]:
    tools = root / TOOLS_DIR
    if not tools.is_dir():
        return []
    return sorted(p.name for p in tools.iterdir() if (p / DEFINITION_FILE).is_file())


@dataclass
class LoadResult:
    tools: list[ToolDefinition] = field(default_factory=list)
    errors: dict[str, ToolbotError] = field(default_factory=dict)


def load_all(root: Path, providers: SourceProviders, only: list[str] | None = None) -> LoadResult:
    """Load every definition (or ``only`` these), collecting failures per tool."""
    result = LoadResult()
    names = list_tool_names(root)
    if only:
        missing = [n for n in only if n not in names]
        for n in missing:
            result.errors[n] = ToolbotError("CONFIG_NOT_FOUND", f'no tool named "{n}" in {TOOLS_DIR}/')
        names = [n for n in names if n in only]
    for name in names:
        try:
            if not TOOL_NAME.match(name):
                raise ToolbotError(
                    "CONFIG_INVALID", f'{TOOLS_DIR}/{name}: directory name must be lowercase letters, digits and "-"'
                )
            result.tools.append(load_tool(root, name, providers))
        except ToolbotError as err:
            result.errors[name] = err
    return result


_VERSION_LINE = re.compile(r"""^(version:[ \t]*)(["']?)([^"'\s#]*)\2([ \t]*(?:#.*)?)$""", re.MULTILINE)


def with_version(text: str, source: str, version: str) -> str:
    """Rewrite the top-level ``version:`` line, preserving comments and layout."""
    if not _VERSION_LINE.search(text):
        raise ToolbotError("CONFIG_INVALID", f'{source}: could not find a top-level "version:" line to update')

    def replace(m: re.Match[str]) -> str:
        quote = m.group(2) or '"'
        return f"{m.group(1)}{quote}{version}{quote}{m.group(4)}"

    updated = _VERSION_LINE.sub(replace, text, count=1)
    parsed = parse_yaml(updated, source)
    if not isinstance(parsed, dict) or parsed.get("version") != version:
        raise ToolbotError("CONFIG_INVALID", f"{source}: failed to update version safely")
    return updated
