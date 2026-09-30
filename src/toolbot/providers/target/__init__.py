"""Target providers: where Toolbot raises update requests.

Only GitLab is implemented. A GitHub target (pull requests) would implement
``TargetProvider`` and be selected here by ``TOOLBOT_TARGET``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from toolbot.errors import ToolbotError
from toolbot.http import HttpClient
from toolbot.providers.target.base import ChangeRequest, ChangeRequestSpec, TargetProvider

TARGETS = ("gitlab",)


def create_target(
    http: HttpClient, env: Mapping[str, str], warn: Callable[[str], None] = lambda _m: None
) -> TargetProvider:
    kind = env.get("TOOLBOT_TARGET", "gitlab")
    if kind == "gitlab":
        from toolbot.providers.target.gitlab import GitLabTargetProvider

        return GitLabTargetProvider.from_env(http, env, warn)
    raise ToolbotError("USAGE", f'unsupported TOOLBOT_TARGET "{kind}" (supported: {", ".join(TARGETS)})')


__all__ = ["ChangeRequest", "ChangeRequestSpec", "TargetProvider", "create_target"]
