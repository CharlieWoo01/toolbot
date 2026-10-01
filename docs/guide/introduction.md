# Introduction

## Why Toolbot exists

Package managers keep npm, Maven or pip dependencies current. Teams also depend on things no package manager covers: the Pact Broker CLI zip used by contract tests, a pinned Terraform binary, a scanner JAR, a vendor's Windows installer. These are usually downloaded by hand, committed or cached somewhere, and forgotten until something breaks.

Toolbot gives those tools the same automated, reviewable update flow that Dependabot gives to packages.

## Terminology

| Term | Meaning |
| --- | --- |
| **Toolbot** | This application: the `toolbot` CLI and its container image. |
| **Tool** | An external CLI/binary/archive being tracked, defined by `tools/<name>/tool.yaml`. |
| **Asset** | One file Toolbot commits: a release asset, a download, a file extracted from an archive, or a file from the upstream repository. A tool can have several. |
| **Version file** | A file whose version pin (e.g. `ARG TERRAFORM_VERSION=1.16.3`) is bumped with the tool. |
| **Host** | One GitHub or GitLab instance (public or internal), with the variable holding its token. |
| **Source provider** | Where upstream versions are published: `github` or `gitlab` releases or tags. |
| **Target provider** | Where Toolbot proposes updates: GitLab (merge requests) or GitHub (pull requests). |
| **Update request** | A merge request (GitLab) or pull request (GitHub). |
| **Control repository** | Optional GitOps repository with `toolbot.yaml`: hosts, central rules and the target repositories Toolbot maintains. |

## Architecture

```text
                        tools/<name>/tool.yaml
                                  │
                                  ▼
 ┌──────────────────────────── toolbot check ────────────────────────────┐
 │                                                                         │
 │  config       load + validate definitions                               │
 │     │                                                                   │
 │  discovery ── SourceProvider ── GitHub / GitLab release + tag APIs      │
 │     │         (list releases or tags, assets, digests)                  │
 │  versions     tag → version, ordering, prereleases, ignore/allow rules  │
 │     │                                                                   │
 │  lifecycle ── TargetProvider ── GitLab MR / GitHub PR API               │
 │     │         (existing requests? rejected? supersede?)                 │
 │  artifacts    download → verify → extract → bump version pins          │
 │     │         → atomic replace → lock file                              │
 │  git          private temp repo + worktree → commit → push (lease)      │
 │     │                                                                   │
 │  target       create merge/pull request, close superseded ones          │
 └─────────────────────────────────────────────────────────────────────────┘
```

| Module | Responsibility |
| --- | --- |
| `toolbot.config` | Load and validate `tool.yaml` and `toolbot.yaml`; placeholders; version-line rewrite |
| `toolbot.hosts` | Named GitHub/GitLab instances and which token goes where |
| `toolbot.versions` | Parse and order versions (SemVer, PEP 440), tag templates, ignore/allow constraints |
| `toolbot.version_files` | Version pins in other files (`version-files`) |
| `toolbot.providers.source` | `SourceProvider` interface; GitHub and GitLab implementations |
| `toolbot.providers.target` | `TargetProvider` interface; GitLab and GitHub implementations; group listing |
| `toolbot.assets` | Deterministic asset matching, checksum files, archive extraction, transactional install |
| `toolbot.http` | HTTPS-only client: timeouts, retries, redirect credential stripping |
| `toolbot.git` | Git via argument lists only; private temp repository; leased pushes |
| `toolbot.services` | Use cases: discovery, applying a version, verification, the update lifecycle |
| `toolbot.cli` | Commands and output |

Only the parts that genuinely vary have abstractions: source providers, target providers, versioning schemes and checksum strategies. Everything else is plain functions.

## Sources and targets are independent

A tool released on GitHub can be updated in a GitLab repository, and the other way round. The source belongs to each tool definition. The target is the repository Toolbot runs in (from the CI environment) or the repositories listed in a [control repository](../deploy/control-repository.md).

## What a repository using Toolbot contains

```text
your-repo/
├── .gitlab-ci.yml                  # includes the Toolbot CI template (or a GitHub workflow)
├── .terraform-version              # optional: a version pin Toolbot bumps
├── bin/terraform                   # optional: a binary outside tools/ (destination: /bin/terraform)
└── tools/
    └── pact-broker-cli/
        ├── tool.yaml               # written by you
        ├── tool.lock.json          # generated
        └── pact-broker-cli-x86_64-pc-windows-msvc.zip
```
