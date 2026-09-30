# Introduction

## Why Toolbot exists

Package managers keep npm, Maven or pip dependencies current. Teams also depend on things no package manager covers: the Pact Broker CLI zip used by contract tests, a pinned Terraform binary, a scanner JAR, a vendor's Windows installer. These are usually downloaded by hand, committed or cached somewhere, and forgotten until something breaks.

Toolbot gives those tools the same automated, reviewable update flow that Dependabot gives to packages.

## Terminology

| Term | Meaning |
| --- | --- |
| **Toolbot** | This application: the `toolbot` CLI and its container image. |
| **Tool** | An external CLI/binary/archive being tracked, defined by `tools/<name>/tool.yaml`. |
| **Asset** | One file of an upstream release, e.g. the Windows zip. A tool can have several. |
| **Source provider** | Where upstream releases are published: `github` or `gitlab`. |
| **Target provider** | Where Toolbot proposes updates. MVP: GitLab (merge requests). |
| **Update request** | A merge request (GitLab) or, in future, a pull request (GitHub). |

## Architecture

```text
                        tools/<name>/tool.yaml
                                  │
                                  ▼
 ┌──────────────────────────── toolbot check ────────────────────────────┐
 │                                                                         │
 │  config       load + validate definitions                               │
 │     │                                                                   │
 │  discovery ── SourceProvider ── GitHub / GitLab release APIs            │
 │     │         (list releases, assets, digests)                          │
 │  versions     tag → version, ordering, prereleases, ignore rules        │
 │     │                                                                   │
 │  lifecycle ── TargetProvider ── GitLab MR API                           │
 │     │         (existing MRs? rejected? supersede?)                      │
 │  artifacts    download → verify checksums → atomic replace → lock file  │
 │     │                                                                   │
 │  git          private temp repo + worktree → commit → push (lease)      │
 │     │                                                                   │
 │  target       create merge request, close superseded ones               │
 └─────────────────────────────────────────────────────────────────────────┘
```

| Module | Responsibility |
| --- | --- |
| `toolbot.config` | Load and validate `tool.yaml`; placeholders; version-line rewrite |
| `toolbot.versions` | Parse and order versions (SemVer, PEP 440), tag templates, ignore constraints |
| `toolbot.providers.source` | `SourceProvider` interface; GitHub and GitLab implementations |
| `toolbot.providers.target` | `TargetProvider` interface; GitLab implementation |
| `toolbot.assets` | Deterministic asset matching, checksum files, transactional install |
| `toolbot.http` | HTTPS-only client: timeouts, retries, redirect credential stripping |
| `toolbot.git` | Git via argument lists only; private temp repository; leased pushes |
| `toolbot.services` | Use cases: discovery, applying a version, verification, the update lifecycle |
| `toolbot.cli` | Commands and output |

Only the parts that genuinely vary have abstractions: source providers, target providers, versioning schemes and checksum strategies. Everything else is plain functions.

## Sources and targets are independent

A tool released on GitHub can be updated in a GitLab repository. The source belongs to each tool definition; the target is where Toolbot runs against (configured by environment). A GitHub target producing pull requests is a matter of implementing one more `TargetProvider`.

## What a repository using Toolbot contains

```text
your-repo/
├── .gitlab-ci.yml                  # includes the Toolbot CI template
└── tools/
    └── pact-broker-cli/
        ├── tool.yaml               # written by you
        ├── tool.lock.json          # generated
        └── pact-broker-cli-x86_64-pc-windows-msvc.zip
```
