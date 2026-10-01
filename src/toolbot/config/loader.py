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
from toolbot.paths import DEFINITION_FILE, TOOL_NAME, TOOLS_DIR, is_repository_relative, tool_directory
from toolbot.providers.source import SourceProviders


class StrictYamlLoader(yaml.SafeLoader):
    """SafeLoader that also rejects duplicate keys and aliases.

    Aliases are legal YAML but add nothing to configuration and enable
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
        seen_keys: set[Any] = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen_keys:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark, f'duplicate key "{key}"', key_node.start_mark
                )
            seen_keys.add(key)
        return super().construct_mapping(node, deep=deep)


def parse_yaml(text: str, source: str) -> Any:
    try:
        return yaml.load(text, Loader=StrictYamlLoader)  # noqa: S506 - StrictYamlLoader derives from SafeLoader
    except yaml.YAMLError as error:
        problem = getattr(error, "problem", None) or (str(error).splitlines() or ["invalid YAML"])[0]
        mark = getattr(error, "problem_mark", None)
        location = f" (line {mark.line + 1}, column {mark.column + 1})" if mark is not None else ""
        raise ConfigValidationError(source, [f"{problem}{location}"]) from None


def load_tool(root: Path, name: str, providers: SourceProviders) -> ToolDefinition:
    directory = tool_directory(root, name)
    definition_file = directory / DEFINITION_FILE
    try:
        text = definition_file.read_text(encoding="utf-8")
    except OSError:
        raise ToolbotError(
            "CONFIG_NOT_FOUND", f'no definition for tool "{name}" (expected {TOOLS_DIR}/{name}/{DEFINITION_FILE})'
        ) from None
    return validate_definition(
        parse_yaml(text, str(definition_file)),
        definition_file,
        directory,
        providers,
        providers.hosts,
        expected_name=name,
    )


def list_tool_names(root: Path) -> list[str]:
    tools_directory = root / TOOLS_DIR
    if not tools_directory.is_dir():
        return []
    return sorted(entry.name for entry in tools_directory.iterdir() if (entry / DEFINITION_FILE).is_file())


@dataclass
class LoadResult:
    tools: list[ToolDefinition] = field(default_factory=list)
    errors: dict[str, ToolbotError] = field(default_factory=dict)


def load_all(root: Path, providers: SourceProviders, only: list[str] | None = None) -> LoadResult:
    """Load every definition (or ``only`` these), collecting failures per tool."""
    result = LoadResult()
    names = list_tool_names(root)
    if only:
        for missing_name in (name for name in only if name not in names):
            result.errors[missing_name] = ToolbotError(
                "CONFIG_NOT_FOUND", f'no tool named "{missing_name}" in {TOOLS_DIR}/'
            )
        names = [name for name in names if name in only]
    for name in names:
        try:
            if not TOOL_NAME.match(name):
                raise ToolbotError(
                    "CONFIG_INVALID", f'{TOOLS_DIR}/{name}: directory name must be lowercase letters, digits and "-"'
                )
            result.tools.append(load_tool(root, name, providers))
        except ToolbotError as error:
            result.errors[name] = error
    _reject_shared_root_paths(result)
    return result


def _reject_shared_root_paths(result: LoadResult) -> None:
    """Two tools committing the same repository-root file (``/bin/x``) would fight over it.

    Version files may be shared (one Dockerfile pinning several tools).
    """
    owners: dict[str, str] = {}
    for tool in list(result.tools):
        paths = [asset.destination for asset in tool.assets]
        for path in (path.lower() for path in paths if is_repository_relative(path)):
            owner = owners.setdefault(path, tool.name)
            if owner != tool.name:
                result.errors[tool.name] = ToolbotError(
                    "CONFIG_INVALID", f'{TOOLS_DIR}/{tool.name}: "{path}" is also written by tool "{owner}"'
                )
                result.tools.remove(tool)
                break


VERSION_LINE = re.compile(r"""^(version:[ \t]*)(["']?)([^"'\s#]*)\2([ \t]*(?:#.*)?)$""", re.MULTILINE)


def with_version(text: str, source: str, version: str) -> str:
    """Rewrite the top-level ``version:`` line, preserving comments and layout."""
    if not VERSION_LINE.search(text):
        raise ToolbotError("CONFIG_INVALID", f'{source}: could not find a top-level "version:" line to update')

    def replace_version(match: re.Match[str]) -> str:
        quote = match.group(2) or '"'
        return f"{match.group(1)}{quote}{version}{quote}{match.group(4)}"

    updated = VERSION_LINE.sub(replace_version, text, count=1)
    parsed = parse_yaml(updated, source)
    if not isinstance(parsed, dict) or parsed.get("version") != version:
        raise ToolbotError("CONFIG_INVALID", f"{source}: failed to update version safely")
    return updated
