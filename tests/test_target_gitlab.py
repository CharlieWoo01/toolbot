from __future__ import annotations

import json

import httpx
import pytest
from conftest import Router, make_http

from toolbot.errors import ToolbotError
from toolbot.providers.target.base import ChangeRequest, ChangeRequestSpec
from toolbot.providers.target.gitlab import GitLabTargetProvider

API = "https://gitlab.example.com/api/v4/projects/team%2Fci-tools"
SPEC = ChangeRequestSpec(
    "toolbot/widget/2.0.0", "main", "chore(tools): update widget to 2.0.0", "body", ("toolbot", "tool-update")
)


def mr(iid: int, branch: str, state: str = "opened") -> dict[str, object]:
    return {
        "iid": iid,
        "web_url": f"https://gitlab.example.com/team/ci-tools/-/merge_requests/{iid}",
        "state": state,
        "source_branch": branch,
        "target_branch": "main",
        "title": "t",
    }


def provider(router: Router, warnings: list[str] | None = None) -> GitLabTargetProvider:
    sink = warnings if warnings is not None else []
    return GitLabTargetProvider(
        make_http(router), "https://gitlab.example.com", "team/ci-tools", "glpat-secret", sink.append
    )


def test_from_env_requires_configuration() -> None:
    with pytest.raises(ToolbotError, match="TOOLBOT_GITLAB_TOKEN"):
        GitLabTargetProvider.from_env(
            make_http(Router()), {"CI_SERVER_URL": "https://gitlab.example.com", "CI_PROJECT_PATH": "a/b"}
        )
    target = GitLabTargetProvider.from_env(
        make_http(Router()),
        {
            "CI_SERVER_URL": "https://gitlab.example.com",
            "CI_PROJECT_PATH": "team/ci-tools",
            "TOOLBOT_GITLAB_TOKEN": "t",
        },
    )
    assert target.repository_url() == "https://gitlab.example.com/team/ci-tools.git"
    assert target.git_auth().username == "oauth2"


def test_find_by_branch_maps_states(router: Router) -> None:
    router.json(
        f"{API}/merge_requests", [mr(3, "toolbot/widget/2.0.0", "closed"), mr(2, "toolbot/widget/2.0.0", "merged")]
    )
    found = provider(router).find_by_branch("toolbot/widget/2.0.0")
    assert [(r.id, r.state) for r in found] == [(3, "closed"), (2, "merged")]
    params = router.requests[0].url.params
    assert params["source_branch"] == "toolbot/widget/2.0.0" and params["state"] == "all"
    assert router.requests[0].headers["private-token"] == "glpat-secret"


def test_list_open_filters_to_toolbot_branches_of_the_tool(router: Router) -> None:
    router.json(
        f"{API}/merge_requests", [mr(1, "toolbot/widget/1.5.0"), mr(2, "feature/x"), mr(3, "toolbot/widget-extra/1.0")]
    )
    found = provider(router).list_open("toolbot/widget/", "main")
    assert [r.id for r in found] == [1]
    params = router.to(f"{API}/merge_requests")[0].url.params
    assert params["state"] == "opened" and params["target_branch"] == "main"
    assert "author_id" not in params  # found by branch, so token rotation doesn't orphan MRs


def test_pagination(router: Router) -> None:
    router.add(
        "GET",
        f"{API}/merge_requests",
        lambda r: httpx.Response(
            200,
            json=[mr(int(r.url.params["page"]), "toolbot/widget/1")],
            headers={"x-next-page": "2"} if r.url.params["page"] == "1" else {},
        ),
    )
    assert [r.id for r in provider(router).find_by_branch("toolbot/widget/1")] == [1, 2]


def test_create_ensures_labels(router: Router) -> None:
    router.add(
        "GET",
        f"{API}/labels",
        lambda r: httpx.Response(200, json=[{"name": "toolbot"}] if r.url.params["search"] == "toolbot" else []),
    )
    router.json(f"{API}/labels", {"name": "tool-update"}, status=201, method="POST")
    router.json(f"{API}/merge_requests", mr(9, SPEC.source_branch), status=201, method="POST")
    created = provider(router).create(SPEC)
    assert created.id == 9 and created.state == "open"
    label_posts = [r for r in router.writes() if r.url.path.endswith("/labels")]
    assert [json.loads(r.content)["name"] for r in label_posts] == ["tool-update"]
    body = json.loads(router.writes()[-1].content)
    assert body["labels"] == "toolbot,tool-update" and body["source_branch"] == SPEC.source_branch
    assert body["remove_source_branch"] is True


def test_labels_that_cannot_be_created_are_dropped_with_warning(router: Router) -> None:
    router.json(f"{API}/labels", [])
    router.json(f"{API}/labels", {"message": "403 Forbidden"}, status=403, method="POST")
    router.json(f"{API}/merge_requests", mr(9, SPEC.source_branch), status=201, method="POST")
    warnings: list[str] = []
    provider(router, warnings).create(SPEC)
    assert json.loads(router.writes()[-1].content)["labels"] == ""
    assert len(warnings) == 2


def test_create_is_idempotent_on_conflict(router: Router) -> None:
    router.json(f"{API}/labels", [{"name": "toolbot"}, {"name": "tool-update"}])
    router.json(
        f"{API}/merge_requests", {"message": ["Another open merge request already exists"]}, status=409, method="POST"
    )
    router.json(f"{API}/merge_requests", [mr(4, SPEC.source_branch)])
    assert provider(router).create(SPEC).id == 4


def test_create_failure(router: Router) -> None:
    router.json(f"{API}/labels", [{"name": "toolbot"}, {"name": "tool-update"}])
    router.json(f"{API}/merge_requests", {"message": "boom"}, status=500, method="POST")
    with pytest.raises(ToolbotError) as err:
        provider(router).create(SPEC)
    assert err.value.code == "TARGET_ERROR" and "glpat-secret" not in err.value.message


def test_close_comments_then_closes(router: Router) -> None:
    router.json(f"{API}/merge_requests/5/notes", {}, status=201, method="POST")
    router.json(f"{API}/merge_requests/5", mr(5, "b", "closed"), method="PUT")
    provider(router).close(ChangeRequest(5, "u", "open", "b", "main", "t"), "Superseded by !6")
    note, close = router.writes()
    assert json.loads(note.content) == {"body": "Superseded by !6"}
    assert json.loads(close.content) == {"state_event": "close"}


def test_default_branch(router: Router) -> None:
    router.json(API, {"default_branch": "trunk"})
    assert provider(router).default_branch() == "trunk"
