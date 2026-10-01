# Environment variables

Tokens are only ever read from environment variables (masked CI/CD variables or secrets), never from files. Variable *names* for extra hosts are declared in [`toolbot.yaml`](../guide/hosts.md) with `token-env`.

## Target: the repository receiving updates

Used when `toolbot.yaml` defines no `targets`. The target platform is detected: GitHub when `GITHUB_ACTIONS=true`, GitLab otherwise.

| Variable | Default | Description |
| --- | --- | --- |
| `TOOLBOT_TARGET` | detected | `gitlab` or `github`. |
| `TOOLBOT_GITLAB_TOKEN` | *(required for GitLab `check`)* | Project/group access token or bot PAT, scope `api`, role Developer. Also read for sources on the same instance. |
| `TOOLBOT_GITLAB_URL` | `CI_SERVER_URL` | GitLab base URL (`https://…`). |
| `TOOLBOT_GITLAB_PROJECT` | `CI_PROJECT_PATH` | Project receiving merge requests. |
| `TOOLBOT_GITHUB_TOKEN` | *(required for GitHub `check`)* | GitHub App installation token or fine-grained PAT: Contents, Pull requests, Issues read & write. Also used for GitHub sources. |
| `TOOLBOT_GITHUB_URL` | `GITHUB_SERVER_URL`, else `https://github.com` | GitHub (Enterprise) base URL. |
| `TOOLBOT_GITHUB_REPOSITORY` | `GITHUB_REPOSITORY` | Repository receiving pull requests (`owner/repo`). |
| `TOOLBOT_BASE_BRANCH` | repository default branch | Target branch for requests. |
| `TOOLBOT_MR_LABELS` | `toolbot,tool-update` (or `defaults.labels`) | Comma-separated labels; empty for none. |
| `TOOLBOT_GIT_AUTHOR_NAME` | `Toolbot` (or `defaults.git-author`) | Commit author/committer name. |
| `TOOLBOT_GIT_AUTHOR_EMAIL` | `toolbot@noreply.invalid` (or `defaults.git-author`) | Commit author/committer email. |

## Sources

| Variable | Description |
| --- | --- |
| `TOOLBOT_GITHUB_TOKEN` | Token for github.com (read-only is enough when it's only a source). Avoids rate limits; required for private repositories. |
| `TOOLBOT_GITLAB_SOURCE_TOKEN` | `read_api` token for **gitlab.com** sources. |
| *any `token-env` from `toolbot.yaml`* | Tokens for internal/other instances, e.g. `TOOLBOT_GHE_TOKEN`. |

The instance Toolbot runs on is registered automatically with `TOOLBOT_GITLAB_TOKEN` (or `TOOLBOT_GITHUB_TOKEN` on GitHub Enterprise), so upstream projects on the same instance need no extra token.

## Behaviour

| Variable | Default | Description |
| --- | --- | --- |
| `TOOLBOT_MAX_DOWNLOAD_MB` | `1024` | Size limit per download, and per file extracted from an archive. |

## CI template

| Variable | Description |
| --- | --- |
| `TOOLBOT_IMAGE` | Image the template's jobs run in (pinned). |
| `TOOLBOT_RUN` | Set to `true` on the schedule that should run `toolbot check`. |
| `TOOLBOT_CHECK_ARGS` | Extra arguments for `toolbot check`, e.g. `--dry-run`, `--targets nightly` or tool names. |

## Network

| Variable | Description |
| --- | --- |
| `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` | Proxy for Toolbot and git. |
| `SSL_CERT_FILE`, `SSL_CERT_DIR` | CA bundle for Toolbot's HTTP client (TLS inspection). |
| `GIT_SSL_CAINFO` | CA bundle for git. |
