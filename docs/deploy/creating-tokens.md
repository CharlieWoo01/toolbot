# Creating tokens

Toolbot needs a token to push `toolbot/*` branches and open pull/merge requests in the repository it maintains. This page walks through creating one on GitHub and GitLab and giving it to the pipeline. For what each token is used for and where it's sent, see [Tokens & permissions](tokens.md).

!!! tip "Use a bot account for anything beyond a trial"
    Pull/merge requests and commits show up as whoever owns the token. A personal token is fine to try Toolbot out. For team use, create the token on a dedicated bot user, or use a GitHub App / GitLab project or group access token, so updates don't look like they came from you and don't stop when you leave.

**Never** put a token in `tool.yaml`, `toolbot.yaml`, a workflow file or a commit. Toolbot only reads tokens from environment variables, i.e. CI/CD secrets.

## Which token do I need?

| Toolbot runs in… | and updates… | Create | Store it as |
| --- | --- | --- | --- |
| GitHub Actions | the same GitHub repository | [Fine-grained PAT](#github-fine-grained-personal-access-token) or [GitHub App](#github-app) | Actions secret `TOOLBOT_GITHUB_TOKEN` |
| GitLab CI | the same GitLab project | [Project access token](#gitlab-project-access-token) | CI/CD variable `TOOLBOT_GITLAB_TOKEN` |
| A control repository | many repositories / a group | [Group access token](#gitlab-group-access-token) or a GitHub token covering every repository | The `token-env` name in `toolbot.yaml` |

Tools *released* on public GitHub/GitLab need no token. An optional read-only one avoids GitHub's rate limit of 60 requests/hour: see [Read-only tokens for sources](#read-only-tokens-for-sources).

---

## GitHub: fine-grained personal access token

Quickest way to get going.

1. Sign in as the account that should author the pull requests (ideally a bot user).
2. Go to **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**, or open <https://github.com/settings/personal-access-tokens/new>.
3. Fill in:

    | Field | Value |
    | --- | --- |
    | Token name | `toolbot – <repository>` |
    | Expiration | 90 days (or your organisation's maximum); note the date |
    | Resource owner | The user or organisation that owns the repository |
    | Repository access | **Only select repositories** → the repository (or repositories) Toolbot maintains |

4. Under **Permissions → Repository permissions**, set:

    | Permission | Access | Why |
    | --- | --- | --- |
    | Contents | **Read and write** | Push `toolbot/*` branches; read tool definitions |
    | Pull requests | **Read and write** | Find, open and close Toolbot's pull requests |
    | Issues | **Read and write** | Add labels to pull requests (GitHub labels are an issues feature) |
    | Metadata | Read-only | Selected automatically |

    Nothing else. Toolbot never changes workflows, settings or secrets.

5. **Generate token** and copy it (it starts with `github_pat_`). It's shown only once.
6. If the repository belongs to an **organisation**, an owner may have to approve the token (*Organization settings → Personal access tokens → Pending requests*), and the organisation must allow fine-grained tokens. Until it's approved, Toolbot gets `403`/`404` errors.

Then [add it to the repository](#github-add-the-secret).

## GitHub: App

Recommended for organisations: no personal account involved, tokens are short-lived, and it can be installed on many repositories.

1. **Organization settings → Developer settings → GitHub Apps → New GitHub App** (or under your user settings).
2. Name it (e.g. `acme-toolbot`), set any homepage URL, and **untick Webhook → Active**.
3. **Repository permissions:** Contents *Read and write*, Pull requests *Read and write*, Issues *Read and write*; Metadata *Read-only*.
4. **Where can this app be installed?** *Only on this account.* Create it.
5. On the app's page note the **App ID**, then **Generate a private key** (a `.pem` file downloads).
6. **Install App** → choose the repositories Toolbot maintains.
7. In each repository (or once for the organisation): add variable `TOOLBOT_APP_ID` = the App ID, and secret `TOOLBOT_APP_PRIVATE_KEY` = the whole `.pem` content.

The [workflow template](github-actions.md) turns these into a token with `actions/create-github-app-token` and passes it as `TOOLBOT_GITHUB_TOKEN`. Pull requests appear as `acme-toolbot[bot]`.

## GitHub: add the secret

=== "Web UI"

    Repository → **Settings → Secrets and variables → Actions → New repository secret**:

    - Name: `TOOLBOT_GITHUB_TOKEN`
    - Secret: the token

=== "gh CLI"

    ```sh
    gh secret set TOOLBOT_GITHUB_TOKEN -R <owner>/<repository>   # paste the token when prompted
    ```

For many repositories, an **organisation secret** (*Organization settings → Secrets and variables → Actions*) with access limited to selected repositories avoids copying it around.

!!! warning "Why not the built-in `GITHUB_TOKEN`?"
    It can open pull requests, but GitHub doesn't run workflows for pull requests created with it, so the `verify` job would never check Toolbot's pull requests. Use a PAT or an App.

---

## GitLab: project access token

The narrowest option: a token for one project, acting as a generated bot user.

1. Project → **Settings → Access tokens → Add new token**.
2. Fill in:

    | Field | Value |
    | --- | --- |
    | Token name | `toolbot` (this becomes the bot user's name in merge requests) |
    | Expiration date | Your policy's maximum; note the date |
    | Select a role | **Developer**. Use **Maintainer** only if your protected-branch or merge request rules require it. |
    | Select scopes | **`api`** (includes pushing over HTTPS) |

3. **Create project access token** and copy it (it starts with `glpat-`). It's shown only once.

On GitLab.com, project and group access tokens need the Premium or Ultimate tier. On free GitLab.com, use a [bot user's personal access token](#gitlab-bot-user-personal-access-token) instead. Self-managed GitLab has them on every tier, unless an administrator disabled them.

Then [add it as a CI/CD variable](#gitlab-add-the-cicd-variable).

## GitLab: group access token

For a [control repository](control-repository.md) that maintains every project in a group (subgroups included):

1. Group → **Settings → Access tokens → Add new token**.
2. Role **Developer**, scope **`api`**, expiry as above.
3. Store it in the control repository under the variable name used as `token-env` in `toolbot.yaml` (e.g. `TOOLBOT_GITLAB_TOKEN`).

The same token lets Toolbot list the group's projects for `targets: [{group: …}]`.

## GitLab: bot user personal access token

Works on every tier and setup.

1. Create a user such as `toolbot-bot` (an administrator may need to), and add it to the project or group with role **Developer**.
2. Signed in as that user: **avatar → Edit profile → Access tokens → Add new token**, scope **`api`**, with an expiry date.
3. Set `TOOLBOT_GIT_AUTHOR_EMAIL` to the bot's email if push rules require commits from a verified or matching email.

## GitLab: add the CI/CD variable

Project (or group, for many projects) → **Settings → CI/CD → Variables → Add variable**:

| Setting | Value |
| --- | --- |
| Key | `TOOLBOT_GITLAB_TOKEN` |
| Value | the token |
| Visibility | **Masked** (or *Masked and hidden*) so it never appears in job logs |
| Flags | **Protect variable** ✅ (only protected branches, e.g. `main`, which schedules run on); *Expand variable reference* ❌ |

Then create the pipeline schedule with `TOOLBOT_RUN=true` ([GitLab CI](gitlab.md)).

---

## Read-only tokens for sources

Only needed for private upstream projects, or to avoid GitHub's anonymous rate limit:

| Upstream on | Token | Variable |
| --- | --- | --- |
| github.com | Fine-grained PAT, *Public repositories (read-only)* (add *Contents: read* for private repos) | `TOOLBOT_GITHUB_TOKEN` (the target token covers this when Toolbot runs on GitHub) |
| gitlab.com | PAT or access token with `read_api` | `TOOLBOT_GITLAB_SOURCE_TOKEN` |
| Internal GitLab / GitHub Enterprise | `read_api` / read-only token | The `token-env` of that [host](../guide/hosts.md) |
| The GitLab instance Toolbot runs on | Nothing extra: `TOOLBOT_GITLAB_TOKEN` is reused | |

## Check it works

Run the pipeline once in **dry-run** mode: GitHub *Run workflow* with *dry-run* ticked, or a GitLab pipeline with `TOOLBOT_CHECK_ARGS=--dry-run`. Or run locally:

```sh
export TOOLBOT_GITHUB_TOKEN=…        # or TOOLBOT_GITLAB_TOKEN / _URL / _PROJECT
toolbot check --dry-run
```

A working token gets as far as listing existing requests and reporting `Would create …`. Then run for real.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `no token for … set TOOLBOT_GITHUB_TOKEN` / `TOOLBOT_GITLAB_TOKEN` | The secret/variable isn't set, has another name, or (GitLab) is *protected* and the pipeline ran on an unprotected branch |
| `401` / `authentication failed` | Token expired, revoked, or mistyped |
| `403` / `404` on a repository that exists | Token doesn't cover that repository, lacks a permission above, or (GitHub org) is awaiting owner approval |
| Branch pushed but `creating the pull request failed` | GitHub: *Pull requests* not read & write. GitLab: role below Developer |
| Pull request opened but no labels | GitHub: *Issues* permission missing. GitLab: role may not create labels. Toolbot warns and carries on |
| `push` rejected | Protected-branch or push rules: allow the bot to push `toolbot/*`, or use Maintainer; check commit email rules |
| Verify job doesn't run on Toolbot's pull requests | The pull requests were created with GitHub's built-in `GITHUB_TOKEN` |
| Git LFS upload fails | LFS is disabled for the project or over quota; use `defaults.large-files: fetch` ([Large binaries](../guide/what-can-be-updated.md#large-binaries-terraform-is-120-mb)) |

## Rotation

Tokens expire; put a calendar reminder a week before.

1. Create the new token with the same settings.
2. Update the secret / CI/CD variable (same name).
3. Run the pipeline in dry-run mode, then normally.
4. Revoke the old token.
