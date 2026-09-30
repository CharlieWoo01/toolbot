# tool.yaml

Location: `tools/<name>/tool.yaml`, one tool per file. Editors that support `yaml-language-server` get completion and validation from the bundled schema:

```yaml
# yaml-language-server: $schema=../../schema/tool.schema.json
```

`toolbot validate` is authoritative, and also enforces the cross-field rules the schema can't express.

## Top level

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `name` | string | ✅ | | Lowercase letters, digits, `-`; max 64; must equal the directory name. |
| `version` | string | ✅ | | Current version without tag prefix. **Quote it.** Toolbot rewrites this line on update. |
| `tag` | string | | `"{version}"` | Upstream tag template containing `{version}` exactly once. Other characters: letters, digits, `. _ / @ + -`. |
| `versioning` | `semver` \| `pep440` | | `semver` | How versions are parsed and ordered. |
| `prereleases` | bool | | `false` | Also propose prereleases. |
| `ignore` | list of strings | | `[]` | Versions never proposed: exact (`"0.9.0"`) or constraints (`">=2.0.0"`, `">=1.4, <1.5"`). |
| `source` | map | ✅ | | Where releases are published. |
| `checksum` | map | | `{type: none}` | Default verification for every asset. |
| `assets` | map | ✅ | | At least one asset, keyed by id. |

## `source`

| Field | Required | Description |
| --- | --- | --- |
| `provider` | ✅ | `github` or `gitlab`. |
| `repository` | ✅ | `github`: `owner/repo`. `gitlab`: `group[/subgroup…]/project`. |
| `host` | | `https://` base URL for GitHub Enterprise or self-managed GitLab (path prefixes allowed). |

## `assets.<id>`

`<id>`: lowercase letters, digits and `-`, e.g. `linux-amd64`.

| Field | Required | Description |
| --- | --- | --- |
| `name` | one of | Exact upstream asset name. Placeholders `{version}`, `{tag}`. |
| `pattern` | one of | Glob (`*`, `?`) matching exactly one asset. Placeholders `{version}`, `{tag}`. |
| `destination` | ✅ | Path relative to `tools/<name>/`, `/`-separated; each segment starts with a letter or digit. |
| `checksum` | | Overrides the tool-level `checksum`. |

## `checksum`

| Field | Applies to | Description |
| --- | --- | --- |
| `type` | all | `none`, `provider` (GitHub only) or `file`. |
| `file` | `file` | Release asset holding checksums. Placeholders `{asset}`, `{version}`, `{tag}`. |
| `algorithm` | `file` | `sha256` (default) or `sha512`. |

## Validation rules beyond single fields

- unknown keys at any level are errors
- `version` must parse under `versioning`, and every `ignore` entry must be valid
- no duplicate destinations (case-insensitive), and none nested inside another
- no two assets with an identical `name`/`pattern`
- `name` and `pattern` are mutually exclusive
- patterns need a wildcard and enough literal text, and no `**`
- `checksum.type: provider` only where the provider publishes digests
- YAML duplicate keys, aliases and non-standard tags are rejected

## Complete example

```yaml
# yaml-language-server: $schema=../../schema/tool.schema.json
name: example-cli
version: "2.4.1"
tag: example-cli-v{version}
versioning: semver
prereleases: false
ignore:
  - "2.5.0"
  - ">=3.0.0"

source:
  provider: gitlab
  repository: platform/tools/example-cli
  host: https://gitlab.example.com

checksum:
  type: file
  file: SHA256SUMS

assets:
  linux-amd64:
    name: example-cli_{version}_linux_amd64.tar.gz
    destination: linux-amd64/example-cli.tar.gz
  windows-amd64:
    pattern: example-cli_{version}_windows_amd64_*.zip
    destination: windows-amd64/example-cli.zip
  darwin-arm64:
    name: example-cli_{version}_darwin_arm64.tar.gz
    destination: darwin-arm64/example-cli.tar.gz
    checksum:
      type: file
      file: "{asset}.sha512"
      algorithm: sha512
```
