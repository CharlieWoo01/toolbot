# Providers

Toolbot separates **where tools come from** (source providers) from **where updates are proposed** (target providers). Any source works with any target: a tool released on github.com can be updated in a repository on an internal GitLab, and vice versa. Which instance is used is decided by [hosts](hosts.md).

## Source providers

A source provider lists a project's releases or tags, fetches one by tag, downloads release assets, and reads files committed in the repository. Choosing the version and verifying, extracting and installing files are shared, so the rest of Toolbot never sees platform APIs.

| Provider | `source.provider` | Default host | Release digests |
| --- | --- | --- | --- |
| GitHub / GitHub Enterprise | `github` | `https://github.com` | ✅ |
| GitLab / self-managed GitLab | `gitlab` | `https://gitlab.com` | ❌ |

Files that live elsewhere (Terraform, Helm, kubectl…) are downloaded with `url:` assets; versions still come from the project's GitHub/GitLab releases or tags. See [What can be updated](what-can-be-updated.md).

### GitHub

```yaml
source:
  provider: github
  repository: owner/repo
  host: ghe                       # a host name or https:// URL; GitHub Enterprise only
  versions: tags                  # optional: plain tags instead of releases
```

- Releases: `GET /repos/{owner}/{repo}/releases` (paginated), or `/releases/tags/{tag}` for one tag. On Enterprise the API is `<host>/api/v3`.
- Tags (`versions: tags`): `GET /repos/{owner}/{repo}/git/matching-refs/tags/<prefix>`, filtered server-side by the tag template's prefix, which keeps monorepos cheap.
- Drafts are skipped. The prerelease flag is respected. Only fully uploaded assets count.
- Release asset downloads use the API asset URL (`Accept: application/octet-stream`), which redirects to a signed CDN URL. The token is dropped on that redirect.
- `path:` assets use the contents API at the tag (`Accept: application/vnd.github.raw`, up to 100 MB). Git LFS pointers are detected and refused.
- Private repositories need a token that can read contents. Without one GitHub answers 404, and Toolbot's message says a token may be needed.

**Why the API rather than `github.com/<repo>/releases/download/<tag>/<asset>`?** The predictable URL is public-only and gives no asset list (so no "asset renamed" diagnostics or ambiguity checks), no draft/prerelease flags and no digest.

### GitLab

```yaml
source:
  provider: gitlab
  repository: group/subgroup/project
  host: internal-gitlab           # a host name or https:// URL (path prefixes like /gitlab work)
```

- Releases: `GET /api/v4/projects/<url-encoded path>/releases`. Nested groups are encoded as one parameter.
- Tags (`versions: tags`): `GET …/repository/tags?search=^<prefix>`.
- Assets are release **links**. Toolbot downloads each link's target, usually the generic package registry.
- Upcoming (scheduled) releases are skipped.
- `path:` assets use `…/repository/files/<path>/raw?ref=<tag>&lfs=true`, so Git LFS content is served.
- No digests: use `checksum.type: file`.

## Target providers

A target provider owns everything platform-specific about a repository receiving updates: its git URL and push credentials, and change-request operations (find by branch, list open Toolbot requests, create with labels, close with a comment). The updater only talks to this interface, so behaviour is identical on both platforms.

| Target | Requests | Selected by |
| --- | --- | --- |
| GitLab / self-managed | Merge requests (`!12`), `remove_source_branch` set | GitLab CI, `TOOLBOT_TARGET=gitlab`, or a `gitlab` host in `toolbot.yaml` targets |
| GitHub / Enterprise | Pull requests (`#12`), labels via the issues API | GitHub Actions, `TOOLBOT_TARGET=github`, or a `github` host in `toolbot.yaml` targets |

Pushes use HTTPS with the token supplied through git's environment-based config, never in URLs, arguments or files.

### Which repository?

- **One repository**: the one the CI job runs in (`CI_PROJECT_PATH` / `GITHUB_REPOSITORY`), or `TOOLBOT_GITLAB_PROJECT` / `TOOLBOT_GITHUB_REPOSITORY`. See [Environment variables](../reference/environment.md).
- **Many repositories**: target sets in a [control repository](../deploy/control-repository.md), including whole GitLab groups and GitHub organisations.

Adding another platform (Bitbucket, Gitea…) means implementing the two interfaces: see [Adding a provider](../contributing/adding-a-provider.md).
