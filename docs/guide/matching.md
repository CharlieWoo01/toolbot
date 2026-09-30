# Tags & asset matching

## Tags

`version` is the plain version (`0.8.6`); `tag` says how upstream turns it into a git tag. The template contains `{version}` exactly once.

| Upstream tags | `tag:` |
| --- | --- |
| `1.2.3` | *(omit; default)* or `"{version}"` |
| `v1.2.3` | `v{version}` |
| `release-1.2.3` | `release-{version}` |
| `cli-v1.2.3` (monorepo) | `cli-v{version}` |
| `cli/v1.2.3` | `cli/v{version}` |

Release discovery only considers tags that match the template, so in a monorepo tagging `cli-v…` and `server-v…`, `tag: cli-v{version}` follows only the CLI.

!!! warning "Quote templates that start with `{`"
    `tag: {version}` is a YAML *mapping*, not a string. Write `tag: "{version}"`. The validator points this out.

## Assets

Each entry under `assets` needs exactly one of `name` or `pattern`.

### `name`: exact (preferred)

```yaml
assets:
  linux-amd64:
    name: tool_{version}_linux_amd64.tar.gz
```

After placeholder substitution the name must equal an upstream asset exactly. `tool.zip` never matches `tool.zip.sha256`.

### `pattern`: glob, for unpredictable names

For names that include build numbers or commit hashes:

```yaml
assets:
  windows-amd64:
    pattern: tool-{version}+*-windows-amd64.zip
```

`*` matches any characters and `?` matches one. A pattern must match **exactly one** asset of the release:

- **No match:** the update fails and lists the available names.
- **Several matches:** the update fails and lists them.
- **Two asset entries resolving to the same file:** the update fails.

Validation also rejects patterns that are mostly wildcards (`*`, `*.zip`), `**`, and patterns without any wildcard. A too-broad pattern can never fetch the wrong platform or architecture; at worst the update stops.

## Placeholders

| Placeholder | Example | Allowed in |
| --- | --- | --- |
| `{version}` | `0.8.8` | `tag`, asset `name`/`pattern`, checksum `file` |
| `{tag}` | `v0.8.8` | asset `name`/`pattern`, checksum `file` |
| `{asset}` | `tool-linux-amd64.tar.gz` | checksum `file` |

There's no other templating. Unknown placeholders such as `{verison}` are validation errors.

## Destinations

`destination` is relative to the tool directory (`tools/<name>/`) and may include sub-directories:

```yaml
destination: linux-amd64/tool.tar.gz   # → tools/<name>/linux-amd64/tool.tar.gz
```

Destinations can't leave the tool directory, overwrite `tool.yaml`/`tool.lock.json`, be hidden files, or collide with each other (case-insensitively, so checkouts on Windows and macOS stay safe).
