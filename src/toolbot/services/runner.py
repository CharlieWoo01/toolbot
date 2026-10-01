"""Running ``toolbot check`` across target repositories.

Each repository is processed independently: a repository that can't be
reached or read is reported and the others still run.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from toolbot.errors import ToolbotError
from toolbot.git import GitIdentity, Repository
from toolbot.providers.target.base import TargetProvider
from toolbot.services.context import Context
from toolbot.services.targets import TargetRepository, open_target
from toolbot.services.updater import Outcome, Updater, UpdateSettings

TargetFactory = Callable[[Context, TargetRepository], TargetProvider]


@dataclass
class RepositoryResult:
    repository: TargetRepository
    outcomes: list[Outcome] = field(default_factory=list)
    error: str | None = None
    """Set when the repository as a whole could not be processed."""


def git_identity(ctx: Context) -> GitIdentity:
    """Commit author: environment overrides toolbot.yaml, which overrides the built-in default."""
    defaults = ctx.control.defaults
    return GitIdentity(
        ctx.env.get("TOOLBOT_GIT_AUTHOR_NAME") or defaults.git_author_name,
        ctx.env.get("TOOLBOT_GIT_AUTHOR_EMAIL") or defaults.git_author_email,
    )


def change_request_labels(ctx: Context) -> tuple[str, ...]:
    labels_variable = ctx.env.get("TOOLBOT_MR_LABELS")
    if labels_variable is None:
        return ctx.control.defaults.labels
    return tuple(label.strip() for label in labels_variable.split(",") if label.strip())


def check_repositories(
    ctx: Context,
    repositories: Sequence[TargetRepository],
    only: Sequence[str] | None = None,
    dry_run: bool = False,
    base_branch_override: str | None = None,
    target_factory: TargetFactory = open_target,
) -> list[RepositoryResult]:
    identity = git_identity(ctx)
    labels = change_request_labels(ctx)
    results: list[RepositoryResult] = []
    for target_repository in repositories:
        result = RepositoryResult(target_repository)
        results.append(result)
        ctx.log(f"toolbot: {'checking' if not dry_run else 'dry run for'} {target_repository.label}")
        try:
            target = target_factory(ctx, target_repository)
            base_branch = (
                base_branch_override
                or target_repository.base_branch
                or ctx.control.defaults.base_branch
                or target.default_branch()
            )
            settings = UpdateSettings(
                base_branch=base_branch,
                labels=labels,
                repository_path=target_repository.path,
                dry_run=dry_run,
            )
            repository = Repository(target.repository_url(), identity, target.git_auth())
            result.outcomes = Updater(ctx, target, repository, settings, ctx.control.rules).run(only)
        except ToolbotError as error:
            result.error = error.message
    return results
