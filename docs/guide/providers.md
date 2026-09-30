# Providers

## Source providers

A source provider knows how to list a project's releases, fetch one release by tag, and download an asset. Choosing the version and verifying and installing files are shared, so the rest of Toolbot never sees platform APIs.

| Provider | `source.provider` | Default host | Token (optional) | Digests |
| --- | --- | --- | --- | --- |
| GitHub / GitHub Enterprise | `github` | `https://github.com` | `TOOLBOT_GITHUB_TOKEN` | ✅ |
| GitLab / self-managed GitLab | `gitlab` | `https://gitlab.com` | `TOOLBOT_GITLAB_SOURCE_TOKEN`, see below | ❌ |

### GitHub

```yaml
source:
  provider: github
  repository: owner/repo
  host: https://github.example.com   # GitHub Enterprise only
```

- Releases: `GET /repos/{owner}/{repo}/releases` (paginated), or `/releases/tags/{tag}` for one tag. On Enterprise the API is `<host>/api/v3`.
- Drafts are skipped. The prerelease flag is respected. Only fully uploaded assets count.
- Downloads use the API asset URL (`Accept: application/octet-stream`), which redirects to a signed CDN URL. The token is dropped on that redirect.
- Private repositories need a token that can read contents. Without one GitHub answers 404, and Toolbot's message says a token may be needed.

**Why the API rather than `github.com/<repo>/releases/download/<tag>/<asset>`?** The predictable URL is public-only and gives no asset list (so no "asset renamed" diagnostics or ambiguity checks), no draft/prerelease flags and no digest.

### GitLab

```yaml
source:
  provider: gitlab
  repository: group/subgroup/project
  host: https://gitlab.example.com   # self-managed only; path prefixes like /gitlab work
```

- Releases: `GET /api/v4/projects/<url-encoded path>/releases`. Nested groups are encoded as one parameter.
- Assets are release **links**. Toolbot downloads each link's target, usually the generic package registry.
- Upcoming (scheduled) releases are skipped.
- No digests: use `checksum.type: file`.
- **Token:** `TOOLBOT_GITLAB_SOURCE_TOKEN` if set; otherwise, for upstream projects on the **same instance as the target**, the target token `TOOLBOT_GITLAB_TOKEN` is reused, so one token covers both. Tokens only go to the source host, never to external hosts a link may point to.

## Target providers

A target provider owns everything platform-specific about the repository receiving updates: its Git URL and push credentials, and merge-request operations (find by branch, list open Toolbot MRs, create with labels, close with a comment). The updater only talks to this interface.

### GitLab (MVP)

Configured by environment. In GitLab CI the defaults come from the job:

| Variable | Default | Purpose |
| --- | --- | --- |
| `TOOLBOT_GITLAB_TOKEN` | *(required)* | API calls and git push |
| `TOOLBOT_GITLAB_URL` | `CI_SERVER_URL` | Instance URL |
| `TOOLBOT_GITLAB_PROJECT` | `CI_PROJECT_PATH` | Project receiving updates |
| `TOOLBOT_BASE_BRANCH` | project default branch | Target branch of MRs |
| `TOOLBOT_MR_LABELS` | `toolbot,tool-update` | Labels (created if missing; skipped with a warning if not permitted) |

- Merge requests: REST API v4. `remove_source_branch` is set, so merged branches are cleaned up.
- Pushes: HTTPS with the token supplied through git's environment-based config, never in URLs or arguments.

### GitHub (future)

Implementing a `GitHubTargetProvider` (pull requests via the REST API, push over HTTPS) and selecting it with `TOOLBOT_TARGET=github` is all that's needed. Nothing else in Toolbot depends on GitLab.
