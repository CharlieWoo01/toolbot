"""GitLab as a target: merge requests via the REST API (v4).

The token (named by the host's ``token-env``) needs scope ``api`` and at least
the Developer role on each target project: it is used for the MR API and for
pushing ``toolbot/*`` branches over HTTPS.
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

MR_STATES: dict[str, State] = {"opened": "open", "closed": "closed", "merged": "merged", "locked": "closed"}
LABEL_COLOR = "#2f6f5e"
PAGE_SIZE = 100
MAX_PAGES = 20


def _ignore_warning(_message: str) -> None:
    return None


class GitLabApi:
    """Minimal authenticated client for one GitLab instance."""

    def __init__(self, http: HttpClient, host: Host, token: str) -> None:
        self.http = http
        self.host = host
        self.token = token

    def request_for(self, url: str) -> HttpRequest:
        return HttpRequest(url, {"private-token": self.token} if self.token else {})

    def project_url(self, project: str) -> str:
        return f"{self.host.api_url}/projects/{quote(project, safe='')}"

    def get_json(self, url: str, what: str) -> Any:
        return self.http.get_json(self.request_for(url), what)

    def get_list(self, url: str, params: Mapping[str, str | int], what: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        page = 1
        while True:
            response = self.http.request(
                "GET", self.request_for(url), what, params={**params, "per_page": PAGE_SIZE, "page": page}
            )
            items = response.json()
            if not isinstance(items, list):
                raise ToolbotError("TARGET_ERROR", f"{what}: unexpected GitLab API response")
            results.extend(items)
            next_page = response.headers.get("x-next-page", "")
            if not next_page.isdigit() or page >= MAX_PAGES:
                return results
            page = int(next_page)

    def list_group_repositories(self, group: str) -> list[RepositoryInfo]:
        """All projects in ``group`` and its subgroups."""
        projects = self.get_list(
            f"{self.host.api_url}/groups/{quote(group, safe='')}/projects",
            {"include_subgroups": "true", "archived": "false", "order_by": "path", "sort": "asc"},
            f"projects of GitLab group {group}",
        )
        return [
            RepositoryInfo(
                path=str(project["path_with_namespace"]),
                default_branch=project.get("default_branch"),
                archived=bool(project.get("archived")),
            )
            for project in projects
        ]

    def has_tools(self, project: str, branch: str) -> bool:
        url = f"{self.project_url(project)}/repository/tree"
        try:
            response = self.http.request(
                "GET",
                self.request_for(url),
                f"{TOOLS_DIR}/ in {project}",
                params={"path": TOOLS_DIR, "ref": branch, "per_page": 1},
            )
        except HttpError as error:
            if error.status == 404:
                return False
            raise
        return bool(response.json())


class GitLabTargetProvider(TargetProvider):
    kind = "merge request"

    def __init__(
        self,
        http: HttpClient,
        host: Host,
        project: str,
        token: str,
        warn: Callable[[str], None] = _ignore_warning,
    ) -> None:
        self.repository = project
        self._api = GitLabApi(http, host, token)
        self._project_url = self._api.project_url(project)
        self._warn = warn
        self._project_details: dict[str, Any] | None = None

    # -- repository --------------------------------------------------------------

    def repository_url(self) -> str:
        return f"{self._api.host.url}/{self.repository}.git"

    def git_auth(self) -> GitAuth:
        # GitLab accepts any username with an access token as the password.
        return GitAuth(self.repository_url(), "oauth2", self._api.token)

    def default_branch(self) -> str:
        if self._project_details is None:
            self._project_details = cast(
                dict[str, Any], self._api.get_json(self._project_url, f"GitLab project {self.repository}")
            )
        branch = self._project_details.get("default_branch")
        if not isinstance(branch, str) or not branch:
            raise ToolbotError("TARGET_ERROR", f"could not determine the default branch of {self.repository}")
        return branch

    # -- change requests -----------------------------------------------------------

    def find_by_branch(self, branch: str) -> list[ChangeRequest]:
        merge_requests = self._api.get_list(
            f"{self._project_url}/merge_requests",
            {"source_branch": branch, "state": "all", "order_by": "created_at", "sort": "desc"},
            f"merge requests of {self.repository}",
        )
        return [_change_request(item) for item in merge_requests]

    def list_open(self, branch_prefix: str, target_branch: str) -> list[ChangeRequest]:
        merge_requests = self._api.get_list(
            f"{self._project_url}/merge_requests",
            {"state": "opened", "target_branch": target_branch},
            f"open merge requests of {self.repository}",
        )
        return [
            _change_request(item)
            for item in merge_requests
            if str(item.get("source_branch", "")).startswith(branch_prefix)
        ]

    def create(self, spec: ChangeRequestSpec) -> ChangeRequest:
        labels = self._ensure_labels(spec.labels)
        body = {
            "source_branch": spec.source_branch,
            "target_branch": spec.target_branch,
            "title": spec.title,
            "description": spec.description,
            "labels": ",".join(labels),
            "remove_source_branch": True,
        }
        try:
            response = self._api.http.request(
                "POST", self._api.request_for(f"{self._project_url}/merge_requests"), "create merge request", json=body
            )
        except HttpError as error:
            if error.status == 409:  # "Another open merge request already exists for this source branch"
                already_open = [
                    request for request in self.find_by_branch(spec.source_branch) if request.state == "open"
                ]
                if already_open:
                    return already_open[0]
            raise ToolbotError("TARGET_ERROR", f"creating merge request failed: {error.message}") from None
        return _change_request(response.json())

    def close(self, request: ChangeRequest, comment: str) -> None:
        merge_request_url = f"{self._project_url}/merge_requests/{request.id}"
        self._api.http.request(
            "POST",
            self._api.request_for(f"{merge_request_url}/notes"),
            f"comment on {request.ref}",
            json={"body": comment},
        )
        self._api.http.request(
            "PUT", self._api.request_for(merge_request_url), f"close {request.ref}", json={"state_event": "close"}
        )

    def _ensure_labels(self, labels: tuple[str, ...]) -> list[str]:
        """Create missing labels; if that isn't permitted, drop them with a warning rather than fail."""
        usable_labels: list[str] = []
        for label in labels:
            try:
                existing = self._api.get_list(
                    f"{self._project_url}/labels",
                    {"search": label, "include_ancestor_groups": "true"},
                    f'label "{label}"',
                )
                if not any(item.get("name") == label for item in existing):
                    self._api.http.request(
                        "POST",
                        self._api.request_for(f"{self._project_url}/labels"),
                        f'create label "{label}"',
                        json={"name": label, "color": LABEL_COLOR},
                    )
                usable_labels.append(label)
            except ToolbotError as error:
                if isinstance(error, HttpError) and error.status == 409:  # created concurrently
                    usable_labels.append(label)
                    continue
                self._warn(
                    f'label "{label}" is missing and could not be created; MR created without it ({error.message})'
                )
        return usable_labels


def _change_request(item: Mapping[str, Any]) -> ChangeRequest:
    return ChangeRequest(
        id=int(item["iid"]),
        url=str(item.get("web_url", "")),
        state=MR_STATES.get(str(item.get("state")), "closed"),
        source_branch=str(item.get("source_branch", "")),
        target_branch=str(item.get("target_branch", "")),
        title=str(item.get("title", "")),
        reference_prefix="!",
    )
