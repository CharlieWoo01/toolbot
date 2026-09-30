# Toolbot

Toolbot automates updates for standalone development and CI tooling. It monitors upstream releases, downloads and verifies configured artifacts, and raises automated update requests when newer versions are available.

Think *Dependabot for binaries*: Pact Broker CLI, Terraform, Helm, kubectl, security scanners, vendor CLIs, standalone executables, ZIP/TAR archives and JARs. These are tools distributed through release platforms rather than package managers.

```text
chore(tools): update pact-broker-cli to 0.8.8

  tools/pact-broker-cli/tool.yaml                                   version: "0.8.8"
  tools/pact-broker-cli/tool.lock.json                              tag, upstream asset, sha256
  tools/pact-broker-cli/pact-broker-cli-x86_64-pc-windows-msvc.zip  verified binary
```

📖 **Full documentation** lives in [`docs/`](docs/) and is published by the pipeline (GitLab Pages / GitHub Pages). Preview it with `mkdocs serve`.

## How it works

```text
tools/<name>/tool.yaml
        │
        ▼
toolbot check ── source provider (GitHub / GitLab release APIs)
        │          newest acceptable release: tag template, SemVer/PEP 440 ordering,
        │          prereleases off by default, ignore rules
        │
        ├── target provider (GitLab): open MR for toolbot/<tool>/<version>? closed = rejected?
        │
        ├── download every asset → verify checksums → atomic replace → tool.lock.json
        ├── private temp repo + worktree → commit → push --force-with-lease
        └── create merge request, supersede older Toolbot MRs for the tool
```

- **One active update per tool.** A newer release supersedes the open MR instead of piling up MRs.
- **Idempotent.** Re-runs find the existing MR by its deterministic branch and do nothing.
- **Rejections stick.** Closing an MR without merging stops that version being proposed; `ignore` makes it explicit.
- **Human work is safe.** Branches with commits from people are never overwritten or closed.
- **All or nothing.** A failed download or checksum leaves every file untouched; other tools continue.

Details: [update lifecycle](docs/guide/lifecycle.md) · [architecture](docs/guide/introduction.md).

## Quick start

```sh
pip install -e .                  # Python 3.11+, git
toolbot list
toolbot outdated
toolbot check --dry-run           # what would happen; changes nothing
toolbot update pact-broker-cli --latest   # apply to the working tree (no git)
```

| Command | Purpose |
| --- | --- |
| `toolbot list` | Configured tools |
| `toolbot validate` | Validate definitions (offline) |
| `toolbot verify` | Committed artifacts match lock files (offline; run in MR pipelines) |
| `toolbot outdated [--json]` | Current vs latest per tool |
| `toolbot update TOOL [--version V \| --latest]` | Fetch, verify and install into the working tree |
| `toolbot check [TOOL…] [--dry-run]` | Full lifecycle: branch, commit, push, merge request |

## A tool definition

```yaml
# tools/pact-broker-cli/tool.yaml
name: pact-broker-cli
version: "0.8.6"
tag: v{version}
source:
  provider: github            # or gitlab (+ host: for self-managed / Enterprise)
  repository: pact-foundation/pact-broker-cli
checksum:
  type: file
  file: "{asset}.sha256"
assets:
  windows-amd64:
    name: pact-broker-cli-x86_64-pc-windows-msvc.zip
    destination: pact-broker-cli-x86_64-pc-windows-msvc.zip
# prereleases: true
# ignore: ["0.9.0", ">=2.0.0"]
```

A GitLab-hosted, multi-asset example is in [`examples/tools/glab/tool.yaml`](examples/tools/glab/tool.yaml). Reference: [tool.yaml](docs/reference/tool-yaml.md).

## Running it in GitLab CI

```yaml
include:
  - project: platform/toolbot
    ref: v0.1.0
    file: /templates/toolbot.gitlab-ci.yml
variables:
  TOOLBOT_IMAGE: gitlab.example.com:5050/platform/toolbot:0.1.0
```

Add the masked, protected variable `TOOLBOT_GITLAB_TOKEN` (project access token, `api`, Developer), then a pipeline schedule with `TOOLBOT_RUN=true`. Optional: `TOOLBOT_GITHUB_TOKEN` (read-only) to avoid GitHub rate limits. Step by step: [GitLab CI](docs/deploy/gitlab.md) · [tokens](docs/deploy/tokens.md).

| I want to… | Read |
| --- | --- |
| Try it locally, including a full run against a test project | [Running locally](docs/deploy/local.md) |
| Add a tool | [Adding a tool](docs/guide/adding-a-tool.md) |
| Understand prereleases, ignore rules and rejections | [Versions](docs/guide/versions.md) |
| Configure checksums | [Checksums](docs/guide/checksums.md) |
| Build or mirror the image | [Container image](docs/deploy/image.md) |
| Host the project on GitHub (CI, GHCR, Pages) | [GitHub Actions](docs/deploy/github-actions.md) |
| Run on Kubernetes / Jenkins / Docker | [Anywhere](docs/deploy/anywhere.md) |
| Review the security model | [Security](docs/guide/security.md) |
| Fix a problem | [Troubleshooting](docs/guide/troubleshooting.md) |

## Repository layout

```text
├── src/toolbot/              the application (see docs/contributing/development.md)
├── tests/                    pytest; tests/integration is opt-in (-m integration)
├── tools/                    tools tracked by this project (Pact Broker CLI)
├── examples/tools/glab/      GitLab-hosted, multi-asset example
├── schema/tool.schema.json   editor schema for tool.yaml
├── templates/                GitLab CI template for consuming projects
├── docs/ + mkdocs.yml        documentation site
├── Dockerfile                python:3.13-slim + git + toolbot
├── requirements.lock         pinned runtime dependencies for the image
├── scripts/                  image smoke test
├── .gitlab-ci.yml            test · image · docs → Pages · scheduled self-check
└── .github/workflows/        CI · image (GHCR) · release · docs (Pages) · integration
```

## Development

```sh
pip install -e ".[dev,docs]"
pytest && ruff check src tests && ruff format --check src tests && mypy
pytest -m integration        # live GitHub + GitLab
mkdocs serve
```
