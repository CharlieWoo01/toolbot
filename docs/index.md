# Toolbot

**Toolbot automates updates for standalone development and CI tooling.** It monitors upstream releases, downloads and verifies configured artifacts, and raises automated update requests when newer versions are available.

Think *Dependabot for binaries*: Pact Broker CLI, Terraform, Helm, kubectl, security scanners, vendor CLIs, standalone executables, ZIP/TAR archives and JARs. These are tools distributed through release platforms rather than package managers.

```text
chore(tools): update pact-broker-cli to 0.8.8

  tools/pact-broker-cli/tool.yaml                                   version: "0.8.8"
  tools/pact-broker-cli/tool.lock.json                              tag, upstream asset, sha256
  tools/pact-broker-cli/pact-broker-cli-x86_64-pc-windows-msvc.zip  verified binary
```

<div class="grid cards" markdown>

- **Configuration, not scripts:** adding a tool is one `tools/<name>/tool.yaml`.
- **GitHub and GitLab sources:** github.com, GitHub Enterprise, gitlab.com or self-managed GitLab.
- **Safe updates:** checksum verification, all-or-nothing installs, deterministic asset matching.
- **Well-behaved automation:** one merge request per tool update, no duplicates, rejected versions stay rejected.
- **Dry run:** `toolbot check --dry-run` shows exactly what would happen.
- **Standalone:** a small Python CLI plus `git`, runnable from GitLab CI, a container or your laptop.

</div>

## The definition behind that update

```yaml
name: pact-broker-cli
version: "0.8.6"
tag: v{version}

source:
  provider: github
  repository: pact-foundation/pact-broker-cli

checksum:
  type: file
  file: "{asset}.sha256"

assets:
  windows-amd64:
    name: pact-broker-cli-x86_64-pc-windows-msvc.zip
    destination: pact-broker-cli-x86_64-pc-windows-msvc.zip
```

## Where next

- [Quick start](guide/quick-start.md): run it locally in five minutes.
- [GitLab CI](deploy/gitlab.md): scheduled update merge requests at work.
- [Update lifecycle](guide/lifecycle.md): how Toolbot avoids duplicate and unwanted merge requests.
