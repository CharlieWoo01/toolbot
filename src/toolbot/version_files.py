"""``version-files``: version pins in other files of the target repository.

For tools where nothing is downloaded (or not only): ``.terraform-version``,
a Dockerfile ``ARG``, a CI variable. Each entry is a file and a template such
as ``ARG TERRAFORM_VERSION={version}``. Toolbot renders the template for the
current version and replaces exactly that text with the template rendered for
the new version. No regular expressions from configuration, no guessing:

* the text for the current version must be present, otherwise the update
  fails (the file and ``tool.yaml`` disagree);
* where the template starts or ends with a placeholder, the match must not be
  part of a longer version (``1.2.0`` never matches inside ``11.2.0`` or
  ``1.2.0-rc.1``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from toolbot.config.models import ToolDefinition, VersionFile
from toolbot.config.placeholders import render
from toolbot.errors import ToolbotError
from toolbot.paths import repo_relative, resolve_destination
from toolbot.versions import tag_for

# Characters that would continue a version: "1.2.0" must not match in "11.2.0" or "1.2.0-rc.1".
_VERSION_CHARACTER = r"[0-9A-Za-z.+-]"


@dataclass(frozen=True)
class VersionFileChange:
    path: Path
    display: str
    """Repository-relative path."""
    content: str
    replacements: int


def pin_pattern(version_file: VersionFile, version: str, tag: str) -> re.Pattern[str]:
    text = render(version_file.template, {"version": version, "tag": tag})
    before = rf"(?<!{_VERSION_CHARACTER})" if version_file.template.startswith(("{version}", "{tag}")) else ""
    after = rf"(?!{_VERSION_CHARACTER})" if version_file.template.endswith(("{version}", "{tag}")) else ""
    return re.compile(before + re.escape(text) + after)


def read_text(path: Path) -> str:
    # newline="" keeps CRLF files CRLF when written back.
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def bump_version_files(tool: ToolDefinition, new_version: str) -> list[VersionFileChange]:
    """New contents for every version file, or an error naming the file that disagrees."""
    root = tool.root
    current_tag = tag_for(tool.version, tool.tag)
    new_tag = tag_for(new_version, tool.tag)
    changes: list[VersionFileChange] = []
    for version_file in tool.version_files:
        path = resolve_destination(tool.dir, version_file.path, create_parents=False, allow_hidden=True)
        display = repo_relative(root, path)
        if not path.is_file():
            raise ToolbotError("VERSION_FILE_MISMATCH", f"version-files: {display} does not exist")
        try:
            text = read_text(path)
        except UnicodeDecodeError:
            raise ToolbotError("VERSION_FILE_MISMATCH", f"version-files: {display} is not UTF-8 text") from None
        old_pattern = pin_pattern(version_file, tool.version, current_tag)
        new_text = render(version_file.template, {"version": new_version, "tag": new_tag})
        # Backslashes escaped: the new text is literal, never a regex replacement template.
        updated, count = old_pattern.subn(new_text.replace("\\", "\\\\"), text)
        if count == 0:
            if new_version != tool.version and pin_pattern(version_file, new_version, new_tag).search(text):
                continue  # already pinned to the new version
            expected = render(version_file.template, {"version": tool.version, "tag": current_tag})
            raise ToolbotError(
                "VERSION_FILE_MISMATCH",
                f'version-files: {display} does not contain "{expected}" (tool.yaml declares {tool.version}); '
                "fix the file or the template",
            )
        if updated != text:
            changes.append(VersionFileChange(path, display, updated, count))
    return changes


def check_version_files(tool: ToolDefinition) -> list[str]:
    """Offline: every version file pins the declared version (``toolbot verify``)."""
    problems: list[str] = []
    tag = tag_for(tool.version, tool.tag)
    for version_file in tool.version_files:
        try:
            path = resolve_destination(tool.dir, version_file.path, create_parents=False, allow_hidden=True)
            text = read_text(path) if path.is_file() else None
        except (ToolbotError, UnicodeDecodeError, OSError):
            text = None
        expected = render(version_file.template, {"version": tool.version, "tag": tag})
        if text is None:
            problems.append(f"version-files: {version_file.path} is missing or unreadable")
        elif not pin_pattern(version_file, tool.version, tag).search(text):
            problems.append(f'version-files: {version_file.path} does not contain "{expected}"')
    return problems
