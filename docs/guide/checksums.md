# Checksums

## What is `checksums.txt`?

When a project publishes a release, its build also publishes a small text file listing the **SHA-256 fingerprint** of every file it uploaded. glab calls it `checksums.txt`, HashiCorp `terraform_1.16.4_SHA256SUMS`, others `SHA256SUMS` or one `<file>.sha256` per file:

```text
4e6c59de9f7ed2f304bf93aad01ea8f8a69584f0450ce90ad696ef81f69c69aa  glab_1.120.0_linux_amd64.tar.gz
4240365e23ac7cb5ce09aa0f5892cd66b0da103963fe01954c8d64727039a3f6  glab_1.120.0_windows_amd64.zip
```

A SHA-256 is a 64-character fingerprint of a file's exact bytes: change one byte and it's completely different. Toolbot downloads the file, computes its fingerprint, and compares it with the line for that file name. If they differ, the download was corrupted, truncated, swapped by a proxy, or isn't the file the project published, and the update **fails without touching your repository**.

Toolbot never creates these files; it reads the ones upstream publishes. You tell it where to find them with `checksum`.

## Configuring verification

Set `checksum` at tool level (it applies to every asset) or per asset (overrides).

| `type` | Verifies against | Use when |
| --- | --- | --- |
| `file` | A checksum file published by the project **(preferred)** | The project publishes checksums |
| `provider` | The digest the GitHub API reports for a release asset | GitHub release assets without checksum files |
| `none` | Nothing (a warning is logged and shown in the MR) | No other option |

## `type: file`

The checksum file can come from three places. Use exactly one:

```yaml
checksum: { type: file, file: "{asset}.sha256" }   # a release asset (sidecar per file)
checksum: { type: file, file: checksums.txt }      # a release asset (one file for all)
checksum: { type: file, url: "https://releases.hashicorp.com/terraform/{version}/terraform_{version}_SHA256SUMS" }
checksum: { type: file, path: dist/SHA256SUMS }    # committed in the source repository at the tag
```

All three may use `{asset}` (the downloaded file's name), `{version}` and `{tag}`. Add `algorithm: sha512` for SHA-512 files. Supported formats:

| Format | Example |
| --- | --- |
| Bare digest | `bb639caa…c40` |
| `sha256sum` text mode | `bb639caa…c40  tool.zip` |
| `sha256sum` binary mode | `bb639caa…c40 *tool.zip` |
| BSD style | `SHA256 (tool.zip) = bb639caa…c40` |

Entries must match the file name exactly (a leading `./` is ignored), so `tool.zip` never matches `tool.zip.sig`.

### Archives

With [`extract`](what-can-be-updated.md#archives-commit-the-binary-not-the-zip), the **archive** is verified (that's what upstream checksums list) before anything is extracted. The lock file then records the SHA-256 of the extracted file you commit, which is what `toolbot verify` re-checks.

## Failures are never ignored

If a tool is configured to verify and verification can't be performed, the update **fails**. That covers a missing checksum file, a file missing from it, conflicting entries, no provider digest, or a mismatch. The download is discarded; committed files, lock file and definition stay byte-for-byte unchanged; no branch or merge request is created; other tools carry on.

## Verification in merge requests

Each MR lists every asset with how it was verified:

| Asset | Upstream file | Size | SHA-256 | Verification |
| --- | --- | --- | --- | --- |
| `windows-amd64` | `pact-broker-cli-x86_64-pc-windows-msvc.zip` | 3,188,345 B | `ebd944d5fd2581f7…` | ✅ SHA256 (`pact-broker-cli-x86_64-pc-windows-msvc.zip.sha256`) |

`tool.lock.json` records the same, and `toolbot verify` re-hashes committed files in every pipeline.

## What checksums protect against, and what they don't

A checksum from the same release protects against corrupted or truncated downloads, tampering by proxies or CDNs, and fetching the wrong file. It does **not** protect against a compromised upstream account that uploads a malicious binary *and* a matching checksum. Toolbot's defences there are human review of every MR (with the upstream name and hash) and no automerge. Signature verification is on the roadmap.

## Real examples

| Tool | Upstream publishes | Config |
| --- | --- | --- |
| Pact Broker CLI | `<asset>.sha256` sidecar (`<hex> *<name>`) | `file: "{asset}.sha256"` |
| glab (GitLab CLI) | `checksums.txt` covering all assets | `file: checksums.txt` |
| gitleaks | `gitleaks_<version>_checksums.txt` | `file: "gitleaks_{version}_checksums.txt"` |
| Terraform | `SHA256SUMS` on releases.hashicorp.com | `url: …/terraform_{version}_SHA256SUMS` |
| Helm | `<file>.sha256sum` on get.helm.sh | `url: "https://get.helm.sh/{asset}.sha256sum"` |
| kubectl | bare digest at `<file URL>.sha256` | per asset `url: …/kubectl.sha256` |
| Any GitHub release | API digest per asset | `type: provider` |
