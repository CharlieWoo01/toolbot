# CI template

`templates/toolbot.gitlab-ci.yml` in the Toolbot project.

```yaml
include:
  - project: platform/toolbot
    ref: v0.1.0
    file: /templates/toolbot.gitlab-ci.yml

variables:
  TOOLBOT_IMAGE: gitlab.example.com:5050/platform/toolbot:0.1.0
```

## Jobs

| Job | Runs on | Script |
| --- | --- | --- |
| `toolbot-verify` | MRs and the default branch, if `tools/*/tool.yaml` or `toolbot.yaml` exists; never on schedules | `toolbot validate`, `toolbot verify` |
| `toolbot-check` | Schedules and manual pipelines with `TOOLBOT_RUN=true` | `toolbot check $TOOLBOT_CHECK_ARGS` with `resource_group: toolbot` and a 1 h timeout |

Both use stage `test` and are skipped when `TOOLBOT_IMAGE` is empty.

## Variables

| Variable | Description |
| --- | --- |
| `TOOLBOT_IMAGE` | **Required.** Pinned image. |
| `TOOLBOT_RUN` | `true` on the schedule that should run Toolbot. |
| `TOOLBOT_CHECK_ARGS` | e.g. `--dry-run`, or tool names to limit the run. |

Plus the tokens in [Tokens & permissions](../deploy/tokens.md) and anything in [Environment variables](environment.md).

## Customising

```yaml
toolbot-check:
  stage: maintenance
  tags: [docker]
  timeout: 2h

toolbot-verify:
  stage: lint
```
