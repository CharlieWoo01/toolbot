# Adding a provider

## A source provider

Implement `toolbot.providers.source.base.SourceProvider`:

```python
class SourceProvider(ABC):
    id: str                 # value of source.provider
    default_host: str       # used when source.host is omitted
    supports_digest: bool   # enables checksum.type: provider

    def check_repository(self, repository: str) -> str | None: ...
    def list_releases(self, source: SourceConfig) -> Iterator[Release]: ...          # newest first, lazily paginated
    def get_release(self, source: SourceConfig, tag: str) -> Release: ...            # or RELEASE_NOT_FOUND
    def list_tags(self, source: SourceConfig, prefix: str) -> Iterator[Release]: ... # versions: tags (no assets)
    def get_tag(self, source: SourceConfig, tag: str) -> Release: ...
    def download_request(self, source: SourceConfig, asset: ReleaseAsset) -> HttpRequest: ...
    def file_request(self, source: SourceConfig, path: str, ref: str) -> HttpRequest: ...  # path: assets
    def auth_hint(self, source: SourceConfig) -> str: ...
```

Providers don't choose versions, download, verify, extract or write files; that's shared. They report draft/prerelease flags truthfully and return only complete, downloadable assets. Credentials come from the `HostRegistry` (`hosts.auth_headers(url)`), which only returns a token for URLs owned by that host.

Steps:

1. Add `src/toolbot/providers/source/<id>.py`.
2. Register it in `create_source_providers()`, and add the platform type to `HOST_TYPES` in `toolbot.hosts` if it's new.
3. Add the id to `source.provider.enum` in `schema/tool.schema.json` (`test_schema.py` enforces this).
4. Tests with `httpx.MockTransport`: listing, flags, assets, tags, files, 404/401, token scoping.
5. Document it in `docs/guide/providers.md`.

Candidates: Bitbucket, Gitea/Forgejo, vendor APIs, internal artifact repositories. Plain download sites need no provider: use `url:` assets.

## A target provider

Implement `toolbot.providers.target.base.TargetProvider`: `repository_url`, `git_auth`, `default_branch`, `find_by_branch`, `list_open`, `create` (idempotent: return the open request if one exists for the branch) and `close`. For group scanning, also implement the platform API's `list_group_repositories` and `has_tools`. Register both in `toolbot.providers.target` (`create_target`, `create_platform_api`).

The GitLab and GitHub implementations are the reference: they share the updater, git service and everything else unchanged.
