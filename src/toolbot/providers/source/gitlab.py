"""GitLab.com and self-managed GitLab releases.

GitLab release "assets" are release links. They usually point at the
generic package registry but may point anywhere, so the Releases API is the
only reliable way to resolve them. Project paths with sub-groups are
URL-encoded as a single path parameter. GitLab publishes no asset digests.

Token: ``TOOLBOT_GITLAB_SOURCE_TOKEN`` if set; otherwise, for upstream
projects on the *same* instance as the target, the target token
``TOOLBOT_GITLAB_TOKEN`` is reused so one token covers both. Tokens are only
ever sent to the source host.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import quote

from toolbot.config.models import SourceConfig
from toolbot.errors import ToolbotError
from toolbot.http import HttpClient, HttpError, HttpRequest, same_origin
from toolbot.providers.source.base import Release, ReleaseAsset, SourceProvider

_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,254}$")
PAGE_SIZE = 100
MAX_PAGES = 5


class GitLabSourceProvider(SourceProvider):
    id = "gitlab"
    default_host = "https://gitlab.com"
    token_env = "TOOLBOT_GITLAB_SOURCE_TOKEN"  # noqa: S105 - the variable *name*, not a secret
    supports_digest = False

    def __init__(self, http: HttpClient, env: Mapping[str, str]) -> None:
        self._http = http
        self._source_token = env.get("TOOLBOT_GITLAB_SOURCE_TOKEN", "")
        self._target_token = env.get("TOOLBOT_GITLAB_TOKEN", "")
        self._target_host = env.get("TOOLBOT_GITLAB_URL") or env.get("CI_SERVER_URL") or ""

    def check_repository(self, repository: str) -> str | None:
        segments = repository.split("/")
        if len(segments) >= 2 and all(_SEGMENT.match(s) and s != ".." for s in segments):
            return None
        return 'must be a project path such as "group/project" or "group/subgroup/project"'

    def list_releases(self, source: SourceConfig) -> Iterator[Release]:
        base = f"{self._project_api(source)}/releases"
        for page in range(1, MAX_PAGES + 1):
            url = f"{base}?per_page={PAGE_SIZE}&page={page}&order_by=released_at&sort=desc"
            items = self._get(source, url, f"releases of gitlab:{source.repository}")
            if not isinstance(items, list):
                raise ToolbotError("HTTP_ERROR", f"unexpected response listing releases of gitlab:{source.repository}")
            for item in items:
                yield _release(item)
            if len(items) < PAGE_SIZE:
                return

    def get_release(self, source: SourceConfig, tag: str) -> Release:
        url = f"{self._project_api(source)}/releases/{quote(tag, safe='')}"
        what = f'release "{tag}" of gitlab:{source.repository}'
        try:
            item = self._get(source, url, what)
        except HttpError as err:
            if err.status == 404:
                raise ToolbotError(
                    "RELEASE_NOT_FOUND",
                    f"{what} not found. Check the tag template and that a GitLab *release* exists for that tag; "
                    f"for private projects {self.auth_hint()}.",
                ) from None
            raise
        return _release(item)

    def download_request(self, source: SourceConfig, asset: ReleaseAsset) -> HttpRequest:
        # Release links can point at any host; the token only goes to the source instance.
        return HttpRequest(asset.url, self._headers(source, asset.url))

    def auth_hint(self) -> str:
        return "set TOOLBOT_GITLAB_SOURCE_TOKEN (read_api) if the source project is private"

    def _project_api(self, source: SourceConfig) -> str:
        return f"{source.host}/api/v4/projects/{quote(source.repository, safe='')}"

    def _get(self, source: SourceConfig, url: str, what: str) -> Any:
        return self._http.get_json(HttpRequest(url, self._headers(source, url)), what, self.auth_hint())

    def _token_for(self, source: SourceConfig) -> str:
        if self._source_token:
            return self._source_token
        if self._target_token and self._target_host and same_origin(source.host, self._target_host):
            return self._target_token
        return ""

    def _headers(self, source: SourceConfig, target: str) -> dict[str, str]:
        token = self._token_for(source)
        return {"private-token": token} if token and same_origin(target, source.host) else {}


def _release(item: Mapping[str, Any]) -> Release:
    links = (item.get("assets") or {}).get("links") or []
    return Release(
        tag=item["tag_name"],
        url=((item.get("_links") or {}).get("self")) or "",
        assets=tuple(ReleaseAsset(name=link["name"], url=link["url"]) for link in links),
        # Upcoming (scheduled) releases are not published yet: treat like drafts.
        draft=bool(item.get("upcoming_release")),
        prerelease=False,
        notes=item.get("description") or "",
        published_at=item.get("released_at"),
    )
