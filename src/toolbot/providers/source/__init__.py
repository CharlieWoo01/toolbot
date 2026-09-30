"""Source providers and their registry.

Adding a provider means implementing ``SourceProvider`` and registering it in
``create_source_providers`` (plus the schema enum; a test enforces that).
"""

from __future__ import annotations

from collections.abc import Mapping

from toolbot.http import HttpClient
from toolbot.providers.source.base import Release, ReleaseAsset, SourceProvider


class SourceProviders:
    def __init__(self, providers: list[SourceProvider]) -> None:
        self._providers = {p.id: p for p in providers}

    def get(self, provider_id: str) -> SourceProvider | None:
        return self._providers.get(provider_id)

    def require(self, provider_id: str) -> SourceProvider:
        provider = self._providers.get(provider_id)
        if provider is None:  # unreachable for validated definitions
            raise KeyError(provider_id)
        return provider

    def ids(self) -> list[str]:
        return list(self._providers)


def create_source_providers(http: HttpClient, env: Mapping[str, str]) -> SourceProviders:
    from toolbot.providers.source.github import GitHubSourceProvider
    from toolbot.providers.source.gitlab import GitLabSourceProvider

    return SourceProviders([GitHubSourceProvider(http, env), GitLabSourceProvider(http, env)])


__all__ = ["Release", "ReleaseAsset", "SourceProvider", "SourceProviders", "create_source_providers"]
