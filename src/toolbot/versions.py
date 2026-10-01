"""Version parsing, ordering, tag handling and ignore constraints.

Tags and versions are kept apart: a *tag* is what upstream publishes
(``v1.4.0``, ``cli-v1.4.0``); the *version* is what is compared and stored in
``tool.yaml`` (``1.4.0``). The tool's ``tag`` template maps between them.

Two schemes, both backed by mature libraries rather than hand-written rules:

``semver`` (default)
    `python-semver <https://python-semver.readthedocs.io>`_. Full SemVer 2.0
    precedence (``1.9.0 < 1.10.0``, ``1.0.0-beta.2 < 1.0.0-beta.11 <
    1.0.0-rc.1 < 1.0.0``). Missing minor/patch (``1.2``) is accepted.

``pep440``
    `packaging <https://packaging.pypa.io>`_. For tools with four-part or
    otherwise non-SemVer versions (``2024.1.3.1``).
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import total_ordering
from typing import Any

import semver
from packaging.version import InvalidVersion as Pep440Invalid
from packaging.version import Version as Pep440Version

from toolbot.errors import ToolbotError

SCHEMES = ("semver", "pep440")

# Characters allowed in a version string at all (before scheme parsing). Keeps
# versions safe to use in branch names, file content and log output.
VERSION_TEXT = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$")


@total_ordering
@dataclass(frozen=True, eq=False)
class Version:
    raw: str
    """The version exactly as written (tag prefix/suffix already removed)."""
    scheme: str
    _key: Any
    prerelease: bool

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Version) and self.scheme == other.scheme and self._key == other._key

    def __lt__(self, other: Version) -> bool:
        if self.scheme != other.scheme:
            raise TypeError("cannot compare versions of different schemes")
        return bool(self._key < other._key)

    def __hash__(self) -> int:
        return hash((self.scheme, str(self._key)))

    def __str__(self) -> str:
        return self.raw


def parse_version(text: str, scheme: str = "semver") -> Version:
    """Parse ``text`` (a leading ``v`` is tolerated) or raise ``VERSION_INVALID``."""
    if not VERSION_TEXT.match(text):
        raise ToolbotError("VERSION_INVALID", f'"{text}" is not a valid version')
    candidate = text[1:] if text[:1] in ("v", "V") and text[1:2].isdigit() else text
    if scheme == "semver":
        try:
            parsed = semver.Version.parse(candidate, optional_minor_and_patch=True)
        except ValueError:
            raise ToolbotError("VERSION_INVALID", f'"{text}" is not a valid semantic version') from None
        # Build metadata does not affect precedence (SemVer §10).
        return Version(text, scheme, parsed.replace(build=None), parsed.prerelease is not None)
    if scheme == "pep440":
        try:
            pep = Pep440Version(candidate)
        except Pep440Invalid:
            raise ToolbotError("VERSION_INVALID", f'"{text}" is not a valid PEP 440 version') from None
        return Version(text, scheme, pep, pep.is_prerelease)
    raise ToolbotError("VERSION_INVALID", f'unknown versioning scheme "{scheme}" (supported: {", ".join(SCHEMES)})')


def version_from_tag(tag: str, template: str) -> str | None:
    """Extract the version text from ``tag`` using ``template`` (``v{version}``).

    Returns ``None`` for tags that don't follow the template, e.g. tags of
    other components in a monorepo.
    """
    prefix, _, suffix = template.partition("{version}")
    if not (tag.startswith(prefix) and tag.endswith(suffix)):
        return None
    middle = tag[len(prefix) : len(tag) - len(suffix) if suffix else None]
    return middle or None


def tag_for(version: str, template: str) -> str:
    return template.replace("{version}", version)


_CONSTRAINT = re.compile(r"^\s*(==|!=|>=|<=|>|<)?\s*([0-9A-Za-z][0-9A-Za-z.+_-]*)\s*$")
_COMPARISONS: dict[str, Callable[[Version, Version], bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
}


def check_constraint(spec: str, scheme: str) -> str | None:
    """Return a problem description if ``spec`` is not a valid constraint."""
    try:
        _parse_constraint(spec, scheme)
    except ToolbotError as error:
        return error.message
    return None


def matches(version: Version, spec: str) -> bool:
    """True if ``version`` satisfies every comma-separated part of ``spec``.

    ``"0.9.0"`` (exact), ``">=2.0.0"``, ``">=1.4.0, <1.5.0"``.
    """
    return all(
        _COMPARISONS[comparison](version, bound) for comparison, bound in _parse_constraint(spec, version.scheme)
    )


def _parse_constraint(spec: str, scheme: str) -> list[tuple[str, Version]]:
    parts = spec.split(",")
    if not spec.strip() or any(not part.strip() for part in parts):
        raise ToolbotError("VERSION_INVALID", f'"{spec}" is not a valid version constraint')
    result: list[tuple[str, Version]] = []
    for part in parts:
        constraint = _CONSTRAINT.match(part)
        if not constraint:
            raise ToolbotError(
                "VERSION_INVALID",
                f'"{spec}" is not a valid version constraint (use e.g. "1.2.3", ">=2.0.0" or ">=1.4, <1.5")',
            )
        result.append((constraint.group(1) or "==", parse_version(constraint.group(2), scheme)))
    return result
