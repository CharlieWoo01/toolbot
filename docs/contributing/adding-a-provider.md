# Adding a provider

## A source provider

Implement `toolbot.providers.source.base.SourceProvider`:

```python
class SourceProvider(ABC):
    id: str                 # value of source.provider
    default_host: str       # used when source.host is omitted
    token_env: str          # optional read-only token variable
    supports_digest: bool   # enables checksum.type: provider

    def check_repository(self, repository: str) -> str | None: ...
    def list_releases(self, source: SourceConfig) -> Iterator[Release]: ...   # newest first, lazily paginated
    def get_release(self, source: SourceConfig, tag: str) -> Release: ...     # or RELEASE_NOT_FOUND
    def download_request(self, source: SourceConfig, asset: ReleaseAsset) -> HttpRequest: ...
```

Providers don't choose versions, download, verify or write files; that's shared. They report draft/prerelease flags truthfully and return only complete, downloadable assets.

Steps:

1. Add `src/toolbot/providers/source/<id>.py`.
2. Register it in `create_source_providers()`.
3. Add the id to `source.provider.enum` in `schema/tool.schema.json` (`test_schema.py` enforces this).
4. Send credentials only when `same_origin(target, source.host)`.
5. Tests with `httpx.MockTransport`: listing, flags, assets, 404/401, token scoping.
6. Document it in `docs/guide/providers.md`.

Candidates: Bitbucket, Gitea/Forgejo, direct HTTP (URL template plus mandatory checksum), vendor APIs, internal artifact repositories.

## A target provider

Implement `toolbot.providers.target.base.TargetProvider`: `repository_url`, `git_auth`, `default_branch`, `find_by_branch`, `list_open`, `create` (idempotent: return the open request if one exists for the branch) and `close`. Select it in `create_target()` via `TOOLBOT_TARGET`.

A `GitHubTargetProvider` would use the REST API for pull requests (`GET /repos/{r}/pulls?head=owner:branch&state=all`, `POST /pulls`, `PATCH /pulls/{n}` with `state=closed`, issue comments) and push over HTTPS with the token supplied through `GitAuth`. The updater, git service and everything else stay as they are.
