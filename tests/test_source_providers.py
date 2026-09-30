from __future__ import annotations

import httpx
import pytest
from conftest import GH_API, GitHubRelease, Router, github_releases, make_http

from toolbot.config.models import SourceConfig
from toolbot.errors import ToolbotError
from toolbot.providers.source.github import GitHubSourceProvider
from toolbot.providers.source.gitlab import GitLabSourceProvider

GH = SourceConfig("github", "acme/widget", "https://github.com")
GL = SourceConfig("gitlab", "group/sub/widget", "https://gitlab.com")
GL_API = "https://gitlab.com/api/v4/projects/group%2Fsub%2Fwidget"


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
        releases = list(GitHubSourceProvider(make_http(router), {}).list_releases(GH))
        assert [(r.tag, r.prerelease, r.draft) for r in releases] == [
            ("v2.0.0-rc.1", True, False),
            ("v1.1.0", False, False),
            ("v1.2.0", False, True),
        ]
        stable = releases[1]
        assert stable.url == "https://github.com/acme/widget/releases/tag/v1.1.0"
        assert stable.notes == "Notes"
        assert stable.assets[0].name == "a.zip" and stable.assets[0].digest is not None
        assert stable.assets[0].digest.algorithm == "sha256"

    def test_paginates(self, router: Router) -> None:
        page1 = [{"tag_name": f"v1.0.{i}", "assets": []} for i in range(100)]
        page2 = [{"tag_name": "v0.9.0", "assets": []}]
        router.add(
            "GET",
            f"{GH_API}/releases",
            lambda r: httpx.Response(200, json=page1 if r.url.params["page"] == "1" else page2),
        )
        tags = [r.tag for r in GitHubSourceProvider(make_http(router), {}).list_releases(GH)]
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
        release = GitHubSourceProvider(make_http(router), {}).get_release(GH, "v1")
        assert [a.name for a in release.assets] == ["a"]

    def test_token_only_sent_to_github_api(self, router: Router) -> None:
        github_releases(router, [GitHubRelease("v1.0.0", {"a.zip": "x"})])
        provider = GitHubSourceProvider(make_http(router), {"TOOLBOT_GITHUB_TOKEN": "ghp_x"})
        list(provider.list_releases(GH))
        assert router.requests[0].headers["authorization"] == "Bearer ghp_x"
        from toolbot.providers.source.base import ReleaseAsset

        assert "authorization" not in provider.download_request(GH, ReleaseAsset("x", "https://evil.example/x")).headers

    def test_enterprise_api_base(self, router: Router) -> None:
        router.json("https://ghe.example.com/api/v3/repos/acme/widget/releases", [])
        list(
            GitHubSourceProvider(make_http(router), {}).list_releases(
                SourceConfig("github", "acme/widget", "https://ghe.example.com")
            )
        )
        assert len(router.requests) == 1

    def test_missing_release(self, router: Router) -> None:
        with pytest.raises(ToolbotError) as err:
            GitHubSourceProvider(make_http(router), {}).get_release(GH, "v9.9.9")
        assert err.value.code == "RELEASE_NOT_FOUND"
        assert "TOOLBOT_GITHUB_TOKEN" in err.value.message

    def test_api_failure(self, router: Router) -> None:
        router.json(f"{GH_API}/releases", {"message": "Bad credentials"}, status=401)
        with pytest.raises(ToolbotError) as err:
            list(GitHubSourceProvider(make_http(router), {"TOOLBOT_GITHUB_TOKEN": "bad"}).list_releases(GH))
        assert err.value.code == "AUTH_FAILED"


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
            f"{GL_API}/releases", [self.RELEASE, {**self.RELEASE, "tag_name": "v2.0.0", "upcoming_release": True}]
        )
        releases = list(GitLabSourceProvider(make_http(router), {}).list_releases(GL))
        assert releases[0].tag == "v1.0.0" and not releases[0].draft
        assert releases[0].url.endswith("/-/releases/v1.0.0")
        assert [a.name for a in releases[0].assets] == ["widget-linux.tar.gz", "mirror.zip"]
        assert releases[1].draft  # upcoming releases are not published yet

    def test_nested_groups_are_encoded(self, router: Router) -> None:
        router.json(f"{GL_API}/releases/v1.0.0", self.RELEASE)
        GitLabSourceProvider(make_http(router), {}).get_release(GL, "v1.0.0")
        assert "group%2Fsub%2Fwidget" in str(router.requests[0].url)

    def test_source_token_scoping(self, router: Router) -> None:
        router.json(f"{GL_API}/releases/v1.0.0", self.RELEASE)
        provider = GitLabSourceProvider(make_http(router), {"TOOLBOT_GITLAB_SOURCE_TOKEN": "glpat"})
        release = provider.get_release(GL, "v1.0.0")
        assert router.requests[0].headers["private-token"] == "glpat"
        assert provider.download_request(GL, release.assets[0]).headers["private-token"] == "glpat"
        assert "private-token" not in provider.download_request(GL, release.assets[1]).headers

    def test_target_token_reused_only_for_same_instance(self, router: Router) -> None:
        env = {"TOOLBOT_GITLAB_TOKEN": "target", "TOOLBOT_GITLAB_URL": "https://gitlab.com"}
        router.json(f"{GL_API}/releases/v1.0.0", self.RELEASE)
        GitLabSourceProvider(make_http(router), env).get_release(GL, "v1.0.0")
        assert router.requests[-1].headers["private-token"] == "target"
        other = {**env, "TOOLBOT_GITLAB_URL": "https://gitlab.example.com"}
        GitLabSourceProvider(make_http(router), other).get_release(GL, "v1.0.0")
        assert "private-token" not in router.requests[-1].headers

    def test_api_failure(self, router: Router) -> None:
        router.json(f"{GL_API}/releases", {"message": "403 Forbidden"}, status=403)
        with pytest.raises(ToolbotError) as err:
            list(GitLabSourceProvider(make_http(router), {}).list_releases(GL))
        assert err.value.code == "AUTH_FAILED"

    def test_missing_release(self, router: Router) -> None:
        with pytest.raises(ToolbotError) as err:
            GitLabSourceProvider(make_http(router), {}).get_release(GL, "v9")
        assert err.value.code == "RELEASE_NOT_FOUND"

    def test_repository_paths(self, router: Router) -> None:
        provider = GitLabSourceProvider(make_http(router), {})
        assert provider.check_repository("group/sub/project") is None
        assert provider.check_repository("project")
        assert provider.check_repository("group/../project")
