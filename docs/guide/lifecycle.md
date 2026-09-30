# Update lifecycle

`toolbot check` runs on a schedule, typically daily. It must be safe to run any number of times: never duplicating merge requests, never recreating rejected ones, never destroying human work.

## Identity: one branch per tool and version

Every update has a deterministic branch:

```text
toolbot/<tool>/<version>          e.g. toolbot/pact-broker-cli/0.9.0
```

The branch is the identity. Toolbot looks up merge requests by source branch, in any state, so it doesn't rely on titles. Tool names and versions are validated to safe characters, and branch names are checked with `git check-ref-format`.

Commits carry trailers that identify them as Toolbot's:

```text
chore(tools): update pact-broker-cli to 0.9.0

Update pact-broker-cli from 0.8.6 to 0.9.0.

Toolbot-Tool: pact-broker-cli
Toolbot-Version: 0.9.0
```

## Decisions per tool, per run

```text
discover latest acceptable version
        │
        ├─ not newer than base branch ──► up to date
        │                                 (close open Toolbot MRs made obsolete, if untouched)
        │
        ▼
MR from toolbot/<tool>/<version> exists?
        ├─ open ─────────────────────────► exists: nothing to do
        ├─ merged (but base doesn't have it) ► reverted: skip, don't fight the revert
        ├─ closed without merging ────────► rejected: skip this version
        │
        ▼
other open Toolbot MRs for this tool?
        ├─ a person pushed to one ────────► blocked: report, change nothing
        │
        ▼
apply update in a private worktree from the latest base branch
download → verify → replace → commit → push (lease) → create MR
        │
        ▼
close the other (superseded) MRs with a comment, delete their branches
```

## One active update per tool

When upstream publishes 0.10.0 while the 0.9.0 MR is still open:

1. Toolbot creates `toolbot/pact-broker-cli/0.10.0` and its MR, based on the current base branch. It isn't stacked on the old branch.
2. It closes the 0.9.0 MR with the comment *"Superseded by !42 (pact-broker-cli 0.10.0)."* and deletes its branch.
3. The new MR's description says *"Supersedes !41."*

There's never more than one open Toolbot MR per tool.

### Human changes are never overwritten

Toolbot only supersedes or reuses a branch if its **head commit is Toolbot's own** (it carries `Toolbot-Tool: <tool>`). If someone pushed a commit on top, say to fix a script for the new version, Toolbot reports the tool as **blocked** and does nothing for it until that MR is merged or closed. GitLab's *Rebase* button keeps Toolbot's commit and trailers, so rebased MRs still count as Toolbot's.

## Rejected updates

Closing a Toolbot MR without merging rejects that version. Later runs report:

```text
pact-broker-cli
  Current: 0.8.6
  Latest:  0.9.0
  Skipped: version was rejected.
  0.9.0 was rejected (!41 closed without merging). Reopen it to proceed, or add "0.9.0" to ignore in tool.yaml to make this explicit.
```

When 0.9.1 appears, it's proposed normally. To reverse a rejection, reopen the MR. To make it permanent and visible in the repository, add the version (or a range) to [`ignore`](versions.md#ignoring-versions).

## Interrupted runs

| Failure | Result | Next run |
| --- | --- | --- |
| Upstream unavailable, checksum mismatch… | That tool fails; nothing written; others continue | Tries again |
| Push rejected (permissions, protected pattern…) | That tool fails; no MR | Tries again |
| Branch pushed, MR creation failed | That tool fails; message says the branch was pushed | Reuses the branch (lease on its current head) and creates the MR |
| Branch exists with someone else's commits and no MR | Blocked | Stays blocked until the branch is deleted or merged |

## Concurrency

- The CI template uses `resource_group: toolbot`, so GitLab never runs two Toolbot jobs for a project at once.
- Independently of that, every push uses `--force-with-lease` with the exact expected commit (or "must not exist"). A concurrent run can't overwrite another's branch.
- GitLab refuses a second open MR for the same source branch; Toolbot treats that as "already exists".

## Working copies are never touched

`toolbot check` works in a private temporary repository and worktree: it fetches the base branch (shallow), makes changes there, pushes, and deletes everything. Your checkout, or the CI checkout, is never switched, reset or modified. Definitions are read from the **base branch**, not from local edits.

## Exit codes and summary

Each run ends with a summary such as `3 tool(s): 1 created, 1 exists, 1 failed`. The exit code is **1 if any tool failed** (so the pipeline shows red) and 0 otherwise. *Blocked* and *rejected* tools are reported but aren't failures.
