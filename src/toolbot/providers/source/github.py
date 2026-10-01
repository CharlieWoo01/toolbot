"""GitHub.com and GitHub Enterprise Server releases.

Uses the REST API rather than predictable
``https://github.com/<repo>/releases/download/<tag>/<asset>`` URLs: the API
works for private repositories, lists the exact assets (clear errors for
renamed assets, ambiguity checks for patterns), exposes draft/prerelease
flags, and reports a server-computed ``digest`` per asset.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import quote

from toolbot.config.models import SourceConfig
from toolbot.errors import ToolbotError
from toolbot.hosts import GITHUB_COM, HostRegistry
from toolbot.http import HttpClient, HttpError, HttpRequest
from toolbot.providers.source.base import Digest, Release, ReleaseAsset, SourceProvider

REPOSITORY = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
PAGE_SIZE = 100
MAX_PAGES = 5
MAX_TAG_PAGES = 30


def api_base(host_url: str) -> str:
    return "https://api.github.com" if host_url == GITHUB_COM else f"{host_url}/api/v3"


class GitHubSourceProvider(SourceProvider):
    id = "github"
    default_host = GITHUB_COM
    supports_digest = True

    def __init__(self, http: HttpClient, hosts: HostRegistry) -> None:
        self._http = http
        self._hosts = hosts

    def check_repository(self, repository: str) -> str | None:
        return None if REPOSITORY.match(repository) else 'must be "owner/repository"'

    def list_releases(self, source: SourceConfig) -> Iterator[Release]:
        releases_url = f"{api_base(source.host)}/repos/{source.repository}/releases"
        for page in range(1, MAX_PAGES + 1):
            items = self._get(source, f"{releases_url}?per_page={PAGE_SIZE}&page={page}", "releases")
            if not isinstance(items, list):
                raise ToolbotError("HTTP_ERROR", f"unexpected response listing releases of github:{source.repository}")
            for item in items:
                yield _release(item)
            if len(items) < PAGE_SIZE:
                return

    def get_release(self, source: SourceConfig, tag: str) -> Release:
        url = f"{api_base(source.host)}/repos/{source.repository}/releases/tags/{quote(tag, safe='')}"
        try:
            item = self._get(source, url, f'release "{tag}"')
        except HttpError as error:
            # GitHub answers 404 (not 401) for private repositories without access.
            if error.status == 404:
                raise ToolbotError(
                    "RELEASE_NOT_FOUND",
                    f'release "{tag}" of github:{source.repository} not found. Check the tag template and that a '
                    f"GitHub *release* (not only a tag) exists; for private repositories {self.auth_hint(source)}.",
                ) from None
            raise
        return _release(item)

    def list_tags(self, source: SourceConfig, prefix: str) -> Iterator[Release]:
        # matching-refs filters by prefix server-side, so monorepos with many
        # tags of other components stay cheap.
        refs_url = f"{api_base(source.host)}/repos/{source.repository}/git/matching-refs/tags/{quote(prefix, safe='/')}"
        seen: set[str] = set()
        for page in range(1, MAX_TAG_PAGES + 1):
            items = self._get(source, f"{refs_url}?per_page={PAGE_SIZE}&page={page}", "tags")
            if not isinstance(items, list):
                raise ToolbotError("HTTP_ERROR", f"unexpected response listing tags of github:{source.repository}")
            new_tags = [item["ref"].removeprefix("refs/tags/") for item in items if item["ref"] not in seen]
            seen.update(item["ref"] for item in items)
            for tag in new_tags:
                yield self._tag_release(source, tag)
            if len(items) < PAGE_SIZE or not new_tags:
                return

    def get_tag(self, source: SourceConfig, tag: str) -> Release:
        url = f"{api_base(source.host)}/repos/{source.repository}/git/ref/tags/{quote(tag, safe='/')}"
        try:
            self._get(source, url, f'tag "{tag}"')
        except HttpError as error:
            if error.status == 404:
                raise ToolbotError(
                    "RELEASE_NOT_FOUND",
                    f'tag "{tag}" of github:{source.repository} not found. Check the tag template; for private '
                    f"repositories {self.auth_hint(source)}.",
                ) from None
            raise
        return self._tag_release(source, tag)

    def download_request(self, source: SourceConfig, asset: ReleaseAsset) -> HttpRequest:
        # The API asset URL + octet-stream redirects to a signed CDN URL; the
        # HTTP client drops credentials when the redirect changes origin.
        return HttpRequest(asset.url, {**self._headers(asset.url), "accept": "application/octet-stream"})

    def file_request(self, source: SourceConfig, path: str, ref: str) -> HttpRequest:
        # The contents API serves raw bytes (up to 100 MB) for public and private repositories.
        url = (
            f"{api_base(source.host)}/repos/{source.repository}/contents/{quote(path, safe='/')}"
            f"?ref={quote(ref, safe='')}"
        )
        return HttpRequest(url, {**self._headers(url), "accept": "application/vnd.github.raw"})

    def _tag_release(self, source: SourceConfig, tag: str) -> Release:
        return Release(tag=tag, url=f"{source.host}/{source.repository}/tree/{quote(tag, safe='/')}")

    def auth_hint(self, source: SourceConfig) -> str:
        host = self._hosts.find(api_base(source.host))
        if host and host.token_env:
            return f"set {host.token_env} to a token that can read {source.repository}"
        return f"register {source.host} under hosts in toolbot.yaml with a token-env"

    def _get(self, source: SourceConfig, url: str, what: str) -> Any:
        return self._http.get_json(
            HttpRequest(url, {**self._headers(url), "accept": "application/vnd.github+json"}),
            f"{what} of github:{source.repository}",
            self.auth_hint(source),
        )

    def _headers(self, url: str) -> dict[str, str]:
        return {"x-github-api-version": "2022-11-28", **self._hosts.auth_headers(url)}


def _release(item: Mapping[str, Any]) -> Release:
    assets = tuple(
        ReleaseAsset(
            name=asset["name"],
            url=asset["url"],
            size=asset.get("size"),
            digest=_digest(asset.get("digest")),
        )
        for asset in item.get("assets", [])
        if asset.get("state", "uploaded") == "uploaded"
    )
    return Release(
        tag=item["tag_name"],
        url=item.get("html_url", ""),
        assets=assets,
        draft=bool(item.get("draft")),
        prerelease=bool(item.get("prerelease")),
        notes=item.get("body") or "",
        published_at=item.get("published_at"),
    )


def _digest(value: object) -> Digest | None:
    if not isinstance(value, str):
        return None
    match = re.match(r"^([a-z0-9]+):([0-9a-fA-F]+)$", value)
    return Digest(match.group(1).lower(), match.group(2).lower()) if match else None
