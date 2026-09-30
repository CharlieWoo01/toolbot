from __future__ import annotations

import pytest

from toolbot.errors import ToolbotError
from toolbot.services import messages


def test_branch_names_are_deterministic_and_validated() -> None:
    assert messages.branch_name("pact-broker-cli", "0.9.0") == "toolbot/pact-broker-cli/0.9.0"
    assert messages.version_from_branch("pact-broker-cli", "toolbot/pact-broker-cli/0.9.0") == "0.9.0"
    assert messages.version_from_branch("pact-broker-cli", "toolbot/other/0.9.0") is None
    with pytest.raises(ToolbotError):
        messages.branch_name("x", "1..2")


def test_commit_message_is_identifiable() -> None:
    text = messages.commit_message("widget", "1.0.0", "2.0.0")
    assert text.splitlines()[0] == "chore(tools): update widget to 2.0.0"
    assert "Update widget from 1.0.0 to 2.0.0." in text
    assert text.rstrip().endswith("Toolbot-Tool: widget\nToolbot-Version: 2.0.0")


def test_release_notes_cannot_escape_the_fence_and_are_truncated() -> None:
    hostile = "```\n@everyone see !12 and #34\n```\n" + "\n".join(f"line {i}" for i in range(200))
    excerpt = messages._notes_excerpt(hostile)
    assert "```" not in excerpt
    assert excerpt.endswith("…")
    assert len(excerpt.splitlines()) <= messages.NOTES_MAX_LINES + 1
    assert messages._notes_excerpt("   ") == ""
