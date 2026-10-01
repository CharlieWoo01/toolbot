from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from conftest import Router, make_http

from toolbot.errors import ToolbotError
from toolbot.http import HttpClient, HttpRequest


def test_drops_credentials_on_cross_origin_redirect(router: Router) -> None:
    router.redirect("https://api.example.com/asset", "https://cdn.example.net/blob?sig=SIGNED")
    router.bytes("https://cdn.example.net/blob", "payload")
    http = make_http(router)
    text = http.get_text(
        HttpRequest("https://api.example.com/asset", {"Authorization": "Bearer t", "PRIVATE-TOKEN": "p"}), "x"
    )
    assert text == "payload"
    first, second = router.requests
    assert first.headers["authorization"] == "Bearer t"
    assert "authorization" not in second.headers and "private-token" not in second.headers


def test_keeps_credentials_on_same_origin_redirect(router: Router) -> None:
    router.redirect("https://gl.example.com/a", "/b")
    router.bytes("https://gl.example.com/b", "ok")
    make_http(router).get_text(HttpRequest("https://gl.example.com/a", {"private-token": "p"}), "x")
    assert router.requests[1].headers["private-token"] == "p"


def test_refuses_plain_http_including_via_redirect(router: Router) -> None:
    router.redirect("https://a.example/x", "http://a.example/y")
    http = make_http(router)
    with pytest.raises(ToolbotError, match="non-HTTPS"):
        http.get_text(HttpRequest("http://a.example/x"), "x")
    with pytest.raises(ToolbotError, match="non-HTTPS"):
        http.get_text(HttpRequest("https://a.example/x"), "x")


def test_redirect_loop_is_bounded(router: Router) -> None:
    router.redirect("https://a.example/loop", "/loop")
    with pytest.raises(ToolbotError, match="too many redirects"):
        make_http(router).get_text(HttpRequest("https://a.example/loop"), "x")


def test_status_mapping_and_no_secrets_in_messages(router: Router) -> None:
    router.redirect("https://api.example.com/a", "https://cdn.example.net/blob?sig=SIGNED")
    router.bytes("https://cdn.example.net/blob", "denied", status=401)
    http = make_http(router)
    with pytest.raises(ToolbotError) as err:
        http.get_text(
            HttpRequest("https://api.example.com/a", {"authorization": "Bearer secret-token"}), "asset", "set TOKEN"
        )
    assert err.value.code == "AUTH_FAILED"
    assert "set TOKEN" in err.value.message
    assert "SIGNED" not in err.value.message and "secret-token" not in err.value.message
    with pytest.raises(ToolbotError, match="404") as err404:
        http.get_text(HttpRequest("https://api.example.com/missing?token=abc"), "x")
    assert "abc" not in err404.value.message


def test_rate_limit(router: Router) -> None:
    router.add(
        "GET", "https://api.example.com/r", lambda _r: httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
    )
    with pytest.raises(ToolbotError) as err:
        make_http(router).get_json(HttpRequest("https://api.example.com/r"), "x")
    assert err.value.code == "RATE_LIMITED"


def test_retries_transient_errors_then_succeeds(router: Router) -> None:
    responses = iter([httpx.Response(503), httpx.Response(502), httpx.Response(200, json={"ok": True})])
    router.add("GET", "https://api.example.com/flaky", lambda _r: next(responses))
    sleeps: list[float] = []
    http = HttpClient(router.transport(), retries=3, backoff=0.5, sleep=sleeps.append)
    assert http.get_json(HttpRequest("https://api.example.com/flaky"), "x") == {"ok": True}
    assert sleeps == [0.5, 1.0]


def test_does_not_retry_permanent_errors(router: Router) -> None:
    router.json("https://api.example.com/gone", {}, status=404)
    with pytest.raises(ToolbotError):
        make_http(router).get_json(HttpRequest("https://api.example.com/gone"), "x")
    assert len(router.requests) == 1


def test_does_not_retry_non_idempotent_on_5xx(router: Router) -> None:
    router.json("https://api.example.com/create", {}, status=502, method="POST")
    with pytest.raises(ToolbotError):
        make_http(router).request("POST", HttpRequest("https://api.example.com/create"), "x", json={})
    assert len(router.requests) == 1


def test_retries_connection_errors() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json={})

    http = HttpClient(httpx.MockTransport(handler), retries=3, backoff=0, sleep=lambda _s: None)
    http.get_json(HttpRequest("https://a.example/x"), "x")
    assert attempts == 3


def test_download_streams_and_enforces_limits(router: Router, tmp_path: Path) -> None:
    router.bytes("https://a.example/f", "content")
    router.bytes("https://a.example/big", "x" * 100)
    router.add(
        "GET",
        "https://a.example/short",
        lambda _r: httpx.Response(200, content=b"short", headers={"content-length": "999"}),
    )
    http = make_http(router)
    assert http.download(HttpRequest("https://a.example/f"), tmp_path / "f", "x", 100) == 7
    assert (tmp_path / "f").read_text() == "content"
    with pytest.raises(ToolbotError, match="limit"):
        http.download(HttpRequest("https://a.example/big"), tmp_path / "big", "x", 10)
    with pytest.raises(ToolbotError, match="truncated"):
        http.download(HttpRequest("https://a.example/short"), tmp_path / "short", "x", 5000)
    assert not (tmp_path / "short").exists()  # partial files are discarded


class DropsMidway(httpx.SyncByteStream):
    def __iter__(self):  # type: ignore[no-untyped-def]
        yield b"first half"
        raise httpx.ReadTimeout("stalled")


def test_interrupted_download_is_retried_from_the_start(router: Router, tmp_path: Path) -> None:
    attempts = 0

    def flaky(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(200, stream=DropsMidway())
        return httpx.Response(200, content=b"complete file")

    router.add("GET", "https://a.example/big", flaky)
    http = make_http(router)
    assert http.download(HttpRequest("https://a.example/big"), tmp_path / "big", "x", 1000) == 13
    assert (tmp_path / "big").read_bytes() == b"complete file"
    assert attempts == 2


def test_download_gives_up_after_the_retries(router: Router, tmp_path: Path) -> None:
    router.add("GET", "https://a.example/big", lambda _r: httpx.Response(200, stream=DropsMidway()))
    with pytest.raises(ToolbotError, match="download interrupted: ReadTimeout"):
        make_http(router).download(HttpRequest("https://a.example/big"), tmp_path / "big", "x", 1000)
    assert len(router.requests) == 3  # retries=2 in tests
    assert not (tmp_path / "big").exists()
