"""GitHub as a target: pull requests via the REST API.

Works with github.com and GitHub Enterprise Server. The token (named by the
host's ``token-env``) must be able to push branches and manage pull requests:
a GitHub App installation token or a fine-grained token with *Contents*,
*Pull requests* and *Issues* read & write (Issues is used for labels).

Pull requests opened with the workflow's own ``GITHUB_TOKEN`` do not trigger
other workflows, so ``toolbot verify`` would not run on them; use an App.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast
from urllib.parse import quote

from toolbot.errors import ToolbotError
from toolbot.git import GitAuth
from toolbot.hosts import Host
from toolbot.http import HttpClient, HttpError, HttpRequest
from toolbot.paths import TOOLS_DIR
from toolbot.providers.target.base import (
    ChangeRequest,
    ChangeRequestSpec,
    RepositoryInfo,
    State,
    TargetProvider,
)

PAGE_SIZE = 100
MAX_PAGES = 20


def _ignore_warning(_message: str) -> None:
    return None


class GitHubApi:
    """Minimal authenticated client for one GitHub instance."""

    def __init__(self, http: HttpClient, host: Host, token: str) -> None:
        self.http = http
        self.host = host
        self.token = token

    def request_for(self, url: str) -> HttpRequest:
        headers = {"accept": "application/vnd.github+json", "x-github-api-version": "2022-11-28"}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        return HttpRequest(url, headers)

    def get_json(self, url: str, what: str) -> Any:
        return self.http.get_json(self.request_for(url), what)

    def get_list(self, url: str, params: Mapping[str, str | int], what: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            response = self.http.request(
                "GET", self.request_for(url), what, params={**params, "per_page": PAGE_SIZE, "page": page}
            )
            items = response.json()
            if not isinstance(items, list):
                raise ToolbotError("TARGET_ERROR", f"{what}: unexpected GitHub API response")
            results.extend(items)
            if len(items) < PAGE_SIZE:
                break
        return results

    def list_group_repositories(self, organisation: str) -> list[RepositoryInfo]:
        """All repositories of an organisation (or, failing that, a user)."""
        try:
            repositories = self.get_list(
                f"{self.host.api_url}/orgs/{quote(organisation, safe='')}/repos",
                {"type": "all", "sort": "full_name"},
                f"repositories of GitHub organisation {organisation}",
            )
        except HttpError as error:
            if error.status != 404:
                raise
            repositories = self.get_list(
                f"{self.host.api_url}/users/{quote(organisation, safe='')}/repos",
                {"type": "owner", "sort": "full_name"},
                f"repositories of GitHub user {organisation}",
            )
        return [
            RepositoryInfo(
                path=str(repository["full_name"]),
                default_branch=repository.get("default_branch"),
                archived=bool(repository.get("archived")),
            )
            for repository in repositories
        ]

    def has_tools(self, repository: str, branch: str) -> bool:
        url = f"{self.host.api_url}/repos/{repository}/contents/{TOOLS_DIR}?ref={quote(branch, safe='')}"
        try:
            contents = self.get_json(url, f"{TOOLS_DIR}/ in {repository}")
        except HttpError as error:
            if error.status == 404:
                return False
            raise
        return isinstance(contents, list) and bool(contents)


class GitHubTargetProvider(TargetProvider):
    kind = "pull request"

    def __init__(
        self,
        http: HttpClient,
        host: Host,
        repository: str,
        token: str,
        warn: Callable[[str], None] = _ignore_warning,
    ) -> None:
        self.repository = repository
        self._owner = repository.split("/", 1)[0]
        self._api = GitHubApi(http, host, token)
        self._repository_api = f"{host.api_url}/repos/{repository}"
        self._warn = warn
        self._repository_details: dict[str, Any] | None = None

    def repository_url(self) -> str:
        return f"{self._api.host.url}/{self.repository}.git"

    def git_auth(self) -> GitAuth:
        return GitAuth(self.repository_url(), "x-access-token", self._api.token)

    def default_branch(self) -> str:
        if self._repository_details is None:
            self._repository_details = cast(
                dict[str, Any], self._api.get_json(self._repository_api, f"GitHub repository {self.repository}")
            )
        branch = self._repository_details.get("default_branch")
        if not isinstance(branch, str) or not branch:
            raise ToolbotError("TARGET_ERROR", f"could not determine the default branch of {self.repository}")
        return branch

    def find_by_branch(self, branch: str) -> list[ChangeRequest]:
        pulls = self._api.get_list(
            f"{self._repository_api}/pulls",
            {"head": f"{self._owner}:{branch}", "state": "all", "sort": "created", "direction": "desc"},
            f"pull requests of {self.repository}",
        )
        return [_change_request(pull) for pull in pulls]

    def list_open(self, branch_prefix: str, target_branch: str) -> list[ChangeRequest]:
        pulls = self._api.get_list(
            f"{self._repository_api}/pulls",
            {"state": "open", "base": target_branch},
            f"open pull requests of {self.repository}",
        )
        return [
            _change_request(pull)
            for pull in pulls
            # Only branches in this repository, never forks.
            if str(pull["head"]["ref"]).startswith(branch_prefix)
            and (pull["head"].get("repo") or {}).get("full_name") == self.repository
        ]

    def create(self, spec: ChangeRequestSpec) -> ChangeRequest:
        body = {"title": spec.title, "head": spec.source_branch, "base": spec.target_branch, "body": spec.description}
        try:
            response = self._api.http.request(
                "POST", self._api.request_for(f"{self._repository_api}/pulls"), "create pull request", json=body
            )
        except HttpError as error:
            if error.status == 422:  # "A pull request already exists for …"
                already_open = [
                    request for request in self.find_by_branch(spec.source_branch) if request.state == "open"
                ]
                if already_open:
                    return already_open[0]
            raise ToolbotError("TARGET_ERROR", f"creating pull request failed: {error.message}") from None
        pull_request = _change_request(response.json())
        if spec.labels:
            try:
                # GitHub creates missing labels automatically when they are added.
                self._api.http.request(
                    "POST",
                    self._api.request_for(f"{self._repository_api}/issues/{pull_request.id}/labels"),
                    f"label {pull_request.ref}",
                    json={"labels": list(spec.labels)},
                )
            except ToolbotError as error:
                self._warn(f"could not add labels to {pull_request.ref}: {error.message}")
        return pull_request

    def close(self, request: ChangeRequest, comment: str) -> None:
        self._api.http.request(
            "POST",
            self._api.request_for(f"{self._repository_api}/issues/{request.id}/comments"),
            f"comment on {request.ref}",
            json={"body": comment},
        )
        self._api.http.request(
            "PATCH",
            self._api.request_for(f"{self._repository_api}/pulls/{request.id}"),
            f"close {request.ref}",
            json={"state": "closed"},
        )


def _change_request(pull: Mapping[str, Any]) -> ChangeRequest:
    state: State = "merged" if pull.get("merged_at") else ("open" if pull.get("state") == "open" else "closed")
    return ChangeRequest(
        id=int(pull["number"]),
        url=str(pull.get("html_url", "")),
        state=state,
        source_branch=str(pull["head"]["ref"]),
        target_branch=str(pull["base"]["ref"]),
        title=str(pull.get("title", "")),
        reference_prefix="#",
    )
