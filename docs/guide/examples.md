# Examples

The repository's `examples/tools/` folder holds real, working definitions. Each file is commented. They were all checked against the projects' live releases.

| Example | Source | Demonstrates |
| --- | --- | --- |
| `pact-broker-cli` *(in `tools/`)* | GitHub | Tracked by this repository: one asset, `.sha256` sidecar |
| `pact-cli` | GitHub | Fixed asset names, sidecars, two platforms |
| `pact-standalone` | GitHub | `{version}` in asset names; provider digest when upstream only has SHA-1 |
| `pact-stub-server` | GitHub | Gzipped single executables |
| `pact-verifier-cli` | GitHub | Monorepo tag prefix `pact_verifier_cli-v{version}` |
| `pact-plugin-cli` | GitHub | Monorepo tag prefix `pact-plugin-cli-v{version}` |
| `gitleaks` | GitHub | Checksum file named with the version |
| `trivy` | GitHub | `ignore` to hold back a major version |
| `glab` | GitLab | A tool released on GitLab.com |

Try them without changing anything:

```sh
toolbot --root examples outdated
toolbot --root examples check --dry-run
```

To use one, copy its folder into `tools/`, run `toolbot update <name>`, and commit.

## Reading a definition: the four questions

Every definition answers the same four questions about the upstream project.

| Question | Field | pact-cli answer |
| --- | --- | --- |
| Where are releases published? | `source` | GitHub, `pact-foundation/pact-cli` |
| What do its tags look like? | `tag` | `v0.10.8` → `v{version}` |
| Which files do I want? | `assets.*.name` | `pact-x86_64-pc-windows-msvc.zip` |
| How do I know they're genuine? | `checksum` | the `.sha256` file next to each asset |

Find the answers on the project's releases page, then write them down.

## Walkthrough: glab, a GitLab-hosted tool

"GitLab source" only means *the tool's releases are published on GitLab* (here gitlab.com). It has nothing to do with where your repository or Toolbot runs. A GitHub-hosted tool can be updated in a GitLab repository, and a GitLab-hosted tool in any repository Toolbot targets.

```yaml
name: glab
version: "1.119.0"
tag: v{version}

source:
  provider: gitlab
  repository: gitlab-org/cli

checksum:
  type: file
  file: checksums.txt

assets:
  linux-amd64:
    name: glab_{version}_linux_amd64.tar.gz
    destination: linux-amd64/glab.tar.gz
  windows-amd64:
    name: glab_{version}_windows_amd64.zip
    destination: windows-amd64/glab.zip
```

When upstream publishes **v1.120.0**, a scheduled `toolbot check` does this:

1. **Discover.** It calls `https://gitlab.com/api/v4/projects/gitlab-org%2Fcli/releases` and reads each release's tag. `v1.120.0` matches `v{version}`, which gives version `1.120.0`.
2. **Compare.** `1.120.0` is the highest stable version and is newer than `1.119.0`, so an update is available.
3. **Resolve.** It fills `{version}` into the names: `glab_1.120.0_linux_amd64.tar.gz` and `glab_1.120.0_windows_amd64.zip`. Both must exist in the v1.120.0 release, and so must `checksums.txt`.
4. **Check existing merge requests.** Is there one from `toolbot/glab/1.120.0`? If it's open, stop. If it was closed without merging, the version was rejected, so skip it.
5. **Download and verify.** It downloads both files into a temporary folder and compares each file's SHA-256 with its line in `checksums.txt`:

    ```text
    4e6c59de9f7ed2f304bf93aad01ea8f8a69584f0450ce90ad696ef81f69c69aa  glab_1.120.0_linux_amd64.tar.gz
    4240365e23ac7cb5ce09aa0f5892cd66b0da103963fe01954c8d64727039a3f6  glab_1.120.0_windows_amd64.zip
    ```

    If either doesn't match, the update stops and nothing in the repository changes.

6. **Apply.** It writes `tools/glab/linux-amd64/glab.tar.gz` and `tools/glab/windows-amd64/glab.zip`, sets `version: "1.120.0"`, and records both files in `tool.lock.json`.
7. **Propose.** It commits on branch `toolbot/glab/1.120.0`, pushes, and opens one merge request, `chore(tools): update glab to 1.120.0`, containing all four changed files.

The destinations don't contain the version, so the merge request shows the binaries as *modified*, and your scripts can always refer to `tools/glab/linux-amd64/glab.tar.gz`.

## Recipes

### Asset names without a version

```yaml
name: pact-x86_64-pc-windows-msvc.zip
```

### Asset names with a version

```yaml
name: pact-{version}-windows-x86_64.zip     # pact-2.6.4-windows-x86_64.zip
```

### Monorepo: several components released from one repository

```yaml
repository: pact-foundation/pact-reference
tag: pact_verifier_cli-v{version}           # other components' tags are ignored
```

### Checksum per file / one shared file / none published

```yaml
checksum: { type: file, file: "{asset}.sha256" }              # sidecar per asset
checksum: { type: file, file: checksums.txt }                 # one file for all
checksum: { type: file, file: "tool_{version}_checksums.txt" } # shared, versioned name
checksum: { type: provider }                                  # GitHub's own SHA-256 digest
```

### Stay on a major version

```yaml
ignore:
  - ">=1.0.0"
```

## Tools that don't fit (yet)

Toolbot's providers read **GitHub or GitLab releases**. Some popular tools distribute binaries elsewhere:

| Tool | Where binaries live | Status |
| --- | --- | --- |
| Terraform, Vault, Packer | `releases.hashicorp.com` | Needs a future "direct HTTP" provider |
| kubectl | `dl.k8s.io` | Needs a future "direct HTTP" provider |
| Helm | `get.helm.sh` (GitHub releases hold only signatures) | Needs a future "direct HTTP" provider |

See [Adding a provider](../contributing/adding-a-provider.md).
