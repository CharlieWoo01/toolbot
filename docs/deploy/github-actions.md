# GitHub Actions

There are two separate things here:

1. **Using Toolbot in GitHub repositories**: pull requests for tools, like the GitLab template does with merge requests.
2. **Hosting the Toolbot project itself on GitHub**: its CI, image builds and docs.

## Using Toolbot in a GitHub repository

The Toolbot repository is itself a GitHub Action ([`action.yml`](https://github.com/your-org/toolbot/blob/main/action.yml)). It installs Toolbot with pinned dependencies and runs `toolbot <args>`. Copy [`templates/github/toolbot.yml`](https://github.com/your-org/toolbot/blob/main/templates/github/toolbot.yml) to `.github/workflows/toolbot.yml`:

```yaml
jobs:
  verify:                               # pull requests touching tools/
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@<sha> # v7
        with: { persist-credentials: false }
      - uses: your-org/toolbot@v0.1.0
        with: { args: validate }
      - uses: your-org/toolbot@v0.1.0
        with: { args: verify }

  check:                                # schedule / manual run
    if: github.event_name != 'pull_request'
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@<sha> # v7
        with: { persist-credentials: false }
      - id: app
        uses: actions/create-github-app-token@<sha> # v3
        with:
          app-id: ${{ vars.TOOLBOT_APP_ID }}
          private-key: ${{ secrets.TOOLBOT_APP_PRIVATE_KEY }}
      - uses: your-org/toolbot@v0.1.0
        with: { args: check }
        env:
          TOOLBOT_GITHUB_TOKEN: ${{ steps.app.outputs.token }}
```

The repository the workflow runs in is the target (from `GITHUB_REPOSITORY`). Toolbot opens **pull requests** from `toolbot/<tool>/<version>` branches, with the same lifecycle as on GitLab: one active PR per tool, closed PRs are rejections, and human commits are never overwritten.

### Do I need to publish the Action?

**No.** `uses: your-org/toolbot@v0.1.0` works for any repository that can read the Toolbot repository at that tag. The Marketplace is only a catalogue, and only lists public repositories.

| Toolbot repository is… | What consumers need |
| --- | --- |
| Public | Nothing |
| Internal (GitHub Enterprise) | *Settings → Actions → General → Access*: "Accessible from repositories in the organization" |
| Private | The same setting, for repositories owned by the same user/organisation |

Pin to a release tag (or a full commit SHA) and bump it deliberately, like the GitLab template `ref`.

### Which token?

Pull requests created with the built-in `GITHUB_TOKEN` **don't trigger workflows**, so the `verify` job would never run on Toolbot's PRs. Use a **GitHub App** (recommended): Contents, Pull requests and Issues (for labels) read & write, installed on the repositories. Pass its installation token as `TOOLBOT_GITHUB_TOKEN`. A fine-grained PAT of a bot account also works.

### Many repositories, schedules

To maintain many repositories (whole organisations) from one place on one or more schedules, use a [control repository](control-repository.md).

## Hosting the Toolbot project on GitHub

These workflows test Toolbot, build and publish the image to GHCR, publish these docs to GitHub Pages, and cut releases.

| Workflow | Trigger | What it does |
| --- | --- | --- |
| `ci.yml` | pull requests, `main` | ruff, mypy, pytest, `toolbot validate`/`verify`, `mkdocs build --strict`, actionlint + shellcheck |
| `image.yml` → `image-build.yml` | pull requests, `main` | Build, `scripts/smoke-test-image.sh`, validate this repo's tools with the image. On `main`: push `ghcr.io/<owner>/toolbot:sha-<commit>` (amd64 + arm64) with a provenance attestation |
| `release.yml` | tag `vX.Y.Z` | Check tag = `pyproject.toml` version, run tests, push `ghcr.io/<owner>/toolbot:X.Y.Z`, create a GitHub Release with the digest |
| `docs.yml` | `main` (docs changes) | Build and deploy to GitHub Pages |
| `integration.yml` | weekly, manual | Live download tests against GitHub and GitLab |

All actions are pinned to full commit SHAs.

### One-time setup

1. **Pages:** *Settings → Pages → Source: GitHub Actions*.
2. **Package visibility:** after the first `main` build, set the `toolbot` package's visibility (*Packages → toolbot → Settings*) so other projects can pull it, or grant specific repositories read access.
3. **Branch protection:** require the *CI / Test* and *Image* checks on `main`.
4. **Action access** (private/internal repository): *Settings → Actions → General → Access*.

### Releasing

```sh
# bump [project].version in pyproject.toml, commit, push, then:
git tag v0.2.0 && git push origin v0.2.0
gh attestation verify oci://ghcr.io/<owner>/toolbot:0.2.0 --repo <owner>/toolbot
```

The tag is also the version consumers reference in `uses: your-org/toolbot@v0.2.0`.

## Running Toolbot from GitHub Actions against GitLab

Set the target explicitly; the GitLab project receives merge requests:

```yaml
- uses: your-org/toolbot@v0.1.0
  with: { args: check }
  env:
    TOOLBOT_TARGET: gitlab
    TOOLBOT_GITLAB_URL: https://gitlab.example.com
    TOOLBOT_GITLAB_PROJECT: team/ci-tools
    TOOLBOT_GITLAB_TOKEN: ${{ secrets.TOOLBOT_GITLAB_TOKEN }}
    TOOLBOT_GITHUB_TOKEN: ${{ github.token }}   # read-only, avoids GitHub rate limits
```
