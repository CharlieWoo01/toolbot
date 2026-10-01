"""GitLab.com and self-managed GitLab releases.

GitLab release "assets" are release links. They usually point at the
generic package registry but may point anywhere, so the Releases API is the
only reliable way to resolve them. Project paths with sub-groups are
URL-encoded as a single path parameter. GitLab publishes no asset digests.

Tokens come from the host registry: register internal instances in
``toolbot.yaml``; the target GitLab is registered automatically, so one token
covers both when an upstream project lives on the same instance.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import quote

from toolbot.config.models import SourceConfig
from toolbot.errors import ToolbotError
from toolbot.hosts import GITLAB_COM, HostRegistry
from toolbot.http import HttpClient, HttpError, HttpRequest
from toolbot.providers.source.base import Release, ReleaseAsset, SourceProvider

PATH_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,254}$")
PAGE_SIZE = 100
MAX_PAGES = 5
MAX_TAG_PAGES = 30


class GitLabSourceProvider(SourceProvider):
    id = "gitlab"
    default_host = GITLAB_COM
    supports_digest = False

    def __init__(self, http: HttpClient, hosts: HostRegistry) -> None:
        self._http = http
        self._hosts = hosts

    def check_repository(self, repository: str) -> str | None:
        segments = repository.split("/")
        if len(segments) >= 2 and all(PATH_SEGMENT.match(segment) and segment != ".." for segment in segments):
            return None
        return 'must be a project path such as "group/project" or "group/subgroup/project"'

    def list_releases(self, source: SourceConfig) -> Iterator[Release]:
        releases_url = f"{self._project_api(source)}/releases"
        for page in range(1, MAX_PAGES + 1):
            url = f"{releases_url}?per_page={PAGE_SIZE}&page={page}&order_by=released_at&sort=desc"
            items = self._get(source, url, "releases")
            if not isinstance(items, list):
                raise ToolbotError("HTTP_ERROR", f"unexpected response listing releases of gitlab:{source.repository}")
            for item in items:
                yield _release(item)
            if len(items) < PAGE_SIZE:
                return

    def get_release(self, source: SourceConfig, tag: str) -> Release:
        url = f"{self._project_api(source)}/releases/{quote(tag, safe='')}"
        try:
            item = self._get(source, url, f'release "{tag}"')
        except HttpError as error:
            if error.status == 404:
                raise ToolbotError(
                    "RELEASE_NOT_FOUND",
                    f'release "{tag}" of gitlab:{source.repository} not found. Check the tag template and that a '
                    f"GitLab *release* exists for that tag; for private projects {self.auth_hint(source)}.",
                ) from None
            raise
        return _release(item)

    def list_tags(self, source: SourceConfig, prefix: str) -> Iterator[Release]:
        tags_url = f"{self._project_api(source)}/repository/tags"
        search = f"&search={quote('^' + prefix, safe='')}" if prefix else ""
        for page in range(1, MAX_TAG_PAGES + 1):
            items = self._get(source, f"{tags_url}?per_page={PAGE_SIZE}&page={page}{search}", "tags")
            if not isinstance(items, list):
                raise ToolbotError("HTTP_ERROR", f"unexpected response listing tags of gitlab:{source.repository}")
            for item in items:
                yield self._tag_release(source, item["name"])
            if len(items) < PAGE_SIZE:
                return

    def get_tag(self, source: SourceConfig, tag: str) -> Release:
        url = f"{self._project_api(source)}/repository/tags/{quote(tag, safe='')}"
        try:
            self._get(source, url, f'tag "{tag}"')
        except HttpError as error:
            if error.status == 404:
                raise ToolbotError(
                    "RELEASE_NOT_FOUND",
                    f'tag "{tag}" of gitlab:{source.repository} not found. Check the tag template; for private '
                    f"projects {self.auth_hint(source)}.",
                ) from None
            raise
        return self._tag_release(source, tag)

    def download_request(self, source: SourceConfig, asset: ReleaseAsset) -> HttpRequest:
        # Release links can point at any host; tokens only go to their own host.
        return HttpRequest(asset.url, self._hosts.auth_headers(asset.url))

    def file_request(self, source: SourceConfig, path: str, ref: str) -> HttpRequest:
        # lfs=true serves the real content of Git LFS files instead of the pointer.
        url = (
            f"{self._project_api(source)}/repository/files/{quote(path, safe='')}/raw"
            f"?ref={quote(ref, safe='')}&lfs=true"
        )
        return HttpRequest(url, self._hosts.auth_headers(url))

    def _tag_release(self, source: SourceConfig, tag: str) -> Release:
        return Release(tag=tag, url=f"{source.host}/{source.repository}/-/tags/{quote(tag, safe='')}")

    def auth_hint(self, source: SourceConfig) -> str:
        host = self._hosts.find(source.host)
        if host and host.token_env:
            return f"set {host.token_env} to a token with read_api access to {source.repository}"
        return f"register {source.host} under hosts in toolbot.yaml with a token-env"

    def _project_api(self, source: SourceConfig) -> str:
        return f"{source.host}/api/v4/projects/{quote(source.repository, safe='')}"

    def _get(self, source: SourceConfig, url: str, what: str) -> Any:
        return self._http.get_json(
            HttpRequest(url, self._hosts.auth_headers(url)),
            f"{what} of gitlab:{source.repository}",
            self.auth_hint(source),
        )


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
