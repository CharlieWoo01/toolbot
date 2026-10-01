# Example control repository

A **control repository** (GitOps) holds Toolbot's configuration for many repositories in one reviewed place. It contains no tools itself:

```text
toolbot-control/
├── toolbot.yaml                 hosts, rules, target sets
├── .gitlab-ci.yml               GitLab: one pipeline schedule per target set
└── .github/workflows/toolbot.yml   GitHub: the same, with cron entries
```

- **`toolbot.yaml`** declares the platforms (public and internal) with the environment variable holding each token, central version rules, and **target sets**: named lists of repositories and whole GitLab groups / GitHub organisations.
- Each **schedule** runs one set: `toolbot check --targets nightly`, `--targets weekly`, …
- Each **target repository** keeps its own `tools/<name>/tool.yaml` and binaries, and receives merge/pull requests. It needs no Toolbot schedule of its own, only the verify job on merge requests.

Try it without changing anything:

```sh
toolbot --config toolbot.yaml validate
toolbot --config toolbot.yaml targets --targets all          # which repositories each set covers
toolbot --config toolbot.yaml check --targets nightly --dry-run
```

Full guide: [docs/deploy/control-repository.md](../../docs/deploy/control-repository.md).
