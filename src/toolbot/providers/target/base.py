"""The target-provider contract: where Toolbot proposes updates.

A target provider owns every platform-specific operation on the repository
receiving updates: its Git URL and push credentials, and the lifecycle of
change requests (GitLab merge requests; GitHub pull requests in future).
The updater only ever talks to this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from toolbot.git import GitAuth

State = Literal["open", "closed", "merged"]


@dataclass(frozen=True)
class ChangeRequest:
    id: int
    """Platform number (GitLab MR iid)."""
    url: str
    state: State
    source_branch: str
    target_branch: str
    title: str

    @property
    def ref(self) -> str:
        return f"!{self.id}"


@dataclass(frozen=True)
class ChangeRequestSpec:
    source_branch: str
    target_branch: str
    title: str
    description: str
    labels: tuple[str, ...]


class TargetProvider(ABC):
    kind: str
    """Human name of a change request on this platform, e.g. "merge request"."""

    @abstractmethod
    def repository_url(self) -> str:
        """HTTPS Git URL of the target repository (no credentials)."""

    @abstractmethod
    def git_auth(self) -> GitAuth | None:
        """Credentials for pushing to ``repository_url``."""

    @abstractmethod
    def default_branch(self) -> str: ...

    @abstractmethod
    def find_by_branch(self, branch: str) -> list[ChangeRequest]:
        """Change requests (any state) from ``branch``, newest first."""

    @abstractmethod
    def list_open(self, branch_prefix: str, target_branch: str) -> list[ChangeRequest]:
        """Open change requests whose source branch starts with ``branch_prefix``.

        Callers verify the branch's head commit is Toolbot's before acting on it.
        """

    @abstractmethod
    def create(self, spec: ChangeRequestSpec) -> ChangeRequest:
        """Create a change request; if one is already open for the branch, return it instead."""

    @abstractmethod
    def close(self, request: ChangeRequest, comment: str) -> None: ...
