"""Target providers: where Toolbot raises update requests.

GitLab (merge requests) and GitHub (pull requests). Each platform module also
provides an ``…Api`` class used to discover repositories in a group or
organisation.
"""

from __future__ import annotations

from collections.abc import Callable

from toolbot.hosts import Host
from toolbot.http import HttpClient
from toolbot.providers.target.base import ChangeRequest, ChangeRequestSpec, RepositoryInfo, TargetProvider
from toolbot.providers.target.github import GitHubApi, GitHubTargetProvider
from toolbot.providers.target.gitlab import GitLabApi, GitLabTargetProvider


def create_target(
    http: HttpClient, host: Host, repository: str, token: str, warn: Callable[[str], None]
) -> TargetProvider:
    if host.type == "github":
        return GitHubTargetProvider(http, host, repository, token, warn)
    return GitLabTargetProvider(http, host, repository, token, warn)


def create_platform_api(http: HttpClient, host: Host, token: str) -> GitHubApi | GitLabApi:
    return GitHubApi(http, host, token) if host.type == "github" else GitLabApi(http, host, token)


__all__ = [
    "ChangeRequest",
    "ChangeRequestSpec",
    "RepositoryInfo",
    "TargetProvider",
    "create_platform_api",
    "create_target",
]
