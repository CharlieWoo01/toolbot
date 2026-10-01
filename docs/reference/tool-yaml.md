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
| `ignore` | string or list | | `[]` | Versions never proposed: exact (`"0.9.0"`) or constraints (`">=2.0.0"`, `">=1.4, <1.5"`). |
| `allow` | string or list | | `[]` | Constraints every proposed version must satisfy, e.g. `"<2.0.0"` (stay on 1.x) or `"==1.4.2"` (pin). |
| `source` | map | ✅ | | Where versions (and usually files) come from. |
| `checksum` | map | | `{type: none}` | Default verification for every asset. |
| `assets` | map | one of | | Files to download and commit, keyed by id. |
| `version-files` | list | one of | | Version pins in other files to bump. A tool needs `assets`, `version-files` or both. |

## `source`

| Field | Required | Description |
| --- | --- | --- |
| `provider` | ✅ unless `host` names a host | `github` or `gitlab`. |
| `repository` | ✅ | `github`: `owner/repo`. `gitlab`: `group[/subgroup…]/project`. |
| `host` | | A host name from [`toolbot.yaml`](../guide/hosts.md) (e.g. `internal-gitlab`), or an `https://` base URL for GitHub Enterprise / self-managed GitLab. Default: github.com / gitlab.com. |
| `versions` | | `releases` (default): GitHub/GitLab releases. `tags`: plain git tags, for projects that tag but never create releases. With `tags`, assets must use `url` or `path`. |

## `assets.<id>`

`<id>`: lowercase letters, digits and `-`, e.g. `linux-amd64`. Exactly one of `name`, `pattern`, `url` or `path`:

| Field | Description |
| --- | --- |
| `name` | Exact release asset name. Placeholders `{version}`, `{tag}`. |
| `pattern` | Glob (`*`, `?`) matching exactly one release asset. Placeholders `{version}`, `{tag}`. |
| `url` | `https://` download URL, for tools published outside release assets (Terraform, Helm, kubectl). Placeholders `{version}`, `{tag}`. |
| `path` | A file **committed in the source repository**, read at the release tag, e.g. `dist/linux/tool`. Placeholders `{version}`, `{tag}`. |

Plus:

| Field | Required | Description |
| --- | --- | --- |
| `destination` | ✅ | Where the file is committed: relative to `tools/<name>/`, or to the **repository root** with a leading `/` (e.g. `/bin/terraform`). `/`-separated; each segment starts with a letter or digit. |
| `extract` | | The download is an archive (zip, tar, tar.gz/.tgz, tar.xz, tar.bz2, detected from content): commit only this file from it, e.g. `terraform` or `linux-amd64/helm`. The checksum is verified on the archive first. Placeholders `{version}`, `{tag}`. |
| `storage` | | `auto` (default): plain git up to `defaults.max-file-mb` in `toolbot.yaml` (50), otherwise `defaults.large-files`. `git`, `lfs` (Git LFS) or `fetch` (not committed; restored by `toolbot fetch`). See [Large binaries](../guide/what-can-be-updated.md#large-binaries-terraform-is-120-mb). |
| `executable` | | Commit with the executable bit (mode `100755`, also from Windows). Default: `true` when the destination has no extension or ends in `.sh`/`.bash`/`.py`. |
| `checksum` | | Overrides the tool-level `checksum`. |

## `checksum`

| Field | Applies to | Description |
| --- | --- | --- |
| `type` | all | `none`, `provider` (GitHub release assets only) or `file`. |
| `file` | `file` | Release asset holding checksums, e.g. `"{asset}.sha256"`, `SHA256SUMS`, `checksums.txt`. |
| `url` | `file` | URL of the checksum file instead, e.g. `https://get.helm.sh/{asset}.sha256sum`. |
| `path` | `file` | Checksum file committed in the source repository at the release tag, e.g. `dist/SHA256SUMS`. |
| `algorithm` | `file` | `sha256` (default) or `sha512`. |

`type: file` needs exactly one of `file`, `url` or `path`; they may use `{asset}` (the downloaded file's name), `{version}` and `{tag}`.

## `version-files[]`

| Field | Required | Description |
| --- | --- | --- |
| `path` | ✅ | The file: relative to `tools/<name>/`, or to the repository root with a leading `/`. Hidden files (`/.terraform-version`, `/.gitlab-ci.yml`) are allowed; `.git` is not. |
| `template` | ✅ | One line of literal text containing `{version}` or `{tag}`, e.g. `ARG TERRAFORM_VERSION={version}`. |

Toolbot replaces the template rendered for the current version with the template rendered for the new one. If the file doesn't contain the current text, the update fails (and `toolbot verify` reports it). Where the template starts or ends with a placeholder, the match can't be part of a longer version: `1.2.0` never matches inside `11.2.0` or `1.2.0-rc.1`. See [What can be updated](../guide/what-can-be-updated.md#version-pins-only).

## Validation rules beyond single fields

- unknown keys at any level are errors
- `version` must parse under `versioning`, and every `ignore`/`allow` entry must be valid
- no duplicate destinations (case-insensitive), and none nested inside another; two tools can't commit the same root-relative file
- no two assets with an identical `name`/`pattern`/`url`/`path` and `extract`
- patterns need a wildcard and enough literal text, and no `**`
- `checksum.type: provider` only for GitHub release assets
- `source.versions: tags` only with `url`/`path` assets and `url`/`path` checksums
- destinations can't be `tool.yaml`/`tool.lock.json`, point into `.git` or into `tools/` from the root
- YAML duplicate keys, aliases and non-standard tags are rejected

## Complete example

```yaml
# yaml-language-server: $schema=../../schema/tool.schema.json
name: example-cli
version: "2.4.1"
tag: example-cli-v{version}
versioning: semver
prereleases: false
ignore: ["2.5.0"]
allow: "<3.0.0"

source:
  host: internal-gitlab            # a host from toolbot.yaml; provider comes from it
  repository: platform/tools/example-cli

checksum:
  type: file
  file: SHA256SUMS

assets:
  linux-amd64:
    name: example-cli_{version}_linux_amd64.tar.gz
    extract: example-cli            # commit the binary, not the archive
    destination: /bin/example-cli   # repository root
  windows-amd64:
    pattern: example-cli_{version}_windows_amd64_*.zip
    extract: example-cli.exe
    destination: windows-amd64/example-cli.exe
  darwin-arm64:
    name: example-cli_{version}_darwin_arm64.tar.gz
    destination: darwin-arm64/example-cli.tar.gz
    checksum:
      type: file
      file: "{asset}.sha512"
      algorithm: sha512

version-files:
  - path: /Dockerfile
    template: "ARG EXAMPLE_CLI_VERSION={version}"
```
