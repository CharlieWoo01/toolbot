"""Shared fixtures: a routed httpx mock, a Toolbot context, fake GitHub/GitLab
releases, a real git repository with a local bare "remote", and an in-memory
target provider."""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from toolbot.config.control import ControlConfig
from toolbot.errors import ToolbotError
from toolbot.git import GitAuth
from toolbot.http import HttpClient
from toolbot.providers.source import create_source_providers
from toolbot.providers.target.base import ChangeRequest, ChangeRequestSpec, State, TargetProvider
from toolbot.services.context import Context, build_host_registry

Handler = Callable[[httpx.Request], httpx.Response]


def sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


class Router:
    """httpx transport that routes by method + URL without query, records requests, 404s the rest."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Handler] = {}
        self.requests: list[httpx.Request] = []

    def add(self, method: str, url: str, handler: Handler) -> Router:
        self.routes[(method, url)] = handler
        return self

    def json(
        self, url: str, body: Any, status: int = 200, method: str = "GET", headers: dict[str, str] | None = None
    ) -> Router:
        return self.add(method, url, lambda _request: httpx.Response(status, json=body, headers=headers))

    def bytes(self, url: str, body: bytes | str, status: int = 200) -> Router:
        data = body.encode() if isinstance(body, str) else body
        return self.add("GET", url, lambda _request: httpx.Response(status, content=data))

    def redirect(self, url: str, location: str) -> Router:
        return self.add("GET", url, lambda _request: httpx.Response(302, headers={"location": location}))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url.copy_with(query=None))
        handler = self.routes.get((request.method, url))
        if handler is None and request.method == "HEAD" and ("GET", url) in self.routes:
            # Like a real server: HEAD answers with the GET route's status and headers, no body.
            response = self.routes[("GET", url)](request)
            return httpx.Response(response.status_code, headers=response.headers)
        return handler(request) if handler else httpx.Response(404, json={"message": "not found"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def to(self, prefix: str) -> list[httpx.Request]:
        return [request for request in self.requests if str(request.url).startswith(prefix)]

    def writes(self) -> list[httpx.Request]:
        return [request for request in self.requests if request.method not in ("GET", "HEAD")]


def make_http(router: Router) -> HttpClient:
    return HttpClient(router.transport(), retries=2, backoff=0, sleep=lambda _seconds: None)


def make_context(
    router: Router,
    env: dict[str, str] | None = None,
    temp_root: Path | None = None,
    control: ControlConfig | None = None,
) -> Context:
    http = make_http(router)
    environment = env or {}
    control_config = control or ControlConfig()
    logs: list[str] = []
    ctx = Context(
        http=http,
        providers=create_source_providers(http, build_host_registry(control_config, environment)),
        env=environment,
        control=control_config,
        log=logs.append,
        temp_root=temp_root,
        max_download_bytes=10 * 1024 * 1024,
    )
    ctx.logs = logs  # type: ignore[attr-defined]
    return ctx


@pytest.fixture
def router() -> Router:
    return Router()


# -- GitHub fixtures -----------------------------------------------------------

GH_API = "https://api.github.com/repos/acme/widget"


@dataclass
class GitHubRelease:
    tag: str
    files: dict[str, bytes | str]
    prerelease: bool = False
    draft: bool = False
    digests: bool = True
    notes: str = "Bug fixes."


def github_releases(router: Router, releases: list[GitHubRelease], repo_api: str = GH_API) -> None:
    """Register list + by-tag endpoints and asset downloads (API URL -> 302 -> CDN)."""
    items = []
    asset_id = 0
    for release in releases:
        assets = []
        for name, body in release.files.items():
            asset_id += 1
            data = body.encode() if isinstance(body, str) else body
            api_url = f"{repo_api}/releases/assets/{asset_id}"
            cdn = f"https://release-assets.githubusercontent.com/{repo_api.rsplit('/', 1)[-1]}/{release.tag}/{asset_id}"
            router.redirect(api_url, cdn + "?sig=SIGNED")
            router.bytes(cdn, data)
            assets.append(
                {
                    "name": name,
                    "url": api_url,
                    "size": len(data),
                    "state": "uploaded",
                    "digest": f"sha256:{sha256(data)}" if release.digests else None,
                }
            )
        item = {
            "tag_name": release.tag,
            "draft": release.draft,
            "prerelease": release.prerelease,
            "html_url": f"https://github.com/acme/widget/releases/tag/{release.tag}",
            "body": release.notes,
            "published_at": "2026-09-01T00:00:00Z",
            "assets": assets,
        }
        items.append(item)
        router.json(f"{repo_api}/releases/tags/{release.tag}", item)
    router.json(f"{repo_api}/releases", items)


def widget_files(version: str) -> dict[str, bytes | str]:
    name = f"widget-{version}-windows-amd64.zip"
    body = f"zip-{version}"
    return {name: body, f"{name}.sha256": f"{sha256(body)} *{name}\n"}


WIDGET_YAML = """\
name: widget
version: "{version}"
tag: v{{version}}
source:
  provider: github
  repository: acme/widget
checksum:
  type: file
  file: "{{asset}}.sha256"
assets:
  windows-amd64:
    name: widget-{{version}}-windows-amd64.zip
    destination: widget.zip
{extra}"""


def widget_yaml(version: str = "1.0.0", extra: str = "") -> str:
    return WIDGET_YAML.format(version=version, extra=extra)


def write_tool(root: Path, name: str, text: str) -> Path:
    directory = root / "tools" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "tool.yaml").write_text(text, encoding="utf-8")
    return directory


# -- git fixtures ----------------------------------------------------------------


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@dataclass
class GitSetup:
    remote: Path
    work: Path

    @property
    def url(self) -> str:
        return self.remote.as_uri()

    def branches(self) -> list[str]:
        listing = git(self.remote, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
        return sorted(listing.split())

    def show(self, ref: str, path: str) -> str:
        return git(self.remote, "show", f"{ref}:{path}")

    def head(self, branch: str) -> str:
        return git(self.remote, "rev-parse", f"refs/heads/{branch}")

    def push_human_commit(self, branch: str) -> None:
        """Simulate a person pushing a fix on top of a Toolbot branch."""
        clone = self.work.parent / f"human-{branch.replace('/', '-')}"
        git(self.work.parent, "clone", "--quiet", "--branch", branch, self.url, str(clone))
        (clone / "NOTE.md").write_text("manual fix\n")
        git(clone, "add", "NOTE.md")
        git(clone, "-c", "user.name=Human", "-c", "user.email=h@example.com", "commit", "-qm", "manual fix")
        git(clone, "push", "--quiet", "origin", branch)


@pytest.fixture
def git_setup(tmp_path: Path) -> GitSetup:
    """A bare 'remote' with main, and a clone standing in for the CI checkout."""
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    git(tmp_path, "init", "--quiet", "--bare", "--initial-branch=main", str(remote))
    git(tmp_path, "clone", "--quiet", remote.as_uri(), str(work))
    git(work, "checkout", "--quiet", "-b", "main")
    return GitSetup(remote, work)


def commit_all(work: Path, message: str = "init") -> None:
    git(work, "add", "-A")
    git(work, "-c", "user.name=Dev", "-c", "user.email=dev@example.com", "commit", "-qm", message)
    git(work, "push", "--quiet", "origin", "HEAD:main")


# -- target fake -------------------------------------------------------------------


@dataclass
class FakeTarget(TargetProvider):
    """In-memory change requests over a real (file://) git remote."""

    url: str
    repository: str = "team/ci-tools"
    requests: list[ChangeRequest] = field(default_factory=list)
    descriptions: dict[int, str] = field(default_factory=dict)
    labels: dict[int, tuple[str, ...]] = field(default_factory=dict)
    comments: dict[int, list[str]] = field(default_factory=dict)
    fail_create: bool = False
    kind: str = "merge request"

    def repository_url(self) -> str:
        return self.url

    def git_auth(self) -> GitAuth | None:
        return None

    def default_branch(self) -> str:
        return "main"

    def find_by_branch(self, branch: str) -> list[ChangeRequest]:
        return [request for request in reversed(self.requests) if request.source_branch == branch]

    def list_open(self, branch_prefix: str, target_branch: str) -> list[ChangeRequest]:
        return [
            request
            for request in self.requests
            if request.state == "open"
            and request.source_branch.startswith(branch_prefix)
            and request.target_branch == target_branch
        ]

    def create(self, spec: ChangeRequestSpec) -> ChangeRequest:
        if self.fail_create:
            raise ToolbotError("TARGET_ERROR", "API down")
        for request in self.requests:
            if request.source_branch == spec.source_branch and request.state == "open":
                return request
        number = len(self.requests) + 1
        request = ChangeRequest(
            number,
            f"https://gitlab.example.com/{self.repository}/-/merge_requests/{number}",
            "open",
            spec.source_branch,
            spec.target_branch,
            spec.title,
        )
        self.requests.append(request)
        self.descriptions[request.id] = spec.description
        self.labels[request.id] = spec.labels
        return request

    def close(self, request: ChangeRequest, comment: str) -> None:
        self.set_state(request.id, "closed")
        self.comments.setdefault(request.id, []).append(comment)

    def set_state(self, request_id: int, state: State) -> None:
        for index, request in enumerate(self.requests):
            if request.id == request_id:
                self.requests[index] = replace(request, state=state)
