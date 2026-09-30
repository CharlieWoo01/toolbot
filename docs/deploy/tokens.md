# Tokens & permissions

Tokens are only ever environment variables (CI/CD variables, Kubernetes secrets…), never tool definitions. Store them **masked** and **protected**.

## What's needed

| Variable | Required | Used for | Permissions |
| --- | --- | --- | --- |
| `TOOLBOT_GITLAB_TOKEN` | **yes** for `check` | Target: MR API, pushing `toolbot/*` branches. Also reused to read upstream projects **on the same GitLab instance**. | Project or group access token (or a bot user's PAT): scope **`api`**, role **Developer** (Maintainer if your branch/MR rules require it) |
| `TOOLBOT_GITHUB_TOKEN` | no | GitHub sources: raises the API limit from 60 to 5,000 requests/hour; needed for private repos | Fine-grained token: *Public repositories (read-only)*; add *Contents: read* for private upstream repos |
| `TOOLBOT_GITLAB_SOURCE_TOKEN` | no | GitLab sources on **another** instance (e.g. gitlab.com), or a narrower read token | `read_api` |

One GitLab token is enough for most setups. `TOOLBOT_GITLAB_SOURCE_TOKEN` exists only because a token for your company GitLab can't read gitlab.com, and vice versa.

## Where tokens go

```text
TOOLBOT_GITLAB_TOKEN ──► GitLab API (PRIVATE-TOKEN header)            only to TOOLBOT_GITLAB_URL
                    └──► git push over HTTPS (Authorization header,   only to TOOLBOT_GITLAB_URL
                         via git's environment config — never in URLs, argv or logs)
TOOLBOT_GITHUB_TOKEN ──► GitHub API                                   only to the GitHub API origin;
                                                                      dropped on the CDN redirect
TOOLBOT_GITLAB_SOURCE_TOKEN ──► source GitLab API and release links   only to that source host
```

## Choosing the GitLab identity

| Option | Pros | Cons |
| --- | --- | --- |
| **Project access token** | Narrowest scope; a named bot user | One per project; expiry to rotate |
| **Group access token** | One token for many projects | Broader access |
| **Bot user + PAT** | Works on every tier and setup | Uses a licence seat; someone owns the account |

Commits are authored as `TOOLBOT_GIT_AUTHOR_NAME <TOOLBOT_GIT_AUTHOR_EMAIL>` (default `Toolbot <toolbot@noreply.invalid>`). If your push rules require a verified or matching author email, set the email to the bot user's. For access tokens, it's shown on the project's *Members* page.

## Rotation

1. Create the new token with the same role and scope.
2. Update the CI/CD variable.
3. Run the schedule manually with `TOOLBOT_CHECK_ARGS=--dry-run`, then without.
4. Revoke the old token.
