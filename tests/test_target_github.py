from __future__ import annotations

import json

import httpx
import pytest
from conftest import Router, make_http

from toolbot.errors import ToolbotError
from toolbot.hosts import Host
from toolbot.providers.target.base import ChangeRequest, ChangeRequestSpec
from toolbot.providers.target.github import GitHubApi, GitHubTargetProvider

HOST = Host("github", "github", "https://github.com", "TOOLBOT_GITHUB_TOKEN")
ENTERPRISE = Host("ghe", "github", "https://ghe.example.com", "GHE_TOKEN")
API = "https://api.github.com/repos/acme/tools"
SPEC = ChangeRequestSpec("toolbot/widget/2.0.0", "main", "chore(tools): update widget to 2.0.0", "body", ("toolbot",))


def pull(number: int, branch: str, state: str = "open", merged: bool = False, repository: str = "acme/tools") -> dict:  # type: ignore[type-arg]
    return {
        "number": number,
        "html_url": f"https://github.com/acme/tools/pull/{number}",
        "state": state,
        "merged_at": "2026-09-01T00:00:00Z" if merged else None,
        "title": "t",
        "head": {"ref": branch, "repo": {"full_name": repository}},
        "base": {"ref": "main"},
    }


def provider(router: Router, warnings: list[str] | None = None, host: Host = HOST) -> GitHubTargetProvider:
    sink = warnings if warnings is not None else []
    return GitHubTargetProvider(make_http(router), host, "acme/tools", "ghs_secret", sink.append)


def test_urls_and_push_credentials() -> None:
    target = provider(Router())
    assert target.repository_url() == "https://github.com/acme/tools.git"
    assert target.git_auth().username == "x-access-token"
    assert provider(Router(), host=ENTERPRISE).repository_url() == "https://ghe.example.com/acme/tools.git"


def test_find_by_branch_maps_states(router: Router) -> None:
    router.json(
        f"{API}/pulls",
        [pull(3, "toolbot/widget/2.0.0", "closed"), pull(2, "toolbot/widget/2.0.0", "closed", merged=True)],
    )
    found = provider(router).find_by_branch("toolbot/widget/2.0.0")
    assert [(request.id, request.state, request.ref) for request in found] == [(3, "closed", "#3"), (2, "merged", "#2")]
    params = router.requests[0].url.params
    assert params["head"] == "acme:toolbot/widget/2.0.0" and params["state"] == "all"
    assert router.requests[0].headers["authorization"] == "Bearer ghs_secret"


def test_list_open_ignores_other_branches_and_forks(router: Router) -> None:
    router.json(
        f"{API}/pulls",
        [
            pull(1, "toolbot/widget/1.5.0"),
            pull(2, "feature/x"),
            pull(3, "toolbot/widget/9.9.9", repository="someone/fork"),
        ],
    )
    assert [request.id for request in provider(router).list_open("toolbot/widget/", "main")] == [1]
    assert router.requests[0].url.params["base"] == "main"


def test_create_then_label(router: Router) -> None:
    router.json(f"{API}/pulls", pull(7, SPEC.source_branch), status=201, method="POST")
    router.json(f"{API}/issues/7/labels", [], method="POST")
    created = provider(router).create(SPEC)
    assert (created.id, created.ref) == (7, "#7")
    create, label = router.writes()
    assert json.loads(create.content) == {
        "title": SPEC.title,
        "head": SPEC.source_branch,
        "base": "main",
        "body": "body",
    }
    assert json.loads(label.content) == {"labels": ["toolbot"]}


def test_label_failure_is_only_a_warning(router: Router) -> None:
    router.json(f"{API}/pulls", pull(7, SPEC.source_branch), status=201, method="POST")
    router.json(f"{API}/issues/7/labels", {"message": "Forbidden"}, status=403, method="POST")
    warnings: list[str] = []
    assert provider(router, warnings).create(SPEC).id == 7
    assert len(warnings) == 1


def test_create_is_idempotent_when_pull_request_exists(router: Router) -> None:
    router.json(f"{API}/pulls", {"message": "A pull request already exists"}, status=422, method="POST")
    router.json(f"{API}/pulls", [pull(4, SPEC.source_branch)])
    assert provider(router).create(SPEC).id == 4


def test_create_failure(router: Router) -> None:
    router.json(f"{API}/pulls", {"message": "boom"}, status=500, method="POST")
    with pytest.raises(ToolbotError) as error:
        provider(router).create(SPEC)
    assert error.value.code == "TARGET_ERROR" and "ghs_secret" not in error.value.message


def test_close_comments_then_closes(router: Router) -> None:
    router.json(f"{API}/issues/5/comments", {}, status=201, method="POST")
    router.json(f"{API}/pulls/5", pull(5, "b", "closed"), method="PATCH")
    provider(router).close(ChangeRequest(5, "u", "open", "b", "main", "t", "#"), "Superseded by #6")
    comment, close = router.writes()
    assert json.loads(comment.content) == {"body": "Superseded by #6"}
    assert json.loads(close.content) == {"state": "closed"}


def test_organisation_discovery_falls_back_to_user(router: Router) -> None:
    router.json("https://api.github.com/orgs/alice/repos", {"message": "Not Found"}, status=404)
    router.json(
        "https://api.github.com/users/alice/repos",
        [{"full_name": "alice/tools", "default_branch": "main", "archived": False}],
    )
    router.add(
        "GET",
        "https://api.github.com/repos/alice/tools/contents/tools",
        lambda _request: httpx.Response(200, json=[{"name": "pact-cli", "type": "dir"}]),
    )
    api = GitHubApi(make_http(router), HOST, "token")
    assert [repository.path for repository in api.list_group_repositories("alice")] == ["alice/tools"]
    assert api.has_tools("alice/tools", "main") is True
    assert api.has_tools("alice/empty", "main") is False
