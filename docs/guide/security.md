# Security model

Toolbot runs unattended with push access to a repository and downloads executables from the internet. Tool definitions, upstream metadata and downloaded bytes are all treated as untrusted.

| Threat | Mitigation |
| --- | --- |
| Command injection | No shell anywhere. Git runs via `subprocess` with argument lists. Tool names, versions and tags are validated to safe characters, and branch names are checked with `git check-ref-format`. |
| Path traversal / writes outside the repo | Destinations are relative to `tools/<name>/` and whitelisted per segment: no `..`, absolute paths, drive letters, backslashes or dot-files. `tool.yaml` and `tool.lock.json` are protected. Real paths are checked before creating directories, so symlinks can't redirect writes. |
| Malicious upstream file names | Upstream names are only compared with configuration, never used as paths. |
| Wrong artifact | Exact names by default. Patterns must match exactly one asset. |
| Tampered or corrupt downloads | Checksum verification before install, a size cap (1 GiB by default), truncation detection. |
| Half-applied updates | Download and verify everything in a private temp directory, then replace files atomically with rollback. |
| Token leakage | Tokens only come from environment variables and are sent only to their provider's origin; they're dropped on cross-origin redirects. Git receives the push token through environment-scoped config, never argv or URLs. Error output is redacted. httpx request logging is silenced (signed CDN URLs never reach logs). |
| Insecure transport | HTTPS required for every request and redirect. URLs with embedded credentials are refused. |
| YAML tricks | Safe loader only; duplicate keys and aliases rejected. |
| Upstream text in MRs | Release-note excerpts are fenced and truncated, so they can't @-mention users or reference local issues. |
| Destroying human work | Branches are only rewritten or deleted if their head commit is Toolbot's, and only with a lease on the exact expected commit. |
| Concurrent runs | `resource_group` plus leased pushes; duplicate MRs are rejected by GitLab and treated as existing. |
| Unwanted pushes to other branches | Toolbot only pushes `toolbot/<tool>/<version>` branches. Protect your default branch as usual. |
| Compromised upstream release | Human review with the lock-file diff, no automerge. Signature verification is future work. |

## Hardening checklist

- [ ] Pin `TOOLBOT_IMAGE` to a released version (or digest).
- [ ] Store tokens as **masked + protected** CI/CD variables, and run the schedule on a protected branch.
- [ ] Give the target token the minimum: a project or group access token, role **Developer**, scope `api`.
- [ ] Use read-only tokens for sources (`TOOLBOT_GITHUB_TOKEN`: public repositories read-only).
- [ ] Enable **Pipelines must succeed** so `toolbot-verify` gates Toolbot MRs.
- [ ] Protect `tools/` with CODEOWNERS if tool changes need specific approval.
