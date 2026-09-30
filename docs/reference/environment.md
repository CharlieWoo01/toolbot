# Environment variables

## Target (GitLab)

| Variable | Default | Description |
| --- | --- | --- |
| `TOOLBOT_GITLAB_TOKEN` | *(required for `check`)* | Project/group access token or bot PAT, scope `api`. |
| `TOOLBOT_GITLAB_URL` | `CI_SERVER_URL` | GitLab base URL (`https://…`). |
| `TOOLBOT_GITLAB_PROJECT` | `CI_PROJECT_PATH` | Project receiving updates. |
| `TOOLBOT_BASE_BRANCH` | project default branch | Target branch for merge requests. |
| `TOOLBOT_MR_LABELS` | `toolbot,tool-update` | Comma-separated labels; empty for none. |
| `TOOLBOT_GIT_AUTHOR_NAME` | `Toolbot` | Commit author/committer name. |
| `TOOLBOT_GIT_AUTHOR_EMAIL` | `toolbot@noreply.invalid` | Commit author/committer email. |
| `TOOLBOT_TARGET` | `gitlab` | Target provider (only `gitlab` today). |

## Sources

| Variable | Description |
| --- | --- |
| `TOOLBOT_GITHUB_TOKEN` | Optional read-only token for GitHub sources. |
| `TOOLBOT_GITLAB_SOURCE_TOKEN` | Optional `read_api` token for GitLab sources. If unset, `TOOLBOT_GITLAB_TOKEN` is used for sources on the same instance as the target. |

## Behaviour

| Variable | Default | Description |
| --- | --- | --- |
| `TOOLBOT_MAX_DOWNLOAD_MB` | `1024` | Per-asset download size limit. |

## CI template

| Variable | Description |
| --- | --- |
| `TOOLBOT_IMAGE` | Image the template's jobs run in (pinned). |
| `TOOLBOT_RUN` | Set to `true` on the schedule that should run `toolbot check`. |
| `TOOLBOT_CHECK_ARGS` | Extra arguments for `toolbot check`, e.g. `--dry-run` or tool names. |

## Network

| Variable | Description |
| --- | --- |
| `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` | Proxy for Toolbot and git. |
| `SSL_CERT_FILE`, `SSL_CERT_DIR` | CA bundle for Toolbot's HTTP client (TLS inspection). |
| `GIT_SSL_CAINFO` | CA bundle for git. |
