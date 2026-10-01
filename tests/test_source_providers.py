from __future__ import annotations

import httpx
import pytest
from conftest import GH_API, GitHubRelease, Router, github_releases, make_http

from toolbot.config.control import ControlConfig
from toolbot.config.models import SourceConfig
from toolbot.errors import ToolbotError
from toolbot.hosts import Host, HostRegistry
from toolbot.providers.source.base import ReleaseAsset
from toolbot.providers.source.github import GitHubSourceProvider
from toolbot.providers.source.gitlab import GitLabSourceProvider
from toolbot.services.context import build_host_registry

GITHUB_SOURCE = SourceConfig("github", "acme/widget", "https://github.com")
GITLAB_SOURCE = SourceConfig("gitlab", "group/sub/widget", "https://gitlab.com")
GITLAB_API = "https://gitlab.com/api/v4/projects/group%2Fsub%2Fwidget"


def hosts(environment: dict[str, str] | None = None, control: ControlConfig | None = None) -> HostRegistry:
    return build_host_registry(control or ControlConfig(), environment or {})


def github(router: Router, environment: dict[str, str] | None = None) -> GitHubSourceProvider:
    return GitHubSourceProvider(make_http(router), hosts(environment))


def gitlab(router: Router, registry: HostRegistry | None = None) -> GitLabSourceProvider:
    return GitLabSourceProvider(make_http(router), registry or hosts())


class TestGitHub:
    def test_lists_releases_with_flags_and_assets(self, router: Router) -> None:
        github_releases(
            router,
            [
                GitHubRelease("v2.0.0-rc.1", {"a.zip": "rc"}, prerelease=True),
                GitHubRelease("v1.1.0", {"a.zip": "stable"}, notes="Notes"),
                GitHubRelease("v1.2.0", {"a.zip": "draft"}, draft=True),
            ],
        )
        releases = list(github(router).list_releases(GITHUB_SOURCE))
        assert [(release.tag, release.prerelease, release.draft) for release in releases] == [
            ("v2.0.0-rc.1", True, False),
            ("v1.1.0", False, False),
            ("v1.2.0", False, True),
        ]
        stable = releases[1]
        assert stable.url == "https://github.com/acme/widget/releases/tag/v1.1.0"
        assert stable.notes == "Notes"
        assert stable.assets[0].name == "a.zip"
        assert stable.assets[0].digest is not None and stable.assets[0].digest.algorithm == "sha256"

    def test_paginates(self, router: Router) -> None:
        first_page = [{"tag_name": f"v1.0.{index}", "assets": []} for index in range(100)]
        second_page = [{"tag_name": "v0.9.0", "assets": []}]
        router.add(
            "GET",
            f"{GH_API}/releases",
            lambda request: httpx.Response(200, json=first_page if request.url.params["page"] == "1" else second_page),
        )
        tags = [release.tag for release in github(router).list_releases(GITHUB_SOURCE)]
        assert len(tags) == 101 and tags[-1] == "v0.9.0"

    def test_ignores_incomplete_uploads(self, router: Router) -> None:
        router.json(
            f"{GH_API}/releases/tags/v1",
            {
                "tag_name": "v1",
                "assets": [
                    {"name": "a", "url": f"{GH_API}/releases/assets/1", "state": "uploaded"},
                    {"name": "b", "url": f"{GH_API}/releases/assets/2", "state": "starter"},
                ],
            },
        )
        release = github(router).get_release(GITHUB_SOURCE, "v1")
        assert [asset.name for asset in release.assets] == ["a"]

    def test_token_only_sent_to_github(self, router: Router) -> None:
        github_releases(router, [GitHubRelease("v1.0.0", {"a.zip": "x"})])
        provider = github(router, {"TOOLBOT_GITHUB_TOKEN": "ghp_x"})
        list(provider.list_releases(GITHUB_SOURCE))
        assert router.requests[0].headers["authorization"] == "Bearer ghp_x"
        foreign = provider.download_request(GITHUB_SOURCE, ReleaseAsset("x", "https://evil.example/x"))
        assert "authorization" not in foreign.headers

    def test_enterprise_host_with_its_own_token(self, router: Router) -> None:
        enterprise = ControlConfig(hosts=(Host("ghe", "github", "https://ghe.example.com", "GHE_TOKEN"),))
        provider = GitHubSourceProvider(
            make_http(router), hosts({"GHE_TOKEN": "ghe-secret", "TOOLBOT_GITHUB_TOKEN": "public"}, enterprise)
        )
        router.json("https://ghe.example.com/api/v3/repos/acme/widget/releases", [])
        list(provider.list_releases(SourceConfig("github", "acme/widget", "https://ghe.example.com")))
        assert router.requests[-1].headers["authorization"] == "Bearer ghe-secret"

    def test_missing_release(self, router: Router) -> None:
        with pytest.raises(ToolbotError) as error:
            github(router).get_release(GITHUB_SOURCE, "v9.9.9")
        assert error.value.code == "RELEASE_NOT_FOUND"
        assert "TOOLBOT_GITHUB_TOKEN" in error.value.message

    def test_api_failure(self, router: Router) -> None:
        router.json(f"{GH_API}/releases", {"message": "Bad credentials"}, status=401)
        with pytest.raises(ToolbotError) as error:
            list(github(router, {"TOOLBOT_GITHUB_TOKEN": "bad"}).list_releases(GITHUB_SOURCE))
        assert error.value.code == "AUTH_FAILED"


class TestGitLab:
    RELEASE = {
        "tag_name": "v1.0.0",
        "description": "GitLab notes",
        "_links": {"self": "https://gitlab.com/group/sub/widget/-/releases/v1.0.0"},
        "assets": {
            "links": [
                {
                    "name": "widget-linux.tar.gz",
                    "url": "https://gitlab.com/api/v4/projects/1/packages/generic/widget/1.0.0/widget-linux.tar.gz",
                },
                {"name": "mirror.zip", "url": "https://downloads.example.org/widget.zip"},
            ]
        },
    }

    def test_discovers_releases_and_assets(self, router: Router) -> None:
        router.json(
            f"{GITLAB_API}/releases", [self.RELEASE, {**self.RELEASE, "tag_name": "v2.0.0", "upcoming_release": True}]
        )
        releases = list(gitlab(router).list_releases(GITLAB_SOURCE))
        assert releases[0].tag == "v1.0.0" and not releases[0].draft
        assert releases[0].url.endswith("/-/releases/v1.0.0")
        assert [asset.name for asset in releases[0].assets] == ["widget-linux.tar.gz", "mirror.zip"]
        assert releases[1].draft  # upcoming releases are not published yet

    def test_nested_groups_are_encoded(self, router: Router) -> None:
        router.json(f"{GITLAB_API}/releases/v1.0.0", self.RELEASE)
        gitlab(router).get_release(GITLAB_SOURCE, "v1.0.0")
        assert "group%2Fsub%2Fwidget" in str(router.requests[0].url)

    def test_source_token_only_sent_to_its_host(self, router: Router) -> None:
        router.json(f"{GITLAB_API}/releases/v1.0.0", self.RELEASE)
        provider = gitlab(router, hosts({"TOOLBOT_GITLAB_SOURCE_TOKEN": "glpat"}))
        release = provider.get_release(GITLAB_SOURCE, "v1.0.0")
        assert router.requests[0].headers["private-token"] == "glpat"
        assert provider.download_request(GITLAB_SOURCE, release.assets[0]).headers["private-token"] == "glpat"
        assert "private-token" not in provider.download_request(GITLAB_SOURCE, release.assets[1]).headers

    def test_ci_instance_token_reused_only_for_that_instance(self, router: Router) -> None:
        on_gitlab_com = {"TOOLBOT_GITLAB_TOKEN": "target", "TOOLBOT_GITLAB_URL": "https://gitlab.com"}
        router.json(f"{GITLAB_API}/releases/v1.0.0", self.RELEASE)
        gitlab(router, hosts(on_gitlab_com)).get_release(GITLAB_SOURCE, "v1.0.0")
        assert router.requests[-1].headers["private-token"] == "target"
        elsewhere = {**on_gitlab_com, "TOOLBOT_GITLAB_URL": "https://gitlab.example.com"}
        gitlab(router, hosts(elsewhere)).get_release(GITLAB_SOURCE, "v1.0.0")
        assert "private-token" not in router.requests[-1].headers

    def test_internal_instance_from_toolbot_yaml(self, router: Router) -> None:
        internal = ControlConfig(hosts=(Host("internal", "gitlab", "https://git.corp.example/gitlab", "CORP_TOKEN"),))
        source = SourceConfig("gitlab", "platform/cli", "https://git.corp.example/gitlab")
        router.json("https://git.corp.example/gitlab/api/v4/projects/platform%2Fcli/releases/v1", self.RELEASE)
        gitlab(router, hosts({"CORP_TOKEN": "corp"}, internal)).get_release(source, "v1")
        assert router.requests[-1].headers["private-token"] == "corp"

    def test_api_failure(self, router: Router) -> None:
        router.json(f"{GITLAB_API}/releases", {"message": "403 Forbidden"}, status=403)
        with pytest.raises(ToolbotError) as error:
            list(gitlab(router).list_releases(GITLAB_SOURCE))
        assert error.value.code == "AUTH_FAILED"

    def test_missing_release(self, router: Router) -> None:
        with pytest.raises(ToolbotError) as error:
            gitlab(router).get_release(GITLAB_SOURCE, "v9")
        assert error.value.code == "RELEASE_NOT_FOUND"
        assert "read_api" in error.value.message

    def test_repository_paths(self, router: Router) -> None:
        provider = gitlab(router)
        assert provider.check_repository("group/sub/project") is None
        assert provider.check_repository("project")
        assert provider.check_repository("group/../project")
