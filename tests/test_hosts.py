from __future__ import annotations

from toolbot.config.control import ControlConfig
from toolbot.hosts import Host, HostRegistry, merge_hosts, normalise_url
from toolbot.services.context import build_host_registry, ci_hosts

INTERNAL = Host("internal", "gitlab", "https://git.corp.example/gitlab", "CORP_TOKEN")
ENTERPRISE = Host("ghe", "github", "https://ghe.corp.example", "GHE_TOKEN")


def test_api_urls() -> None:
    assert Host("github", "github", "https://github.com").api_url == "https://api.github.com"
    assert ENTERPRISE.api_url == "https://ghe.corp.example/api/v3"
    assert INTERNAL.api_url == "https://git.corp.example/gitlab/api/v4"


def test_ownership_respects_path_prefix_and_origin() -> None:
    assert INTERNAL.owns("https://git.corp.example/gitlab/group/project")
    assert INTERNAL.owns("https://git.corp.example/gitlab/api/v4/projects/1")
    assert not INTERNAL.owns("https://git.corp.example/other-app/x")
    assert not INTERNAL.owns("https://git.corp.example.evil.com/gitlab/x")
    assert not INTERNAL.owns("http://git.corp.example/gitlab/x")
    assert Host("github", "github", "https://github.com").owns("https://api.github.com/repos/a/b")


def test_tokens_only_for_their_own_host() -> None:
    registry = HostRegistry(merge_hosts([INTERNAL, ENTERPRISE]), {"CORP_TOKEN": "corp", "GHE_TOKEN": "ghe"})
    assert registry.auth_headers("https://git.corp.example/gitlab/api/v4/x") == {"private-token": "corp"}
    assert registry.auth_headers("https://ghe.corp.example/api/v3/x") == {"authorization": "Bearer ghe"}
    assert registry.auth_headers("https://downloads.example.org/tool.zip") == {}
    assert registry.auth_headers("https://api.github.com/repos/a/b") == {}  # default host, no token set


def test_configured_hosts_take_precedence_over_defaults() -> None:
    own_github = Host("public-github", "github", "https://github.com", "MY_GITHUB_TOKEN")
    merged = merge_hosts([own_github])
    assert [host.name for host in merged] == ["public-github", "gitlab"]


def test_ci_instance_is_registered_with_the_target_token() -> None:
    gitlab_ci = ci_hosts({"CI_SERVER_URL": "https://gitlab.example.com"})
    assert gitlab_ci == [Host("ci-gitlab", "gitlab", "https://gitlab.example.com", "TOOLBOT_GITLAB_TOKEN")]
    enterprise_actions = ci_hosts({"GITHUB_SERVER_URL": "https://ghe.corp.example"})
    assert enterprise_actions == [Host("ci-github", "github", "https://ghe.corp.example", "TOOLBOT_GITHUB_TOKEN")]
    assert ci_hosts({"GITHUB_SERVER_URL": "https://github.com"}) == []  # covered by the default host


def test_configured_host_wins_over_ci_instance() -> None:
    control = ControlConfig(hosts=(Host("internal", "gitlab", "https://gitlab.example.com", "INTERNAL_TOKEN"),))
    registry = build_host_registry(control, {"CI_SERVER_URL": "https://gitlab.example.com", "INTERNAL_TOKEN": "x"})
    found = registry.find("https://gitlab.example.com/api/v4/projects")
    assert found is not None and found.name == "internal"


def test_normalise_url() -> None:
    assert normalise_url("https://GitLab.Example.com:8443/gitlab/") == "https://gitlab.example.com:8443/gitlab"
    assert normalise_url("http://gitlab.example.com") is None
    assert normalise_url("https://user:pass@gitlab.example.com") is None
    assert normalise_url("https://gitlab.example.com/?x=1") is None
