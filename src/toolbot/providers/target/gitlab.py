"""GitLab as the target: merge requests via the REST API (v4).

Configuration (environment):

``TOOLBOT_GITLAB_TOKEN``
    Token of the Toolbot bot identity (project/group access token or a
    bot user's PAT) with scope ``api`` and at least the Developer role. Used
    for the MR API and for pushing branches over HTTPS.
``TOOLBOT_GITLAB_URL`` (default ``CI_SERVER_URL``)
    Base URL of the GitLab instance.
``TOOLBOT_GITLAB_PROJECT`` (default ``CI_PROJECT_PATH``)
    Project receiving updates.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast
from urllib.parse import quote

from toolbot.errors import ToolbotError
from toolbot.git import GitAuth
from toolbot.http import HttpClient, HttpError, HttpRequest
from toolbot.providers.target.base import ChangeRequest, ChangeRequestSpec, State, TargetProvider

_STATES: dict[str, State] = {"opened": "open", "closed": "closed", "merged": "merged", "locked": "closed"}
LABEL_COLOR = "#2f6f5e"


class GitLabTargetProvider(TargetProvider):
    kind = "merge request"

    def __init__(
        self, http: HttpClient, url: str, project: str, token: str, warn: Callable[[str], None] = lambda _m: None
    ) -> None:
        self._http = http
        self._warn = warn
        self._url = url.rstrip("/")
        self._project = project
        self._token = token
        self._api = f"{self._url}/api/v4/projects/{quote(project, safe='')}"
        self._project_info: dict[str, Any] | None = None

    @classmethod
    def from_env(
        cls, http: HttpClient, env: Mapping[str, str], warn: Callable[[str], None] = lambda _m: None
    ) -> GitLabTargetProvider:
        url = env.get("TOOLBOT_GITLAB_URL") or env.get("CI_SERVER_URL") or ""
        project = env.get("TOOLBOT_GITLAB_PROJECT") or env.get("CI_PROJECT_PATH") or ""
        token = env.get("TOOLBOT_GITLAB_TOKEN", "")
        missing = [
            name
            for name, value in (
                ("TOOLBOT_GITLAB_URL (or CI_SERVER_URL)", url),
                ("TOOLBOT_GITLAB_PROJECT (or CI_PROJECT_PATH)", project),
                ("TOOLBOT_GITLAB_TOKEN", token),
            )
            if not value
        ]
        if missing:
            raise ToolbotError("USAGE", f"GitLab target not configured: set {', '.join(missing)}")
        if not url.startswith("https://"):
            raise ToolbotError("USAGE", "TOOLBOT_GITLAB_URL must be an https:// URL")
        return cls(http, url, project, token, warn)

    # -- repository ------------------------------------------------------------

    def repository_url(self) -> str:
        return f"{self._url}/{self._project}.git"

    def git_auth(self) -> GitAuth:
        # GitLab accepts any username with an access token as the password.
        return GitAuth(self.repository_url(), "oauth2", self._token)

    def default_branch(self) -> str:
        branch = self._project_details().get("default_branch")
        if not isinstance(branch, str) or not branch:
            raise ToolbotError("TARGET_ERROR", f"could not determine the default branch of {self._project}")
        return branch

    # -- change requests -------------------------------------------------------

    def find_by_branch(self, branch: str) -> list[ChangeRequest]:
        items = self._get_list(
            f"{self._api}/merge_requests",
            {"source_branch": branch, "state": "all", "order_by": "created_at", "sort": "desc"},
        )
        return [_change_request(i) for i in items]

    def list_open(self, branch_prefix: str, target_branch: str) -> list[ChangeRequest]:
        items = self._get_list(
            f"{self._api}/merge_requests",
            {"state": "opened", "target_branch": target_branch},
        )
        return [_change_request(i) for i in items if str(i.get("source_branch", "")).startswith(branch_prefix)]

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
            response = self._http.request(
                "POST", self._req(f"{self._api}/merge_requests"), "create merge request", json=body
            )
        except HttpError as err:
            if err.status == 409:  # "Another open merge request already exists for this source branch"
                existing = [r for r in self.find_by_branch(spec.source_branch) if r.state == "open"]
                if existing:
                    return existing[0]
            raise ToolbotError("TARGET_ERROR", f"creating merge request failed: {err.message}") from None
        return _change_request(response.json())

    def close(self, request: ChangeRequest, comment: str) -> None:
        mr = f"{self._api}/merge_requests/{request.id}"
        self._http.request("POST", self._req(f"{mr}/notes"), f"comment on {request.ref}", json={"body": comment})
        self._http.request("PUT", self._req(mr), f"close {request.ref}", json={"state_event": "close"})

    # -- helpers ---------------------------------------------------------------

    def _ensure_labels(self, labels: tuple[str, ...]) -> list[str]:
        """Create missing labels; if that isn't permitted, drop them with a warning rather than fail."""
        usable: list[str] = []
        for label in labels:
            try:
                found = self._get_list(f"{self._api}/labels", {"search": label, "include_ancestor_groups": "true"})
                if not any(item.get("name") == label for item in found):
                    self._http.request(
                        "POST",
                        self._req(f"{self._api}/labels"),
                        f'create label "{label}"',
                        json={"name": label, "color": LABEL_COLOR},
                    )
                usable.append(label)
            except ToolbotError as err:
                if isinstance(err, HttpError) and err.status == 409:  # created concurrently
                    usable.append(label)
                    continue
                self._warn(
                    f'label "{label}" is missing and could not be created; MR created without it ({err.message})'
                )
        return usable

    def _project_details(self) -> dict[str, Any]:
        if self._project_info is None:
            self._project_info = cast(
                dict[str, Any], self._http.get_json(self._req(self._api), f"GitLab project {self._project}")
            )
        return self._project_info

    def _get_list(self, url: str, params: Mapping[str, str | int]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        page = 1
        while True:
            query = {**params, "per_page": 100, "page": page}
            response = self._http.request("GET", self._req(url), "GitLab API", params=query)
            items = response.json()
            if not isinstance(items, list):
                raise ToolbotError("TARGET_ERROR", "unexpected GitLab API response")
            results.extend(items)
            next_page = response.headers.get("x-next-page", "")
            if not next_page.isdigit() or page >= 20:
                return results
            page = int(next_page)

    def _req(self, url: str) -> HttpRequest:
        return HttpRequest(url, {"private-token": self._token})


def _change_request(item: Mapping[str, Any]) -> ChangeRequest:
    return ChangeRequest(
        id=int(item["iid"]),
        url=str(item.get("web_url", "")),
        state=_STATES.get(str(item.get("state")), "closed"),
        source_branch=str(item.get("source_branch", "")),
        target_branch=str(item.get("target_branch", "")),
        title=str(item.get("title", "")),
    )
