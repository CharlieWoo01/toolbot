"""Expected failures.

Everything Toolbot anticipates (bad config, missing release, checksum
mismatch, API errors...) is raised as ``ToolbotError`` so the CLI can print a
clean message and carry on with other tools. Anything else is a bug and is
reported with a traceback.
"""

from __future__ import annotations

from typing import Literal

ErrorCode = Literal[
    "CONFIG_INVALID",
    "CONFIG_NOT_FOUND",
    "USAGE",
    "RELEASE_NOT_FOUND",
    "ASSET_NOT_FOUND",
    "ASSET_AMBIGUOUS",
    "AUTH_FAILED",
    "RATE_LIMITED",
    "HTTP_ERROR",
    "CHECKSUM_MISMATCH",
    "CHECKSUM_UNAVAILABLE",
    "ARCHIVE_INVALID",
    "VERSION_FILE_MISMATCH",
    "UNSAFE_PATH",
    "VERIFY_FAILED",
    "VERSION_INVALID",
    "GIT_ERROR",
    "TARGET_ERROR",
]


class ToolbotError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message

    def __str__(self) -> str:
        return self.message


class ConfigValidationError(ToolbotError):
    """Carries every problem found in a definition, not just the first."""

    def __init__(self, source: str, problems: list[str]) -> None:
        detail = "\n".join(f"  - {problem}" for problem in problems)
        super().__init__("CONFIG_INVALID", f"{source} is invalid:\n{detail}")
        self.problems = problems
