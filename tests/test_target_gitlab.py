from __future__ import annotations

import json

import httpx
import pytest
from conftest import Router, make_http

from toolbot.errors import ToolbotError
from toolbot.hosts import Host
from toolbot.providers.target.base import ChangeRequest, ChangeRequestSpec
from toolbot.providers.target.gitlab import GitLabApi, GitLabTargetProvider

HOST = Host("internal", "gitlab", "https://gitlab.example.com", "TOOLBOT_GITLAB_TOKEN")
API = "https://gitlab.example.com/api/v4/projects/team%2Fci-tools"
SPEC = ChangeRequestSpec(
    "toolbot/widget/2.0.0", "main", "chore(tools): update widget to 2.0.0", "body", ("toolbot", "tool-update")
)


def merge_request(number: int, branch: str, state: str = "opened") -> dict[str, object]:
    return {
        "iid": number,
        "web_url": f"https://gitlab.example.com/team/ci-tools/-/merge_requests/{number}",
        "state": state,
        "source_branch": branch,
        "target_branch": "main",
        "title": "t",
    }


def provider(router: Router, warnings: list[str] | None = None) -> GitLabTargetProvider:
    sink = warnings if warnings is not None else []
    return GitLabTargetProvider(make_http(router), HOST, "team/ci-tools", "glpat-secret", sink.append)


def test_repository_url_and_push_credentials() -> None:
    target = provider(Router())
    assert target.repository_url() == "https://gitlab.example.com/team/ci-tools.git"
    assert target.git_auth().username == "oauth2"


def test_find_by_branch_maps_states(router: Router) -> None:
    router.json(
        f"{API}/merge_requests",
        [merge_request(3, "toolbot/widget/2.0.0", "closed"), merge_request(2, "toolbot/widget/2.0.0", "merged")],
    )
    found = provider(router).find_by_branch("toolbot/widget/2.0.0")
    assert [(request.id, request.state, request.ref) for request in found] == [(3, "closed", "!3"), (2, "merged", "!2")]
    params = router.requests[0].url.params
    assert params["source_branch"] == "toolbot/widget/2.0.0" and params["state"] == "all"
    assert router.requests[0].headers["private-token"] == "glpat-secret"


def test_list_open_filters_to_the_tools_branches(router: Router) -> None:
    router.json(
        f"{API}/merge_requests",
        [
            merge_request(1, "toolbot/widget/1.5.0"),
            merge_request(2, "feature/x"),
            merge_request(3, "toolbot/widget-extra/1.0"),
        ],
    )
    assert [request.id for request in provider(router).list_open("toolbot/widget/", "main")] == [1]
    params = router.to(f"{API}/merge_requests")[0].url.params
    assert params["state"] == "opened" and params["target_branch"] == "main"
    assert "author_id" not in params  # found by branch, so token rotation doesn't orphan MRs


def test_pagination(router: Router) -> None:
    router.add(
        "GET",
        f"{API}/merge_requests",
        lambda request: httpx.Response(
            200,
            json=[merge_request(int(request.url.params["page"]), "toolbot/widget/1")],
            headers={"x-next-page": "2"} if request.url.params["page"] == "1" else {},
        ),
    )
    assert [request.id for request in provider(router).find_by_branch("toolbot/widget/1")] == [1, 2]


def test_create_ensures_labels(router: Router) -> None:
    router.add(
        "GET",
        f"{API}/labels",
        lambda request: httpx.Response(
            200, json=[{"name": "toolbot"}] if request.url.params["search"] == "toolbot" else []
        ),
    )
    router.json(f"{API}/labels", {"name": "tool-update"}, status=201, method="POST")
    router.json(f"{API}/merge_requests", merge_request(9, SPEC.source_branch), status=201, method="POST")
    created = provider(router).create(SPEC)
    assert created.id == 9 and created.state == "open"
    label_posts = [request for request in router.writes() if request.url.path.endswith("/labels")]
    assert [json.loads(request.content)["name"] for request in label_posts] == ["tool-update"]
    body = json.loads(router.writes()[-1].content)
    assert body["labels"] == "toolbot,tool-update" and body["source_branch"] == SPEC.source_branch
    assert body["remove_source_branch"] is True


def test_labels_that_cannot_be_created_are_dropped_with_warning(router: Router) -> None:
    router.json(f"{API}/labels", [])
    router.json(f"{API}/labels", {"message": "403 Forbidden"}, status=403, method="POST")
    router.json(f"{API}/merge_requests", merge_request(9, SPEC.source_branch), status=201, method="POST")
    warnings: list[str] = []
    provider(router, warnings).create(SPEC)
    assert json.loads(router.writes()[-1].content)["labels"] == ""
    assert len(warnings) == 2


def test_create_is_idempotent_on_conflict(router: Router) -> None:
    router.json(f"{API}/labels", [{"name": "toolbot"}, {"name": "tool-update"}])
    router.json(
        f"{API}/merge_requests", {"message": ["Another open merge request already exists"]}, status=409, method="POST"
    )
    router.json(f"{API}/merge_requests", [merge_request(4, SPEC.source_branch)])
    assert provider(router).create(SPEC).id == 4


def test_create_failure(router: Router) -> None:
    router.json(f"{API}/labels", [{"name": "toolbot"}, {"name": "tool-update"}])
    router.json(f"{API}/merge_requests", {"message": "boom"}, status=500, method="POST")
    with pytest.raises(ToolbotError) as error:
        provider(router).create(SPEC)
    assert error.value.code == "TARGET_ERROR" and "glpat-secret" not in error.value.message


def test_close_comments_then_closes(router: Router) -> None:
    router.json(f"{API}/merge_requests/5/notes", {}, status=201, method="POST")
    router.json(f"{API}/merge_requests/5", merge_request(5, "b", "closed"), method="PUT")
    provider(router).close(ChangeRequest(5, "u", "open", "b", "main", "t"), "Superseded by !6")
    note, close = router.writes()
    assert json.loads(note.content) == {"body": "Superseded by !6"}
    assert json.loads(close.content) == {"state_event": "close"}


def test_default_branch(router: Router) -> None:
    router.json(API, {"default_branch": "trunk"})
    assert provider(router).default_branch() == "trunk"


def test_group_discovery_and_tools_detection(router: Router) -> None:
    router.json(
        "https://gitlab.example.com/api/v4/groups/platform/projects",
        [
            {"path_with_namespace": "platform/ci-tools", "default_branch": "main"},
            {"path_with_namespace": "platform/old", "default_branch": "main", "archived": True},
        ],
    )
    router.json("https://gitlab.example.com/api/v4/projects/platform%2Fci-tools/repository/tree", [{"name": "pact"}])
    api = GitLabApi(make_http(router), HOST, "glpat")
    repositories = api.list_group_repositories("platform")
    assert [(repository.path, repository.archived) for repository in repositories] == [
        ("platform/ci-tools", False),
        ("platform/old", True),
    ]
    assert router.requests[0].url.params["include_subgroups"] == "true"
    assert api.has_tools("platform/ci-tools", "main") is True
    assert api.has_tools("platform/missing", "main") is False  # 404
