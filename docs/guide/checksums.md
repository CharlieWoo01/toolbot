# Checksums

Set `checksum` at tool level (it applies to every asset) or per asset (overrides).

| `type` | Verifies against | Use when |
| --- | --- | --- |
| `file` | A checksum file attached to the **same release** | The project publishes checksums **(preferred)** |
| `provider` | The digest reported by the provider API (GitHub asset `digest`) | GitHub projects without checksum files |
| `none` | Nothing (a warning is logged and shown in the MR) | No other option |

## `type: file`

```yaml
checksum:
  type: file
  file: "{asset}.sha256"     # or SHA256SUMS, checksums.txt, {tag}-checksums.txt …
  algorithm: sha256          # default; sha512 also supported
```

`file` names a release asset and may use `{asset}`, `{version}` and `{tag}`. Supported formats:

| Format | Example |
| --- | --- |
| Bare digest | `bb639caa…c40` |
| `sha256sum` text mode | `bb639caa…c40  tool.zip` |
| `sha256sum` binary mode | `bb639caa…c40 *tool.zip` |
| BSD style | `SHA256 (tool.zip) = bb639caa…c40` |

Entries must match the asset name exactly (a leading `./` is ignored).

## Failures are never ignored

If a tool is configured to verify and verification can't be performed, the update **fails**. That covers a missing checksum file, an asset missing from it, conflicting entries, no provider digest, or a mismatch. The download is discarded; committed files, lock file and definition stay byte-for-byte unchanged; no branch or merge request is created; other tools carry on.

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
| Pact Broker CLI | `<asset>.sha256` sidecar (`<hex> *<name>`), plus a `sha256.sum` that omits the raw executables | `type: file`, `file: "{asset}.sha256"` |
| glab (GitLab CLI) | `checksums.txt` covering all assets | `type: file`, `file: checksums.txt` |
| Any GitHub release | API digest per asset | `type: provider` |
