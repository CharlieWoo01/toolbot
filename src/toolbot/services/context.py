"""Shared dependencies, injectable for tests."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from toolbot.http import HttpClient
from toolbot.providers.source import SourceProviders, create_source_providers

DEFAULT_MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024  # 1 GiB


def _stderr(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


@dataclass
class Context:
    http: HttpClient
    providers: SourceProviders
    env: Mapping[str, str]
    log: Callable[[str], None] = _stderr
    temp_root: Path | None = None
    """Parent directory for per-run temp dirs (default: the OS temp dir)."""
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES
    warnings: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        self.log(f"warning: {message}")


def create_context(env: Mapping[str, str] | None = None, http: HttpClient | None = None) -> Context:
    environment = dict(os.environ if env is None else env)
    client = http or HttpClient()
    limit = environment.get("TOOLBOT_MAX_DOWNLOAD_MB", "")
    return Context(
        http=client,
        providers=create_source_providers(client, environment),
        env=environment,
        max_download_bytes=int(limit) * 1024 * 1024
        if limit.isdigit() and int(limit) > 0
        else DEFAULT_MAX_DOWNLOAD_BYTES,
    )
