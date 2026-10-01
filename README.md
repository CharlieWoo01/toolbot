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
tools/<name>/tool.yaml                     toolbot.yaml (optional, GitOps)
        │                                   hosts · central rules · target sets
        ▼
toolbot check ── source provider (GitHub / GitLab releases or tags, any instance)
        │          newest acceptable version: tag template, SemVer/PEP 440 ordering,
        │          prereleases off by default, ignore/allow rules
        │
        ├── target provider (GitLab MRs / GitHub PRs): open request for toolbot/<tool>/<version>?
        │
        ├── download (release asset, URL, or file in a repo) → verify checksum
        │   → extract from zip/tar if asked → bump version pins → atomic replace → tool.lock.json
        ├── private temp repo + worktree → commit → push --force-with-lease
        └── create merge/pull request, supersede older Toolbot requests for the tool
```

What a tool update can be: a release asset, a file from a download site, **the binary inside a ZIP/tar.gz**, a file committed in another repository, or **just version pins** (`.terraform-version`, a Dockerfile `ARG`), anywhere in the repository. Files too big for a plain commit (Terraform is ~120 MB) automatically go through **Git LFS**, or aren't committed at all and are restored with `toolbot fetch`. See [What can be updated](docs/guide/what-can-be-updated.md).

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
| `toolbot targets [--targets SET]` | Repositories a target set in `toolbot.yaml` covers |
| `toolbot update TOOL [--version V \| --latest]` | Fetch, verify and install into the working tree |
| `toolbot fetch [TOOL…]` | Restore large files that aren't committed (`storage: fetch`), verified against the lock files |
| `toolbot check [TOOL…] [--targets SET] [--dry-run]` | Full lifecycle: branch, commit, push, merge/pull request |

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

More real, commented definitions are in [`examples/`](examples/README.md): the Pact CLIs (including monorepo tags), gitleaks, trivy, the GitLab-hosted glab, Terraform (ZIP extract, and a version-pin-only variant), Helm and kubectl. Reference: [tool.yaml](docs/reference/tool-yaml.md).

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

## Running it on GitHub

Toolbot is also a GitHub Action: copy [`templates/github/toolbot.yml`](templates/github/toolbot.yml) and reference `your-org/toolbot@v0.1.0`. No Marketplace publishing is needed. See [GitHub Actions](docs/deploy/github-actions.md).

## Many repositories from one place (GitOps)

A control repository's `toolbot.yaml` lists hosts (public and internal GitHub/GitLab), central version rules, and target sets (repositories, whole groups or organisations), each run by its own schedule. See [Control repository](docs/deploy/control-repository.md) and [`examples/control-repo/`](examples/control-repo/README.md).

| I want to… | Read |
| --- | --- |
| Try it locally, including a full run against a test project | [Running locally](docs/deploy/local.md) |
| Update ZIPs, binaries in repos, or just version pins | [What can be updated](docs/guide/what-can-be-updated.md) |
| Use internal GitLab / GitHub Enterprise | [Hosts](docs/guide/hosts.md) |
| Add a tool | [Adding a tool](docs/guide/adding-a-tool.md) · [Examples](docs/guide/examples.md) |
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
├── examples/tools/           commented example definitions (Pact CLIs, gitleaks, trivy, glab, Terraform, Helm, kubectl)
├── examples/control-repo/    example GitOps control repository (toolbot.yaml + schedules)
├── schema/                   editor schemas for tool.yaml and toolbot.yaml
├── templates/                GitLab CI template and GitHub workflow for consuming repositories
├── action.yml                GitHub Action (uses: your-org/toolbot@<tag>)
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
