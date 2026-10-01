"""Shared dependencies, injectable for tests."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from toolbot.config.control import ControlConfig
from toolbot.hosts import GITHUB_COM, Host, HostRegistry, merge_hosts, normalise_url
from toolbot.http import HttpClient
from toolbot.providers.source import SourceProviders, create_source_providers

DEFAULT_MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024  # 1 GiB


def print_to_stderr(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


@dataclass
class Context:
    http: HttpClient
    providers: SourceProviders
    env: Mapping[str, str]
    control: ControlConfig = field(default_factory=ControlConfig)
    log: Callable[[str], None] = print_to_stderr
    temp_root: Path | None = None
    """Parent directory for per-run temp dirs (default: the OS temp dir)."""
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES
    warnings: list[str] = field(default_factory=list)

    @property
    def hosts(self) -> HostRegistry:
        return self.providers.hosts

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        self.log(f"warning: {message}")


def ci_hosts(environment: Mapping[str, str]) -> list[Host]:
    """The instance Toolbot runs in (GitLab CI / GitHub Actions), with the default target token.

    Registering it means upstream projects on the same instance are read with
    the same token, so one token covers both.
    """
    hosts: list[Host] = []
    gitlab_url = normalise_url(environment.get("TOOLBOT_GITLAB_URL") or environment.get("CI_SERVER_URL") or "")
    if gitlab_url:
        hosts.append(Host("ci-gitlab", "gitlab", gitlab_url, "TOOLBOT_GITLAB_TOKEN"))
    github_url = normalise_url(environment.get("TOOLBOT_GITHUB_URL") or environment.get("GITHUB_SERVER_URL") or "")
    if github_url and github_url != GITHUB_COM:
        hosts.append(Host("ci-github", "github", github_url, "TOOLBOT_GITHUB_TOKEN"))
    return hosts


def build_host_registry(control: ControlConfig, environment: Mapping[str, str]) -> HostRegistry:
    configured_urls = {host.url for host in control.hosts}
    extra = [host for host in ci_hosts(environment) if host.url not in configured_urls]
    return HostRegistry(merge_hosts([*control.hosts, *extra]), environment)


def create_context(
    env: Mapping[str, str] | None = None,
    http: HttpClient | None = None,
    control: ControlConfig | None = None,
) -> Context:
    environment = dict(os.environ if env is None else env)
    client = http or HttpClient()
    control_config = control or ControlConfig()
    download_limit = environment.get("TOOLBOT_MAX_DOWNLOAD_MB", "")
    return Context(
        http=client,
        providers=create_source_providers(client, build_host_registry(control_config, environment)),
        env=environment,
        control=control_config,
        max_download_bytes=int(download_limit) * 1024 * 1024
        if download_limit.isdigit() and int(download_limit) > 0
        else DEFAULT_MAX_DOWNLOAD_BYTES,
    )
