"""Hosts: the GitHub and GitLab instances Toolbot talks to, and their tokens.

A host is a named platform instance, e.g. github.com, gitlab.com, an internal
self-managed GitLab or a GitHub Enterprise server. Hosts are declared in the
control configuration (``toolbot.yaml``) and referenced by name from targets
and, optionally, from tool definitions.

Tokens are never configured directly: each host names the *environment
variable* that holds its token. A token is only ever sent to URLs that belong
to its own host.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

HOST_TYPES = ("github", "gitlab")
ENVIRONMENT_VARIABLE_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")

GITHUB_COM = "https://github.com"
GITLAB_COM = "https://gitlab.com"


@dataclass(frozen=True)
class Host:
    name: str
    """Alias used in toolbot.yaml and tool definitions, e.g. ``internal-gitlab``."""
    type: str
    """``github`` or ``gitlab``."""
    url: str
    """Normalised web URL without trailing slash, e.g. ``https://gitlab.example.com``."""
    token_env: str | None = None
    """Name of the environment variable holding this host's token (optional)."""

    @property
    def api_url(self) -> str:
        if self.type == "github":
            return "https://api.github.com" if self.url == GITHUB_COM else f"{self.url}/api/v3"
        return f"{self.url}/api/v4"

    def token(self, environment: Mapping[str, str]) -> str:
        return environment.get(self.token_env, "") if self.token_env else ""

    def owns(self, url: str) -> bool:
        """True if ``url`` is on this host's web or API address."""
        return _is_under(url, self.url) or _is_under(url, self.api_url)


def _is_under(url: str, base: str) -> bool:
    try:
        target, prefix = urlsplit(url), urlsplit(base)
    except ValueError:
        return False
    same_origin = (target.scheme, target.hostname, target.port) == (prefix.scheme, prefix.hostname, prefix.port)
    base_path = prefix.path.rstrip("/")
    return same_origin and (target.path == base_path or target.path.startswith(base_path + "/"))


class HostRegistry:
    """Known hosts, first match wins. Unknown URLs get no credentials."""

    def __init__(self, hosts: Sequence[Host], environment: Mapping[str, str]) -> None:
        self._hosts = list(hosts)
        self._environment = environment

    def get(self, name: str) -> Host | None:
        return next((host for host in self._hosts if host.name == name), None)

    def names(self) -> list[str]:
        return [host.name for host in self._hosts]

    def find(self, url: str) -> Host | None:
        return next((host for host in self._hosts if host.owns(url)), None)

    def token(self, host: Host) -> str:
        return host.token(self._environment)

    def auth_headers(self, url: str) -> dict[str, str]:
        """Credentials for a request to ``url``, if it belongs to a host with a token."""
        host = self.find(url)
        token = self.token(host) if host else ""
        if not host or not token:
            return {}
        if host.type == "github":
            return {"authorization": f"Bearer {token}"}
        return {"private-token": token}


def default_hosts() -> list[Host]:
    """Always-available public hosts. Configured hosts with the same URL take precedence."""
    return [
        Host("github", "github", GITHUB_COM, "TOOLBOT_GITHUB_TOKEN"),
        Host("gitlab", "gitlab", GITLAB_COM, "TOOLBOT_GITLAB_SOURCE_TOKEN"),
    ]


def merge_hosts(configured: Sequence[Host]) -> list[Host]:
    """Configured hosts first, then defaults not already covered by name or URL."""
    names = {host.name for host in configured}
    urls = {host.url for host in configured}
    return [*configured, *(host for host in default_hosts() if host.name not in names and host.url not in urls)]


def normalise_url(value: str) -> str | None:
    """``https://host[:port][/path]`` without trailing slash, or None if unacceptable."""
    try:
        url = urlsplit(value)
    except ValueError:
        return None
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        return None
    netloc = url.hostname + (f":{url.port}" if url.port else "")
    return f"https://{netloc}{url.path.rstrip('/')}"
