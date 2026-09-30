# CLI

```text
toolbot [--root DIR] [--version] COMMAND …
```

| Option | Description |
| --- | --- |
| `--root DIR` | Repository root containing `tools/` (default: current directory). Used by every command except a real `check`, which reads definitions from the target branch. |
| `--version` | Print the Toolbot version. |

## Read-only

### `toolbot list [--json]`

Configured tools: name, version, source and asset ids.

### `toolbot validate`

Validates every `tools/*/tool.yaml` and reports every problem. Offline.

### `toolbot verify`

Offline check that each tool's lock file matches its definition (version, tag, source, assets) and that every committed artifact exists with the recorded SHA-256. Run it on every merge request.

### `toolbot outdated [TOOL…] [--json]`

Queries upstream and prints:

```text
TOOL             CURRENT  LATEST   STATUS
pact-broker-cli  0.8.6    0.8.8    update available
glab             1.119.0  1.120.0  update available (ignored: 1.121.0)
```

`--json` prints a list of `{tool, current, latest, status}` objects. The exit code is 1 if any tool errored.

## Local change

### `toolbot update TOOL [--version V | --latest]`

Downloads, verifies and installs a tool's assets into the working tree, and updates `tool.lock.json` (and `version:` when the version changes). No git, no merge request.

| Form | Installs |
| --- | --- |
| `toolbot update TOOL` | The version declared in `tool.yaml`: after adding a tool, or to repair files |
| `toolbot update TOOL --latest` | The newest acceptable release |
| `toolbot update TOOL --version V` | Version `V` |

## Automation

### `toolbot check [TOOL…] [--dry-run] [--base BRANCH]`

The full [update lifecycle](../guide/lifecycle.md) for every tool (or just the named ones): discover, compare, look up existing MRs, apply, commit, push, and create or supersede MRs.

| Option | Description |
| --- | --- |
| `--dry-run` | Report what would happen. Nothing is downloaded, written, committed, pushed or created. Reads definitions from `--root`. Checks existing MRs if a target token is configured. |
| `--base BRANCH` | Target branch (default: `TOOLBOT_BASE_BRANCH`, else the project's default branch). |

Per-tool output, then a summary: `3 tool(s): 1 created, 1 exists, 1 up-to-date`.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success. For `check`: no tool failed (blocked or rejected tools don't count as failures) |
| `1` | At least one failure; details on stderr as `error [CODE]: …` |
| `2` | Usage or configuration error (e.g. missing `TOOLBOT_GITLAB_TOKEN`) |

## Output streams

Results (tables, per-tool outcomes, JSON) go to **stdout**. Progress (`toolbot: …`), warnings and errors go to **stderr**.
