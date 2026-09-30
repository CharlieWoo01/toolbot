# Example tool definitions

Real, working definitions for tools you may want Toolbot to manage. Each file is commented to explain what it demonstrates. They live here, not in `tools/`, so this repository doesn't download all of them on every run.

| Example | Source | Demonstrates |
| --- | --- | --- |
| [`pact-cli`](tools/pact-cli/tool.yaml) | GitHub | The common case: fixed asset names, per-file `.sha256` sidecars, two platforms |
| [`pact-standalone`](tools/pact-standalone/tool.yaml) | GitHub | `{version}` in asset names; `checksum.type: provider` when upstream only publishes SHA-1 |
| [`pact-stub-server`](tools/pact-stub-server/tool.yaml) | GitHub | Gzipped single executables |
| [`pact-verifier-cli`](tools/pact-verifier-cli/tool.yaml) | GitHub | **Monorepo**: `tag: pact_verifier_cli-v{version}` picks one component's releases |
| [`pact-plugin-cli`](tools/pact-plugin-cli/tool.yaml) | GitHub | Monorepo with a hyphenated prefix: `tag: pact-plugin-cli-v{version}` |
| [`gitleaks`](tools/gitleaks/tool.yaml) | GitHub | Shared checksum file with the version in its name |
| [`trivy`](tools/trivy/tool.yaml) | GitHub | `ignore` to hold back a major version; picking two files out of a busy release |
| [`glab`](tools/glab/tool.yaml) | **GitLab** | A tool released on GitLab.com instead of GitHub |

The tool this repository actually tracks, `pact-broker-cli`, is in [`../tools/`](../tools/pact-broker-cli/tool.yaml).

## Try them without changing anything

```sh
toolbot --root examples validate
toolbot --root examples outdated
toolbot --root examples check --dry-run
```

## Use one

```sh
mkdir -p tools
cp -r examples/tools/pact-cli tools/
toolbot update pact-cli          # fetch + verify the version in tool.yaml, write tool.lock.json
toolbot verify
git add tools/pact-cli && git commit -m "Track pact-cli with Toolbot"
```

Adjust the `assets` to the platforms you need. From then on `toolbot check` proposes updates as merge requests.

More explanation: [docs/guide/examples.md](../docs/guide/examples.md).
