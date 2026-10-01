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
REDIRECTABLE_METHODS = frozenset({"GET", "HEAD"})
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE"})
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


def display_url(raw_url: str) -> str:
    """URL without query/fragment, for messages."""
    try:
        parts = urlsplit(raw_url)
    except ValueError:
        return "<invalid url>"
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def same_origin(first_url: str, second_url: str) -> bool:
    try:
        first, second = urlsplit(first_url), urlsplit(second_url)
    except ValueError:
        return False
    return (first.scheme, first.hostname, first.port) == (second.scheme, second.hostname, second.port)


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

    # -- reads -------------------------------------------------------------------

    def get_json(self, request: HttpRequest, what: str, auth_hint: str = "") -> Any:
        response = self._send("GET", request, what, auth_hint)
        try:
            return response.json()
        except ValueError:
            raise ToolbotError("HTTP_ERROR", f"{what}: response from {display_url(request.url)} was not JSON") from None

    def get_text(self, request: HttpRequest, what: str, auth_hint: str = "", max_bytes: int = 1024 * 1024) -> str:
        response = self._send("GET", request, what, auth_hint)
        if len(response.content) > max_bytes:
            raise ToolbotError("HTTP_ERROR", f"{what}: response exceeds {max_bytes} bytes")
        return response.content.decode("utf-8", errors="replace")

    def exists(self, request: HttpRequest, what: str, auth_hint: str = "") -> bool:
        """HEAD request (following redirects): True on success, False on 404."""
        try:
            self._send("HEAD", request, what, auth_hint)
        except HttpError as error:
            if error.status == 404:
                return False
            if error.status == 405:  # server doesn't support HEAD; can't tell without downloading
                return True
            raise
        return True

    def download(self, request: HttpRequest, path: Path, what: str, max_bytes: int, auth_hint: str = "") -> int:
        """Stream the body to ``path`` (must not exist). Returns the byte count.

        A transfer interrupted or truncated midway (common for 100 MB+ files)
        is retried from the start; the partial file is always discarded.
        """
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._download_once(request, path, what, max_bytes, auth_hint)
            except _InterruptedDownload as interrupted:
                path.unlink(missing_ok=True)
                if attempt > self._retries:
                    raise ToolbotError("HTTP_ERROR", interrupted.message) from None
                self._sleep(min(self._backoff * 2 ** (attempt - 1), 60.0))
            except BaseException:
                path.unlink(missing_ok=True)
                raise

    def _download_once(self, request: HttpRequest, path: Path, what: str, max_bytes: int, auth_hint: str) -> int:
        response = self._open_stream(request, what, auth_hint)
        try:
            declared_length = response.headers.get("content-length")
            expected_bytes = int(declared_length) if declared_length and declared_length.isdigit() else None
            if expected_bytes is not None and expected_bytes > max_bytes:
                raise ToolbotError("HTTP_ERROR", f"{what}: {expected_bytes} bytes exceeds the {max_bytes} byte limit")
            received_bytes = 0
            try:
                with path.open("xb") as output:
                    for chunk in response.iter_bytes():
                        received_bytes += len(chunk)
                        if received_bytes > max_bytes:
                            raise ToolbotError("HTTP_ERROR", f"{what}: download exceeds the {max_bytes} byte limit")
                        output.write(chunk)
            except httpx.TransportError as error:
                raise _InterruptedDownload(f"{what}: download interrupted: {type(error).__name__}") from None
        finally:
            response.close()
        if expected_bytes is not None and received_bytes != expected_bytes:
            raise _InterruptedDownload(f"{what}: truncated download ({received_bytes} of {expected_bytes} bytes)")
        return received_bytes

    # -- writes (target provider APIs) ---------------------------------------------

    def request(
        self,
        method: str,
        request: HttpRequest,
        what: str,
        *,
        json: Any = None,
        params: Mapping[str, str | int] | None = None,
    ) -> httpx.Response:
        return self._send(method, request, what, "", json=json, params=params)

    # -- internals -------------------------------------------------------------------

    def _send(
        self,
        method: str,
        request: HttpRequest,
        what: str,
        auth_hint: str,
        *,
        json: Any = None,
        params: Mapping[str, str | int] | None = None,
    ) -> httpx.Response:
        url = request.url
        headers = {name.lower(): value for name, value in request.headers.items()}
        for _hop in range(self._max_redirects + 1):
            require_https(url, what)

            def send(current_url: str = url, current_headers: dict[str, str] = headers) -> httpx.Response:
                return self._client.request(method, current_url, headers=current_headers, json=json, params=params)

            response = self._with_retries(method, what, send)
            if response.is_redirect and method in REDIRECTABLE_METHODS:
                url = str(response.next_request.url) if response.next_request else ""
                response.close()
                if not same_origin(url, request.url):
                    headers = without_credentials(headers)
                continue
            if response.is_success:
                return response
            raise status_error(response, what, request.url, auth_hint)
        raise ToolbotError("HTTP_ERROR", f"{what}: too many redirects from {display_url(request.url)}")

    def _open_stream(self, request: HttpRequest, what: str, auth_hint: str) -> httpx.Response:
        url = request.url
        headers = {name.lower(): value for name, value in request.headers.items()}
        for _hop in range(self._max_redirects + 1):
            require_https(url, what)

            def open_stream(current_url: str = url, current_headers: dict[str, str] = headers) -> httpx.Response:
                built = self._client.build_request("GET", current_url, headers=current_headers)
                return self._client.send(built, stream=True)

            response = self._with_retries("GET", what, open_stream)
            if response.is_redirect:
                url = str(response.next_request.url) if response.next_request else ""
                response.close()
                if not same_origin(url, request.url):
                    headers = without_credentials(headers)
                continue
            if response.is_success:
                return response
            response.read()
            response.close()
            raise status_error(response, what, request.url, auth_hint)
        raise ToolbotError("HTTP_ERROR", f"{what}: too many redirects from {display_url(request.url)}")

    def _with_retries(self, method: str, what: str, send: Callable[[], httpx.Response]) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            try:
                response = send()
            except (httpx.ConnectError, httpx.ConnectTimeout) as error:
                # The request never reached the server: safe to retry for any method.
                if attempt > self._retries:
                    raise ToolbotError("HTTP_ERROR", f"{what}: connection failed: {type(error).__name__}") from None
            except httpx.TransportError as error:
                if method not in IDEMPOTENT_METHODS or attempt > self._retries:
                    raise ToolbotError("HTTP_ERROR", f"{what}: request failed: {type(error).__name__}") from None
            else:
                retryable = response.status_code in TRANSIENT_STATUS and method in IDEMPOTENT_METHODS
                if not retryable or attempt > self._retries:
                    return response
                delay = retry_after_seconds(response) or self._backoff * 2 ** (attempt - 1)
                response.close()
                self._sleep(min(delay, 60.0))
                continue
            self._sleep(min(self._backoff * 2 ** (attempt - 1), 60.0))


class _InterruptedDownload(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def without_credentials(headers: dict[str, str]) -> dict[str, str]:
    return {name: value for name, value in headers.items() if name not in SENSITIVE_HEADERS}


def retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after", "")
    return float(value) if value.isdigit() else None


def require_https(url: str, what: str) -> None:
    try:
        parts = urlsplit(url)
    except ValueError:
        raise ToolbotError("HTTP_ERROR", f"{what}: invalid URL") from None
    if parts.scheme != "https":
        raise ToolbotError("HTTP_ERROR", f"{what}: refusing non-HTTPS URL {display_url(url)}")
    if parts.username or parts.password:
        raise ToolbotError("HTTP_ERROR", f"{what}: refusing URL with embedded credentials")


def status_error(response: httpx.Response, what: str, requested_url: str, auth_hint: str) -> HttpError:
    location = f"{what} ({display_url(requested_url)})"
    hint = f" — {auth_hint}" if auth_hint else ""
    status = response.status_code
    remaining = response.headers.get("x-ratelimit-remaining") or response.headers.get("ratelimit-remaining")
    if status == 429 or (status == 403 and remaining == "0"):
        return HttpError("RATE_LIMITED", status, f"{location}: API rate limit exceeded{hint}")
    if status == 401:
        return HttpError(
            "AUTH_FAILED",
            status,
            f"{location}: authentication failed (HTTP 401); token missing, expired or invalid{hint}",
        )
    if status == 403:
        return HttpError("AUTH_FAILED", status, f"{location}: access forbidden (HTTP 403){hint}")
    if status == 404:
        return HttpError("HTTP_ERROR", status, f"{location}: not found (HTTP 404){hint}")
    return HttpError("HTTP_ERROR", status, f"{location}: unexpected HTTP {status}")
