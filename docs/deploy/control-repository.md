# Control repository (GitOps)

Instead of adding a Toolbot schedule to every repository, keep **one control repository** that says which repositories Toolbot maintains, with which policy and on which schedule. Changes to that policy are reviewed like any other code.

```text
toolbot-control/                     (a normal GitLab/GitHub repository)
├── toolbot.yaml                     hosts, rules, target sets
├── .gitlab-ci.yml                   or .github/workflows/toolbot.yml: one schedule per set
```

```text
schedule "nightly" ──► toolbot check --targets nightly
                          │
                          ├─ expand: group platform (incl. subgroups) → repos with tools/
                          ├─ team/ci-tools
                          │
                          └─ for each repository: read its tools/*/tool.yaml from the base branch,
                             apply the rules, open/supersede MRs there
```

Each **target repository** keeps its own `tools/<name>/tool.yaml` (what to track, and where files go) and receives the merge/pull requests. It only needs the `toolbot-verify` job on merge requests, not a schedule of its own.

A complete example is in [`examples/control-repo/`](https://github.com/your-org/toolbot/tree/main/examples/control-repo). Keep it in this repository to try things out, or copy it into a new repository for real use.

## `toolbot.yaml`

```yaml
# yaml-language-server: $schema=https://…/schema/toolbot.schema.json
hosts:
  internal-gitlab:
    type: gitlab
    url: https://gitlab.example.com
    token-env: TOOLBOT_GITLAB_TOKEN        # the NAME of the variable, never the token
  internal-github:
    type: github
    url: https://github.example.com        # GitHub Enterprise Server
    token-env: TOOLBOT_GHE_TOKEN

defaults:
  labels: [toolbot, tool-update]
  git-author: { name: Toolbot, email: toolbot@example.com }

rules:
  - repositories: ["platform/legacy-*"]
    enabled: false
  - tools: ["terraform", "terraform-pin"]
    allow: ">=1.16.0, <2.0.0"
  - tools: ["pact-*"]
    ignore: ["0.9.0"]
  - tools: ["pact-cli"]
    repositories: ["team/contract-testing"]
    prereleases: true

targets:
  nightly:
    - host: internal-gitlab
      group: platform                      # GitLab group (with subgroups)
      exclude: ["platform/sandbox-*"]
    - host: internal-gitlab
      repository: team/ci-tools
  weekly:
    - host: internal-github
      group: acme                          # GitHub organisation (or user)
      include: ["acme/*-tools"]
```

### `hosts`

The GitHub/GitLab instances Toolbot talks to, as **sources** (where tools are released) and as **targets** (where your repositories are). See [Hosts](../guide/hosts.md). `github` (github.com) and `gitlab` (gitlab.com) always exist; declaring a host with the same URL overrides them.

### `rules`

Applied in order on top of each repository's `tool.yaml`. A rule matches by tool-name globs (`tools`) and repository-path globs (`repositories`); omitted means "all".

| Field | Effect |
| --- | --- |
| `enabled: false` | Skip matching tools (reported as `disabled`) |
| `prereleases: true/false` | Override the tool's setting; later rules win |
| `ignore` | Add versions never proposed (exact or constraints) |
| `allow` | Add constraints every proposed version must satisfy: stay on a major (`"<2.0.0"`), a minor line (`">=1.36.0, <1.37.0"`) or pin (`"==1.4.2"`) |

`ignore` and `allow` **accumulate**: a repository's own `tool.yaml` can tighten the central policy but never loosen it.

### `targets`

Named **target sets**. Each entry is one `repository`, or a whole `group` (GitLab group including subgroups, or GitHub organisation/user) with optional `include`/`exclude` path globs and `base-branch`. Group members are kept only if they are not archived, not empty, and have a `tools/` directory on their base branch.

Check what a set resolves to:

```sh
toolbot --config toolbot.yaml targets --targets nightly
toolbot --config toolbot.yaml targets --targets all --json
```

## Schedules

One schedule per target set, so different groups can run at different times (nightly for platform repositories, weekly for the rest, …).

=== "GitLab"

    ```yaml
    # .gitlab-ci.yml of the control repository
    include:
      - project: platform/toolbot
        ref: v0.1.0
        file: /templates/toolbot.gitlab-ci.yml
    variables:
      TOOLBOT_IMAGE: gitlab.example.com:5050/platform/toolbot:0.1.0
    ```

    *Build → Pipeline schedules*, one per set:

    | Description | Interval | Variables |
    | --- | --- | --- |
    | Toolbot nightly | `0 2 * * *` | `TOOLBOT_RUN=true`, `TOOLBOT_CHECK_ARGS=--targets nightly` |
    | Toolbot weekly | `0 6 * * 1` | `TOOLBOT_RUN=true`, `TOOLBOT_CHECK_ARGS=--targets weekly` |

    Add the `token-env` variables from `toolbot.yaml` as masked, protected CI/CD variables.

=== "GitHub Actions"

    Each `cron` entry maps to a set; see [`examples/control-repo/.github/workflows/toolbot.yml`](https://github.com/your-org/toolbot/tree/main/examples/control-repo/.github/workflows/toolbot.yml). Add the `token-env` variables as repository secrets.

The `resource_group` / `concurrency` setting stops two runs overlapping. Runs are idempotent, so a repository in two sets never gets duplicate requests.

## Tokens

Each target host's token must be able to push branches and open requests in every target repository, and list the groups you scan:

| Host type | Token |
| --- | --- |
| GitLab | Group access token (or bot user PAT) with `api`, role Developer, on each target group |
| GitHub | GitHub App installation token (Contents, Pull requests, Issues: read & write; Metadata: read), or a fine-grained PAT |

Tokens for source-only hosts only need read access. See [Tokens & permissions](tokens.md).

## Rolling it out

1. Create the control repository with `toolbot.yaml` and the CI file.
2. `toolbot targets --targets all` in a merge request pipeline shows exactly which repositories will be touched.
3. Run each set once with `--dry-run` (manual pipeline: `TOOLBOT_CHECK_ARGS=--targets nightly --dry-run`).
4. Add the schedules.
5. In each target repository, include the CI template for `toolbot-verify` on merge requests. Remove any per-repository Toolbot schedule.

## One repository, no control repository

Without `targets`, `toolbot check` maintains the repository it runs in (detected from GitLab CI or GitHub Actions variables). A `toolbot.yaml` in that repository can still declare `hosts` and `rules`.
