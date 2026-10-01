# CLI

```text
toolbot [--root DIR] [--config FILE] [--version] COMMAND …
```

| Option | Description |
| --- | --- |
| `--root DIR` | Repository root containing `tools/` (default: current directory). Used by every command except a real `check`, which reads definitions from each target's base branch. |
| `--config FILE` | Control configuration: hosts, rules, targets (default: `toolbot.yaml` in `--root`, if present). See [Control repository](../deploy/control-repository.md). |
| `--version` | Print the Toolbot version. |

## Read-only

### `toolbot list [--json]`

Configured tools: name, version, source and asset ids.

### `toolbot validate`

Validates `toolbot.yaml` (if present) and every `tools/*/tool.yaml`, and reports every problem. Offline.

### `toolbot verify`

Offline check that each tool's lock file matches its definition (version, tag, source, assets), that every committed artifact exists with the recorded SHA-256, and that every `version-files` entry pins the declared version. Run it on every merge request.

### `toolbot targets [--targets SET] [--json]`

Lists the repositories a target set from `toolbot.yaml` resolves to, expanding groups and organisations. `--targets all` lists every set. With a single set, `--targets` can be omitted. Read-only; needs the hosts' tokens to list groups.

### `toolbot outdated [TOOL…] [--json]`

Queries upstream and prints:

```text
TOOL             CURRENT  LATEST   STATUS
pact-broker-cli  0.8.6    0.8.8    update available
helm             3.21.4   3.22.0   update available (held back: 4.3.0, 4.2.4, 4.2.3 (+12 more))
```

Central rules from `toolbot.yaml` are applied.

`--json` prints a list of `{tool, current, latest, status}` objects. The exit code is 1 if any tool errored.

## Local change

### `toolbot update TOOL [--version V | --latest]`

Downloads, verifies (and extracts) a tool's assets into the working tree, bumps its `version-files`, and updates `tool.lock.json` (and `version:` when the version changes). No git, no merge request.

| Form | Installs |
| --- | --- |
| `toolbot update TOOL` | The version declared in `tool.yaml`: after adding a tool, or to repair files |
| `toolbot update TOOL --latest` | The newest acceptable release |
| `toolbot update TOOL --version V` | Version `V` |

## Automation

### `toolbot check [TOOL…] [--targets SET] [--dry-run] [--base BRANCH]`

The full [update lifecycle](../guide/lifecycle.md) for every tool (or just the named ones): discover, compare, look up existing requests, apply, commit, push, and create or supersede merge/pull requests.

Which repositories: the target set from `toolbot.yaml` if it defines `targets`, otherwise the repository the CI job runs in ([environment](environment.md)). One repository failing (no access, for example) doesn't stop the others.

| Option | Description |
| --- | --- |
| `--targets SET` | Target set from `toolbot.yaml`, or `all`. Optional when only one set exists. |
| `--dry-run` | Report what would happen. Nothing is downloaded, written, committed, pushed or created; download URLs and checksum files are probed with `HEAD`. With a target token, definitions and existing requests are read from the target; without one, definitions come from `--root`. |
| `--base BRANCH` | Target branch (default: the target's `base-branch`, `TOOLBOT_BASE_BRANCH`, else each repository's default branch). |

Per-tool output grouped by repository, then a summary: `3 tool(s): 1 created, 1 exists, 1 up-to-date`.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success. For `check`: no tool failed (blocked or rejected tools don't count as failures) |
| `1` | At least one failure; details on stderr as `error [CODE]: …` |
| `2` | Usage or configuration error (e.g. missing target token) |

## Output streams

Results (tables, per-tool outcomes, JSON) go to **stdout**. Progress (`toolbot: …`), warnings and errors go to **stderr**.
