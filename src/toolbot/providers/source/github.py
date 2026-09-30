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
from toolbot.http import HttpClient, HttpError, HttpRequest, same_origin
from toolbot.providers.source.base import Digest, Release, ReleaseAsset, SourceProvider

_REPOSITORY = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
PAGE_SIZE = 100
MAX_PAGES = 5


def api_base(host: str) -> str:
    return "https://api.github.com" if host == "https://github.com" else f"{host}/api/v3"


class GitHubSourceProvider(SourceProvider):
    id = "github"
    default_host = "https://github.com"
    token_env = "TOOLBOT_GITHUB_TOKEN"  # noqa: S105 - the variable *name*, not a secret
    supports_digest = True

    def __init__(self, http: HttpClient, env: Mapping[str, str]) -> None:
        self._http = http
        self._token = env.get(self.token_env, "")

    def check_repository(self, repository: str) -> str | None:
        return None if _REPOSITORY.match(repository) else 'must be "owner/repository"'

    def list_releases(self, source: SourceConfig) -> Iterator[Release]:
        base = f"{api_base(source.host)}/repos/{source.repository}/releases"
        for page in range(1, MAX_PAGES + 1):
            url = f"{base}?per_page={PAGE_SIZE}&page={page}"
            items = self._get(source, url, f"releases of github:{source.repository}")
            if not isinstance(items, list):
                raise ToolbotError("HTTP_ERROR", f"unexpected response listing releases of github:{source.repository}")
            for item in items:
                yield _release(item)
            if len(items) < PAGE_SIZE:
                return

    def get_release(self, source: SourceConfig, tag: str) -> Release:
        url = f"{api_base(source.host)}/repos/{source.repository}/releases/tags/{quote(tag, safe='')}"
        what = f'release "{tag}" of github:{source.repository}'
        try:
            item = self._get(source, url, what)
        except HttpError as err:
            # GitHub answers 404 (not 401) for private repositories without access.
            if err.status == 404:
                raise ToolbotError(
                    "RELEASE_NOT_FOUND",
                    f"{what} not found. Check the tag template and that a GitHub *release* (not only a tag) "
                    f"exists; for private repositories {self.auth_hint()}.",
                ) from None
            raise
        return _release(item)

    def download_request(self, source: SourceConfig, asset: ReleaseAsset) -> HttpRequest:
        # API asset URL + octet-stream redirects to a signed CDN URL; HttpClient
        # drops the Authorization header when the redirect changes origin.
        return HttpRequest(asset.url, self._headers(source, asset.url, "application/octet-stream"))

    def _get(self, source: SourceConfig, url: str, what: str) -> Any:
        return self._http.get_json(
            HttpRequest(url, self._headers(source, url, "application/vnd.github+json")), what, self.auth_hint()
        )

    def _headers(self, source: SourceConfig, target: str, accept: str) -> dict[str, str]:
        headers = {"accept": accept, "x-github-api-version": "2022-11-28"}
        # Only ever sent to the configured GitHub API origin, never to URLs
        # that merely came back in an API response.
        if self._token and same_origin(target, api_base(source.host)):
            headers["authorization"] = f"Bearer {self._token}"
        return headers


def _release(item: Mapping[str, Any]) -> Release:
    assets = tuple(
        ReleaseAsset(
            name=a["name"],
            url=a["url"],
            size=a.get("size"),
            digest=_digest(a.get("digest")),
        )
        for a in item.get("assets", [])
        if a.get("state", "uploaded") == "uploaded"
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
    m = re.match(r"^([a-z0-9]+):([0-9a-fA-F]+)$", value)
    return Digest(m.group(1).lower(), m.group(2).lower()) if m else None
