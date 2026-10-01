"""Branch names, commit messages and merge request text."""

from __future__ import annotations

import json

from toolbot.config.models import ToolDefinition
from toolbot.git import check_branch_name
from toolbot.services.artifacts import ApplyResult

BRANCH_PREFIX = "toolbot"
TRAILER_TOOL = "Toolbot-Tool"
TRAILER_VERSION = "Toolbot-Version"
MARKER = "<!-- toolbot:"
NOTES_MAX_CHARS = 3000
NOTES_MAX_LINES = 60


def branch_prefix(tool: str) -> str:
    return f"{BRANCH_PREFIX}/{tool}/"


def branch_name(tool: str, version: str) -> str:
    """``toolbot/<tool>/<version>``: deterministic, one per tool + version.

    Tool names and versions are already restricted to safe characters by
    validation; git's own ref rules are checked as a final guard.
    """
    branch = f"{branch_prefix(tool)}{version}"
    check_branch_name(branch)
    return branch


def version_from_branch(tool: str, branch: str) -> str | None:
    prefix = branch_prefix(tool)
    return branch[len(prefix) :] if branch.startswith(prefix) else None


def title(tool: str, version: str) -> str:
    return f"chore(tools): update {tool} to {version}"


def commit_message(tool: str, previous: str, version: str) -> str:
    return (
        f"{title(tool, version)}\n\n"
        f"Update {tool} from {previous} to {version}.\n\n"
        f"{TRAILER_TOOL}: {tool}\n"
        f"{TRAILER_VERSION}: {version}\n"
    )


def description(tool: ToolDefinition, result: ApplyResult, superseding: list[str]) -> str:
    source_url = f"{tool.source.host}/{tool.source.repository}"
    release_link = f"[{result.tag}]({result.release.url})" if result.release.url else f"`{result.tag}`"
    lines = [
        f"Toolbot update for **{tool.name}**.",
        "",
        "| | |",
        "| --- | --- |",
        f"| Tool | `{tool.name}` |",
        f"| Previous | `{result.previous_version}` |",
        f"| New | `{result.version}` |",
        f"| Source | [{tool.source.provider}:{tool.source.repository}]({source_url}) |",
        f"| Release | {release_link} |",
        "",
        "### Assets",
        "",
        "| Asset | Upstream file | Size | SHA-256 | Verification |",
        "| --- | --- | --- | --- | --- |",
    ]
    for asset in result.assets:
        lines.append(
            f"| `{asset.id}` | `{asset.upstream}` | {asset.size:,} B | `{asset.sha256[:16]}…` | "
            f"{_verification(asset.verified_by)} |"
        )
    if not result.assets:
        lines = lines[:-4]  # version pins only: no assets table
    if lfs := [asset.destination for asset in result.assets if asset.storage == "lfs"]:
        lines += ["", "Stored with **Git LFS** (too large for a plain commit): " + ", ".join(f"`{p}`" for p in lfs)]
    if fetched := [asset.destination for asset in result.assets if asset.storage == "fetch"]:
        lines += [
            "",
            "**Not committed** (too large); pinned in `tool.lock.json` and restored with `toolbot fetch`: "
            + ", ".join(f"`{path}`" for path in fetched),
        ]
    if result.version_files:
        lines += ["", "### Version pins", ""]
        lines += [f"- `{path}`" for path in result.version_files]
    if superseding:
        lines += ["", f"Supersedes {', '.join(superseding)}."]
    notes = _notes_excerpt(result.release.notes)
    if notes:
        lines += [
            "",
            "<details><summary>Release notes (excerpt)</summary>",
            "",
            "```text",
            notes,
            "```",
            "",
            f"Full notes: {result.release.url}" if result.release.url else "",
            "</details>",
        ]
    lines += [
        "",
        "---",
        f"Created by Toolbot. Closing this merge request without merging tells Toolbot not to propose "
        f"{tool.name} {result.version} again; newer versions will still be proposed. To skip versions "
        f"permanently, add them to `ignore` in `tools/{tool.name}/tool.yaml`.",
        "",
        f"{MARKER} {json.dumps({'tool': tool.name, 'from': result.previous_version, 'to': result.version})} -->",
    ]
    return "\n".join(lines)


def _verification(verified_by: str) -> str:
    if verified_by == "none":
        return "⚠️ not verified"
    if verified_by == "provider-digest":
        return "✅ provider digest"
    algorithm, _, file = verified_by.partition(":")
    return f"✅ {algorithm.upper()} (`{file}`)"


def _notes_excerpt(notes: str) -> str:
    """Upstream notes, shortened and fenced as plain text.

    Fencing prevents upstream text from @-mentioning users or referencing
    issues/MRs in the target GitLab.
    """
    # Swap fence backticks for look-alikes so upstream text cannot close the fence.
    text = notes.strip().replace("```", "ʼ" * 3)  # noqa: RUF001
    if not text:
        return ""
    lines = text.splitlines()
    truncated = len(lines) > NOTES_MAX_LINES or len(text) > NOTES_MAX_CHARS
    text = "\n".join(lines[:NOTES_MAX_LINES])[:NOTES_MAX_CHARS]
    return text + ("\n…" if truncated else "")
