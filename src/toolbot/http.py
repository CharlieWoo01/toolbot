"""Small HTTP layer on top of httpx with the safety properties Toolbot needs.

* HTTPS only, for every request and every redirect hop.
* Redirects are followed manually; credential headers are dropped as soon as
  a redirect leaves the original origin (e.g. GitHub API -> CDN).
* Connect/read timeouts, bounded retries with backoff for transient failures
  (connection errors, 429, 5xx) on idempotent requests only.
* Download size cap and truncation detection.
* Error messages never include headers, and URLs are shown without their
  query string (signed CDN URLs carry short-lived credentials).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from toolbot import __version__
from toolbot.errors import ErrorCode, ToolbotError

SENSITIVE_HEADERS = frozenset({"authorization", "private-token", "job-token", "cookie"})
TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# httpx logs full URLs at INFO; redirect targets can contain signed credentials.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


@dataclass(frozen=True)
class HttpRequest:
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)


class HttpError(ToolbotError):
    def __init__(self, code: ErrorCode, status: int, message: str) -> None:
        super().__init__(code, message)
        self.status = status


def display_url(raw: str) -> str:
    """URL without query/fragment, for messages."""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return "<invalid url>"
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def same_origin(a: str, b: str) -> bool:
    try:
        pa, pb = urlsplit(a), urlsplit(b)
    except ValueError:
        return False
    return (pa.scheme, pa.hostname, pa.port) == (pb.scheme, pb.hostname, pb.port)


class HttpClient:
    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        *,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        max_redirects: int = 5,
        retries: int = 3,
        backoff: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            headers={"user-agent": f"toolbot/{__version__}"},
        )
        self._max_redirects = max_redirects
        self._retries = retries
        self._backoff = backoff
        self._sleep = sleep

    def close(self) -> None:
        self._client.close()

    # -- reads ---------------------------------------------------------------

    def get_json(self, req: HttpRequest, what: str, auth_hint: str = "") -> Any:
        response = self._send("GET", req, what, auth_hint)
        try:
            return response.json()
        except ValueError:
            raise ToolbotError("HTTP_ERROR", f"{what}: response from {display_url(req.url)} was not JSON") from None

    def get_text(self, req: HttpRequest, what: str, auth_hint: str = "", max_bytes: int = 1024 * 1024) -> str:
        response = self._send("GET", req, what, auth_hint)
        if len(response.content) > max_bytes:
            raise ToolbotError("HTTP_ERROR", f"{what}: response exceeds {max_bytes} bytes")
        return response.content.decode("utf-8", errors="replace")

    def download(self, req: HttpRequest, path: Path, what: str, max_bytes: int, auth_hint: str = "") -> int:
        """Stream the body to ``path`` (must not exist). Returns the byte count."""
        with self._stream(req, what, auth_hint) as response:
            declared = response.headers.get("content-length")
            expected = int(declared) if declared and declared.isdigit() else None
            if expected is not None and expected > max_bytes:
                raise ToolbotError("HTTP_ERROR", f"{what}: {expected} bytes exceeds the {max_bytes} byte limit")
            received = 0
            try:
                with path.open("xb") as out:
                    for chunk in response.iter_bytes():
                        received += len(chunk)
                        if received > max_bytes:
                            raise ToolbotError("HTTP_ERROR", f"{what}: download exceeds the {max_bytes} byte limit")
                        out.write(chunk)
            except httpx.TransportError as err:
                raise ToolbotError("HTTP_ERROR", f"{what}: download interrupted: {type(err).__name__}") from None
        if expected is not None and received != expected:
            raise ToolbotError("HTTP_ERROR", f"{what}: truncated download ({received} of {expected} bytes)")
        return received

    # -- writes (target provider APIs) ----------------------------------------

    def request(
        self,
        method: str,
        req: HttpRequest,
        what: str,
        *,
        json: Any = None,
        params: Mapping[str, str | int] | None = None,
    ) -> httpx.Response:
        return self._send(method, req, what, "", json=json, params=params)

    # -- internals -------------------------------------------------------------

    def _send(
        self,
        method: str,
        req: HttpRequest,
        what: str,
        auth_hint: str,
        *,
        json: Any = None,
        params: Mapping[str, str | int] | None = None,
    ) -> httpx.Response:
        url = req.url
        headers = {k.lower(): v for k, v in req.headers.items()}
        origin = url
        for _hop in range(self._max_redirects + 1):
            _require_https(url, what)

            def send(u: str = url, h: dict[str, str] = headers) -> httpx.Response:
                return self._client.request(method, u, headers=h, json=json, params=params)

            response = self._with_retries(method, what, send)
            if response.is_redirect and method == "GET":
                url = str(response.next_request.url) if response.next_request else ""
                response.close()
                if not same_origin(url, origin):
                    headers = {k: v for k, v in headers.items() if k not in SENSITIVE_HEADERS}
                continue
            if response.is_success:
                return response
            raise _status_error(response, what, req.url, auth_hint)
        raise ToolbotError("HTTP_ERROR", f"{what}: too many redirects from {display_url(req.url)}")

    def _stream(self, req: HttpRequest, what: str, auth_hint: str) -> _StreamContext:
        return _StreamContext(self, req, what, auth_hint)

    def _open_stream(self, req: HttpRequest, what: str, auth_hint: str) -> httpx.Response:
        url = req.url
        headers = {k.lower(): v for k, v in req.headers.items()}
        for _hop in range(self._max_redirects + 1):
            _require_https(url, what)

            def open_(u: str = url, h: dict[str, str] = headers) -> httpx.Response:
                return self._client.send(self._client.build_request("GET", u, headers=h), stream=True)

            response = self._with_retries("GET", what, open_)
            if response.is_redirect:
                url = str(response.next_request.url) if response.next_request else ""
                response.close()
                if not same_origin(url, req.url):
                    headers = {k: v for k, v in headers.items() if k not in SENSITIVE_HEADERS}
                continue
            if response.is_success:
                return response
            response.read()
            response.close()
            raise _status_error(response, what, req.url, auth_hint)
        raise ToolbotError("HTTP_ERROR", f"{what}: too many redirects from {display_url(req.url)}")

    def _with_retries(self, method: str, what: str, send: Callable[[], httpx.Response]) -> httpx.Response:
        idempotent = method in ("GET", "HEAD", "PUT", "DELETE")
        attempt = 0
        while True:
            attempt += 1
            try:
                response = send()
            except (httpx.ConnectError, httpx.ConnectTimeout) as err:
                # The request never reached the server: safe to retry for any method.
                if attempt > self._retries:
                    raise ToolbotError("HTTP_ERROR", f"{what}: connection failed: {type(err).__name__}") from None
            except httpx.TransportError as err:
                if not idempotent or attempt > self._retries:
                    raise ToolbotError("HTTP_ERROR", f"{what}: request failed: {type(err).__name__}") from None
            else:
                if response.status_code not in TRANSIENT_STATUS or not idempotent or attempt > self._retries:
                    return response
                delay = _retry_after(response) or self._backoff * 2 ** (attempt - 1)
                response.close()
                self._sleep(min(delay, 60.0))
                continue
            self._sleep(min(self._backoff * 2 ** (attempt - 1), 60.0))


class _StreamContext:
    def __init__(self, client: HttpClient, req: HttpRequest, what: str, auth_hint: str) -> None:
        self._args = (client, req, what, auth_hint)
        self._response: httpx.Response | None = None

    def __enter__(self) -> httpx.Response:
        client, req, what, hint = self._args
        self._response = client._open_stream(req, what, hint)
        return self._response

    def __exit__(self, *exc: object) -> None:
        if self._response is not None:
            self._response.close()


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after", "")
    return float(value) if value.isdigit() else None


def _require_https(url: str, what: str) -> None:
    try:
        parts = urlsplit(url)
    except ValueError:
        raise ToolbotError("HTTP_ERROR", f"{what}: invalid URL") from None
    if parts.scheme != "https":
        raise ToolbotError("HTTP_ERROR", f"{what}: refusing non-HTTPS URL {display_url(url)}")
    if parts.username or parts.password:
        raise ToolbotError("HTTP_ERROR", f"{what}: refusing URL with embedded credentials")


def _status_error(response: httpx.Response, what: str, requested: str, auth_hint: str) -> HttpError:
    where = f"{what} ({display_url(requested)})"
    hint = f" — {auth_hint}" if auth_hint else ""
    status = response.status_code
    remaining = response.headers.get("x-ratelimit-remaining") or response.headers.get("ratelimit-remaining")
    if status == 429 or (status == 403 and remaining == "0"):
        return HttpError("RATE_LIMITED", status, f"{where}: API rate limit exceeded{hint}")
    if status == 401:
        return HttpError(
            "AUTH_FAILED", status, f"{where}: authentication failed (HTTP 401); token missing, expired or invalid{hint}"
        )
    if status == 403:
        return HttpError("AUTH_FAILED", status, f"{where}: access forbidden (HTTP 403){hint}")
    if status == 404:
        return HttpError("HTTP_ERROR", status, f"{where}: not found (HTTP 404){hint}")
    return HttpError("HTTP_ERROR", status, f"{where}: unexpected HTTP {status}")
