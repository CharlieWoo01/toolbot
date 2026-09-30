# Adding a tool

Adding a tool is configuration only.

## 1. Inspect the upstream releases

Note:

- **Tag format**: `v1.2.3`, `1.2.3`, `release-1.2.3`, `cli-v1.2.3`…
- **Asset names** for each platform/architecture you need, and whether they contain the version.
- **Checksums**: a per-asset sidecar (`tool.zip.sha256`), a shared file (`SHA256SUMS`, `checksums.txt`), or none.

!!! tip "See exact names"
    ```sh
    # GitHub
    curl -s https://api.github.com/repos/OWNER/REPO/releases/latest | jq -r '.tag_name, .assets[].name'
    # GitLab
    curl -s "https://gitlab.com/api/v4/projects/GROUP%2FPROJECT/releases/permalink/latest" | jq -r '.tag_name, .assets.links[].name'
    ```

## 2. Write `tools/<name>/tool.yaml`

The directory name is the tool name: lowercase letters, digits and `-`.

=== "GitHub-hosted"

    ```yaml
    # yaml-language-server: $schema=../../schema/tool.schema.json
    name: pact-broker-cli
    version: "0.8.6"            # current version, quoted, without tag prefix
    tag: v{version}             # upstream tags look like v0.8.6

    source:
      provider: github
      repository: pact-foundation/pact-broker-cli

    checksum:
      type: file
      file: "{asset}.sha256"    # per-asset sidecar files

    assets:
      windows-amd64:
        name: pact-broker-cli-x86_64-pc-windows-msvc.zip
        destination: pact-broker-cli-x86_64-pc-windows-msvc.zip
    ```

=== "GitLab-hosted, several assets"

    ```yaml
    name: glab
    version: "1.119.0"
    tag: v{version}

    source:
      provider: gitlab
      repository: gitlab-org/cli
      # host: https://gitlab.example.com   # self-managed GitLab

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

=== "GitHub Enterprise, pinned major"

    ```yaml
    name: internal-cli
    version: "3.2.0"
    tag: internal-cli-v{version}
    ignore: [">=4.0.0"]          # stay on 3.x

    source:
      provider: github
      repository: platform/internal-cli
      host: https://github.example.com

    checksum:
      type: provider             # GitHub's own per-asset digest

    assets:
      linux-amd64:
        name: internal-cli-linux-amd64
        destination: internal-cli
    ```

All fields are described in the [tool.yaml reference](../reference/tool-yaml.md). For more real definitions (Pact CLIs, monorepos, scanners) and a step-by-step walkthrough, see [Examples](examples.md).

## 3. Fetch the artifacts and create the lock file

```sh
toolbot validate
toolbot update <name>       # downloads + verifies the declared version, writes tool.lock.json
toolbot verify
```

## 4. Commit and open a merge request

Commit `tool.yaml`, `tool.lock.json` and the artifacts. The `toolbot-verify` job checks them. From the next scheduled run, Toolbot keeps the tool up to date.

## Checklist

- [ ] `version` is quoted and has no tag prefix.
- [ ] `tag` reproduces the upstream tag for that version exactly.
- [ ] Asset `name`s match exactly. Prefer `name` over `pattern`.
- [ ] Checksum verification is configured when upstream publishes checksums.
- [ ] Destinations are stable (no version in the file name), so updates show as modifications.
- [ ] Private upstream? The right read-only token is available ([Tokens & permissions](../deploy/tokens.md)).

## Removing or pausing a tool

- **Remove:** delete `tools/<name>/`. Open Toolbot MRs for it can be closed by hand.
- **Pause:** add `ignore: [">=0"]` (ignore every version), or ignore specific versions. See [Versions](versions.md).
