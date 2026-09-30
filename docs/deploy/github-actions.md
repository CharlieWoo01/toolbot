# GitHub Actions

The Toolbot project itself can live on GitHub. These workflows test it, build and publish the image to GHCR, publish these docs to GitHub Pages, and cut releases.

!!! note "Targets"
    Toolbot's MVP target is GitLab. From GitHub Actions you can run `toolbot check` against a **GitLab** project (set `TOOLBOT_GITLAB_URL`, `TOOLBOT_GITLAB_PROJECT` and `TOOLBOT_GITLAB_TOKEN`). Pull requests on GitHub need a `GitHubTargetProvider`, which isn't implemented yet ([Providers](../guide/providers.md#github-future)).

## Workflows

| Workflow | Trigger | What it does |
| --- | --- | --- |
| `ci.yml` | pull requests, `main` | ruff, mypy, pytest, `toolbot validate`/`verify`, `mkdocs build --strict`, actionlint + shellcheck |
| `image.yml` → `image-build.yml` | pull requests, `main` | Build, `scripts/smoke-test-image.sh`, validate this repo's tools with the image. On `main`: push `ghcr.io/<owner>/toolbot:sha-<commit>` (amd64 + arm64) with a provenance attestation |
| `release.yml` | tag `vX.Y.Z` | Check tag = `pyproject.toml` version, run tests, push `ghcr.io/<owner>/toolbot:X.Y.Z`, create a GitHub Release with the digest |
| `docs.yml` | `main` (docs changes) | Build and deploy to GitHub Pages |
| `integration.yml` | weekly, manual | Live download tests against GitHub and GitLab |

All actions are pinned to full commit SHAs.

## One-time setup

1. **Pages:** *Settings → Pages → Source: GitHub Actions*.
2. **Package visibility:** after the first `main` build, set the `toolbot` package's visibility (*Packages → toolbot → Settings*) so other projects can pull it, or grant specific repositories read access.
3. **Branch protection:** require the *CI / Test* and *Image* checks on `main`.

## Releasing

```sh
# bump [project].version in pyproject.toml, commit, push, then:
git tag v0.2.0 && git push origin v0.2.0
gh attestation verify oci://ghcr.io/<owner>/toolbot:0.2.0 --repo <owner>/toolbot
```

## Running Toolbot from GitHub Actions against GitLab

```yaml
name: toolbot
on:
  schedule: [{ cron: "0 6 * * 1-5" }]
  workflow_dispatch:
jobs:
  check:
    runs-on: ubuntu-24.04
    concurrency: toolbot
    steps:
      - run: >
          docker run --rm
          -e TOOLBOT_GITLAB_URL=https://gitlab.example.com
          -e TOOLBOT_GITLAB_PROJECT=team/ci-tools
          -e TOOLBOT_GITLAB_TOKEN -e TOOLBOT_GITHUB_TOKEN
          ghcr.io/<owner>/toolbot:0.1.0 toolbot check
        env:
          TOOLBOT_GITLAB_TOKEN: ${{ secrets.TOOLBOT_GITLAB_TOKEN }}
          TOOLBOT_GITHUB_TOKEN: ${{ github.token }}
```
