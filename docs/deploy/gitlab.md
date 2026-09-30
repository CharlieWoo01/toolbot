# GitLab CI

This sets up scheduled Toolbot runs for a GitLab project, e.g. a repository at work that stores CI tooling. It works on GitLab.com and self-managed GitLab.

| Placeholder | Example |
| --- | --- |
| `gitlab.example.com` | your GitLab host |
| `platform/toolbot` | where the Toolbot project lives |
| `team/ci-tools` | the project receiving updates |

## 1. Publish Toolbot (once per organisation)

1. Push this repository to `platform/toolbot` and make sure its **container registry** is enabled.
2. Tag a release matching `pyproject.toml`: `git tag v0.1.0 && git push origin v0.1.0`.
3. The `image` job publishes `gitlab.example.com:5050/platform/toolbot:0.1.0` (copy the exact path from *Deploy → Container registry*).
4. Let other projects pull the image and include the template: make `platform/toolbot` **internal**, or add the consuming projects to its **CI/CD job token allowlist**. See [Container image](image.md#letting-other-projects-pull-the-image).

## 2. Create the bot token

On `team/ci-tools` (or its group), create a **project or group access token**:

- Role: **Developer**, or Maintainer if only Maintainers may push to branches or create MRs in your setup.
- Scope: **`api`**. This covers the MR API and pushing branches over HTTPS.
- An expiry you'll rotate.

This single token is Toolbot's identity on the target. It's also reused automatically to read upstream projects **on the same GitLab instance**. Details and alternatives: [Tokens & permissions](tokens.md).

## 3. Add CI/CD variables

*`team/ci-tools` → Settings → CI/CD → Variables*:

| Key | Value | Flags |
| --- | --- | --- |
| `TOOLBOT_GITLAB_TOKEN` | the token from step 2 | Masked, Protected |
| `TOOLBOT_GITHUB_TOKEN` | read-only github.com token (optional, avoids rate limits) | Masked, Protected |
| `TOOLBOT_GIT_AUTHOR_EMAIL` | the bot user's email (needed if push rules check authors) | |

## 4. Include the template

`.gitlab-ci.yml`:

```yaml
include:
  - project: platform/toolbot
    ref: v0.1.0
    file: /templates/toolbot.gitlab-ci.yml

variables:
  TOOLBOT_IMAGE: gitlab.example.com:5050/platform/toolbot:0.1.0
```

This adds two jobs (stage `test`; override `stage:` if needed):

| Job | Runs | Does |
| --- | --- | --- |
| `toolbot-verify` | MRs and the default branch, if `tools/*/tool.yaml` exists | `toolbot validate && toolbot verify` |
| `toolbot-check` | schedules/manual runs with `TOOLBOT_RUN=true` | `toolbot check $TOOLBOT_CHECK_ARGS` (`resource_group: toolbot`) |

Then [add your first tool](../guide/adding-a-tool.md) and commit it.

## 5. Create the schedule

*Build → Pipeline schedules → New schedule*:

| Field | Value |
| --- | --- |
| Description | `Toolbot` |
| Interval | `0 6 * * 1-5` (weekdays 06:00), or anything you like |
| Target branch | the default branch (protected, so protected variables are exposed) |
| Variables | `TOOLBOT_RUN` = `true` |

Only schedules with `TOOLBOT_RUN=true` run Toolbot, so other schedules in the project are unaffected. Normal pushes never trigger update scans.

## 6. First run: dry run first

1. Run the schedule with the extra variable `TOOLBOT_CHECK_ARGS` = `--dry-run` (*Run pipeline* on the schedule page, or *Build → Pipelines → Run pipeline* with both variables). The job log shows, per tool, what would happen, including existing MRs.
2. Run it again without `TOOLBOT_CHECK_ARGS`. Expect lines like:

    ```text
    toolbot: pact-broker-cli 0.8.6 -> 0.8.8 (github:pact-foundation/pact-broker-cli@v0.8.8)
      windows-amd64: pact-broker-cli-x86_64-pc-windows-msvc.zip (3188345 bytes, verified: sha256:…)

    pact-broker-cli
      Current: 0.8.6
      Latest:  0.8.8
      Update proposed.
      merge request !12
      Branch: toolbot/pact-broker-cli/0.8.8

    1 tool(s): 1 created
    ```

3. Run it a third time: the tool now reports `Update already proposed.` Idempotency confirmed.

## 7. Recommended project settings

- *Settings → Merge requests*: **Pipelines must succeed**.
- Protect `tools/` with CODEOWNERS if needed.

## Upstream projects on GitLab

| Upstream | Configuration |
| --- | --- |
| Public, any instance | Nothing |
| Private, same instance as the target | Nothing: `TOOLBOT_GITLAB_TOKEN` is reused. The bot needs at least Reporter on the upstream project. |
| Private, another instance (e.g. gitlab.com) | `TOOLBOT_GITLAB_SOURCE_TOKEN` with `read_api` on that instance |
