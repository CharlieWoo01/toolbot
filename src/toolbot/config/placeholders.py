"""Minimal placeholder substitution: ``{version}``, ``{tag}`` and ``{asset}``.

Deliberately not a templating language. Any ``{`` or ``}`` that is not part of
a known placeholder is a validation error, so a typo such as ``{verison}``
fails loudly instead of being used literally.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


def check_template(template: str, allowed: Sequence[str]) -> list[str]:
    problems: list[str] = []
    for match in _PLACEHOLDER.finditer(template):
        name = match.group(1)
        if name not in allowed:
            permitted = ", ".join(f"{{{a}}}" for a in allowed) or "none"
            problems.append(f'unknown placeholder "{{{name}}}" (allowed: {permitted})')
    if re.search(r"[{}]", _PLACEHOLDER.sub("", template)):
        problems.append('unbalanced "{" or "}"')
    return problems


def render(template: str, values: Mapping[str, str]) -> str:
    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise KeyError(f"placeholder {{{name}}} has no value")  # unreachable for validated config
        return values[name]

    return _PLACEHOLDER.sub(substitute, template)


def count_placeholder(template: str, name: str) -> int:
    return template.count(f"{{{name}}}")
