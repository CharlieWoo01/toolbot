"""Which repositories to maintain.

Two ways to say it:

* **toolbot.yaml targets** (control repository / GitOps): named sets of
  single repositories and whole GitLab groups / GitHub organisations. Groups
  are expanded through the platform API; only repositories that contain a
  ``tools/`` directory on their base branch are kept.
* **The CI environment** (no targets configured): the repository the job runs
  in, from GitLab CI / GitHub Actions variables or ``TOOLBOT_*`` overrides.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from toolbot.config.control import TargetSpec
from toolbot.errors import ToolbotError
from toolbot.hosts import GITHUB_COM, Host, HostRegistry, normalise_url
from toolbot.providers.target import TargetProvider, create_platform_api, create_target
from toolbot.services.context import Context


@dataclass(frozen=True)
class TargetRepository:
    host: Host
    path: str
    base_branch: str | None = None
    """Explicit base branch from configuration; otherwise the repository default."""
    target_set: str | None = None

    @property
    def label(self) -> str:
        return f"{self.path} ({self.host.name})"


@dataclass
class TargetResolution:
    repositories: list[TargetRepository] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    """Groups/organisations that could not be listed (the rest still run)."""


def resolve_targets(ctx: Context, selected: Sequence[tuple[str, TargetSpec]]) -> TargetResolution:
    resolution = TargetResolution()
    seen: set[tuple[str, str]] = set()
    for set_name, spec in selected:
        host = ctx.hosts.get(spec.host)
        if host is None:  # validated earlier; defensive
            resolution.problems.append(f'unknown host "{spec.host}"')
            continue
        try:
            candidates = _expand(ctx, host, spec, set_name)
        except ToolbotError as error:
            resolution.problems.append(f"{spec.host}:{spec.group or spec.repository}: {error.message}")
            continue
        for candidate in candidates:
            key = (candidate.host.url, candidate.path)
            if key not in seen:
                seen.add(key)
                resolution.repositories.append(candidate)
    return resolution


def _expand(ctx: Context, host: Host, spec: TargetSpec, set_name: str) -> list[TargetRepository]:
    if spec.repository is not None:
        return [TargetRepository(host, spec.repository, spec.base_branch, set_name)]
    if spec.group is None:
        return []
    api = create_platform_api(ctx.http, host, ctx.hosts.token(host))
    found: list[TargetRepository] = []
    for repository in api.list_group_repositories(spec.group):
        if repository.archived or not repository.default_branch:
            continue  # archived or empty repositories can't receive updates
        if spec.include and not any(fnmatch.fnmatchcase(repository.path, pattern) for pattern in spec.include):
            continue
        if any(fnmatch.fnmatchcase(repository.path, pattern) for pattern in spec.exclude):
            continue
        branch = spec.base_branch or repository.default_branch
        if api.has_tools(repository.path, branch):
            found.append(TargetRepository(host, repository.path, spec.base_branch, set_name))
    return found


def environment_target(environment: Mapping[str, str], hosts: HostRegistry) -> TargetRepository:
    """The single repository Toolbot runs in, when toolbot.yaml defines no targets."""
    kind = environment.get("TOOLBOT_TARGET") or (
        "github" if environment.get("GITHUB_ACTIONS") == "true" and not environment.get("GITLAB_CI") else "gitlab"
    )
    if kind == "gitlab":
        url = normalise_url(environment.get("TOOLBOT_GITLAB_URL") or environment.get("CI_SERVER_URL") or "")
        path = environment.get("TOOLBOT_GITLAB_PROJECT") or environment.get("CI_PROJECT_PATH") or ""
        token_env = "TOOLBOT_GITLAB_TOKEN"  # noqa: S105 - variable name, not a secret
        missing = [
            name
            for name, value in (
                ("TOOLBOT_GITLAB_URL (or CI_SERVER_URL)", url),
                ("TOOLBOT_GITLAB_PROJECT (or CI_PROJECT_PATH)", path),
            )
            if not value
        ]
    elif kind == "github":
        url = normalise_url(environment.get("TOOLBOT_GITHUB_URL") or environment.get("GITHUB_SERVER_URL") or GITHUB_COM)
        path = environment.get("TOOLBOT_GITHUB_REPOSITORY") or environment.get("GITHUB_REPOSITORY") or ""
        token_env = "TOOLBOT_GITHUB_TOKEN"  # noqa: S105 - variable name, not a secret
        missing = [] if path else ["TOOLBOT_GITHUB_REPOSITORY (or GITHUB_REPOSITORY)"]
    else:
        raise ToolbotError("USAGE", f'unsupported TOOLBOT_TARGET "{kind}" (supported: gitlab, github)')
    if missing or not url:
        raise ToolbotError("USAGE", f"{kind} target not configured: set {', '.join(missing) or 'a valid URL'}")
    host = hosts.find(url) or Host(f"ci-{kind}", kind, url, token_env)
    return TargetRepository(host, path, environment.get("TOOLBOT_BASE_BRANCH") or None)


def open_target(ctx: Context, repository: TargetRepository) -> TargetProvider:
    token = ctx.hosts.token(repository.host)
    if not token:
        variable = repository.host.token_env or "a token-env for this host in toolbot.yaml"
        raise ToolbotError(
            "USAGE",
            f"no token for {repository.host.name} ({repository.host.url}): set {variable} "
            f"to a token that can push branches and open {'pull' if repository.host.type == 'github' else 'merge'} "
            "requests",
        )
    return create_target(ctx.http, repository.host, repository.path, token, ctx.warn)
