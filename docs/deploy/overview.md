# Deployment overview

Toolbot is a Python CLI with one system dependency, `git`. It ships as a container image, and the image is the recommended way to run it anywhere.

```text
schedule ──► toolbot check ──► GitHub/GitLab release APIs (sources)
                  │
                  └──► GitLab: git push toolbot/<tool>/<version> + merge request (target)
```

## Pick a setup

| Setup | Use when | Guide |
| --- | --- | --- |
| **GitLab CI, per project** | The usual setup: a project keeps its tools and runs Toolbot on a schedule. | [GitLab CI](gitlab.md) |
| **Your machine** | Trying it out, adding tools, debugging, one-off runs. | [Running locally](local.md) |
| **GitHub Actions** | Hosting and building the Toolbot project on GitHub (CI, GHCR, Pages). | [GitHub Actions](github-actions.md) |
| **Anywhere else** | Kubernetes CronJob, Jenkins, a VM with cron. | [Docker, Kubernetes & other CI](anywhere.md) |

## Setup order

1. Publish the [container image](image.md) (once per organisation). The Toolbot project's pipeline does it on tag.
2. Create the bot token ([Tokens & permissions](tokens.md)).
3. Follow the guide for your setup.
4. Behind a proxy or TLS inspection? Read [Proxies & internal networks](networking.md).

Everything environment-specific is an environment variable ([reference](../reference/environment.md)).
