# To do

Sizes: **S** ≈ a day, **M** a few days, **L** a week or more.

Suggested order: 5 (finish the trial) → 0 + 1 (scanning foundation with GitHub Actions as the first ecosystem) → 3 (definitions anywhere) → 2a/2b CLI versions and modules → 2b providers and lock-file hashes.

Note: Dependabot and Renovate already update Actions and Terraform. Toolbot's angle is one consistent lifecycle with verified, reviewable updates, and GitLab (including self-managed) where Dependabot doesn't run. Decide whether Toolbot replaces them or covers the gaps.

## 0. Foundation: dependencies found by scanning files

Today an update is driven by a hand-written `tools/<name>/tool.yaml`. Actions and Terraform need Toolbot to *find* dependencies where they already are.

- [ ] **M** `Dependency` model: file, exact text span, current value/ref, upstream (owner/repo or registry address), versioning, how to write the new value
- [ ] **M** Ecosystem plug-in interface (`github-actions`, `terraform`, …): scan files → dependencies; version discovery reuses the GitHub/GitLab sources (incl. `versions: tags`)
- [ ] **S** Span-based edits: replace only the located text, refuse if the file changed since scanning (generalises `version-files`; comments and formatting survive)
- [ ] **M** Grouping: one PR per dependency, or grouped ("all GitHub Actions", "all Terraform providers"); branches `toolbot/<ecosystem>/<name>/<version>`, trailer `Toolbot-Dependency`; same supersede/reject/human-commit lifecycle
- [ ] **S** `toolbot.yaml`: enable ecosystems, include/exclude paths, rules matching `dependencies:` globs as well as `tools:`
- [ ] **S** `outdated` and `check --dry-run` list scanned dependencies alongside tools

## 1. GitHub Actions updates

- [ ] **M** Scan `.github/workflows/*.yml` and `action.yml`/`action.yaml` for `uses:`: `owner/repo@ref`, `owner/repo/subpath@ref`, reusable workflows `owner/repo/.github/workflows/x.yml@ref`; skip `./local` and `docker://`
- [ ] **M** Keep each reference's style:
  - [ ] `@v4` (major only): bump the major (`v4` → `v5`)
  - [ ] `@v4.1.2`: latest allowed by rules
  - [ ] `@<sha> # v4.1.2`: resolve the new tag to its commit (peel annotated tags), update SHA and comment together
- [ ] **S** Option `pin: sha` to convert floating tags to SHAs (protects against moved tags, cf. tj-actions)
- [ ] **S** Safety: tag's commit must be reachable upstream; show the SHA in the PR; warn if a tag moved since last seen
- [ ] **S** Token gotcha: changing `.github/workflows/` needs **Workflows: read & write** (PAT/App), and `GITHUB_TOKEN` can't push it. Detect the rejected push and explain; update `docs/deploy/creating-tokens.md`
- [ ] **S** Release notes / compare link between old and new tag in the PR
- [ ] **S** Example repo in `examples/` and a docs page
- [ ] **M** (later) GitLab equivalent: `include: project … ref:` and CI/CD components `component: host/group/comp@1.2.3` in `.gitlab-ci.yml`

## 2. Terraform (anywhere in the repo)

### 2a. Parsing and layout

- [ ] **M** Parse HCL (e.g. `python-hcl2`) to *locate* dependencies; edit by span, never re-render
- [ ] **S** Discover root modules (directories with `.tf`, each with its own `.terraform.lock.hcl`); exclude `.terraform/`, `examples/` etc.

### 2b. What gets updated

- [ ] **S** CLI versions: `required_version` in `terraform {}`, `.terraform-version`, `.opentofu-version`, `.tool-versions` (automatic instead of hand-written `version-files`)
- [ ] **L** Providers (`required_providers { aws = { source = "hashicorp/aws", version = "~> 5.0" } }`)
  - [ ] New source: Terraform Registry API (`/v1/providers/<ns>/<name>/versions`), OpenTofu registry, private registries via `/.well-known/terraform.json`; Terraform Cloud/Enterprise auth via a `token-env` host
  - [ ] Constraint strategy per rule: bump (`~> 5.0` → `~> 6.0`), widen, or pin; never exclude the locked version
- [ ] **L** `.terraform.lock.hcl` updated with every provider change (otherwise `terraform init` fails on the branch)
  - [ ] Preferred: compute natively: `zh:` from the registry SHA256SUMS, `h1:` by hashing each configured platform's zip (no external binaries)
  - [ ] Alternative: `terraform providers lock -platform=…` in the image
- [ ] **M** Modules: registry modules (`source` + `version`) and git sources (`git::https://…//vpc?ref=v1.2.0`, `github.com/org/repo?ref=`) via the existing tag sources and hosts
- [ ] **M** Group a provider bump across all root modules into one PR, updating each lock file
- [ ] **M** (later) Terragrunt: `terragrunt.hcl` `source = "…?ref=…"`, `.terragrunt-version`
- [ ] **S** Docs: optional `terraform validate` job in CI (Toolbot itself doesn't run Terraform)

## 3. Tool definitions anywhere in the repo

- [ ] **M** `tool-paths` in `toolbot.yaml` (default `tools/*/tool.yaml`, e.g. `["**/tool.yaml"]`); the definition's directory is its base; names unique repo-wide (branch identity)
- [ ] **S** Remove hard-coded `tools/`: commit paths (`ApplyResult.paths`), root-destination guard, duplicate-root-file check, CI template `exists:` rule, GitHub template `paths:`, docs
- [ ] **M** Group scanning: `has_tools` checks `tools/` today; detect `toolbot.yaml` or configured ecosystem files instead (repo tree API, bounded)
- [ ] **S** Optional inline definitions in `toolbot.yaml` (no file per tool)

## 4. More ecosystems (same plug-in interface)

- [ ] **M** Docker images: `FROM`, `image:` in CI/Kubernetes/Compose; tag + digest pinning
- [ ] **S** `.tool-versions` / `mise.toml`
- [ ] **S** pre-commit hooks (`rev:` in `.pre-commit-config.yaml`)
- [ ] **M** Helm chart dependencies (`Chart.yaml`, `Chart.lock`)

## 5. Follow-ups from what's built

- [ ] **S** Windows exec bit: a manual commit after `toolbot update` on Windows loses `+x`. Add a `verify` check of git mode vs `executable` and a `toolbot fix-modes` (`git update-index --chmod=+x`)
- [ ] **S** Trial in `CharlieWoo01/toolbot-test`: add `TOOLBOT_GITHUB_TOKEN`, run the workflow, confirm Git LFS upload with a token (only tested against a local remote), PRs, labels, verify job
- [ ] **S** Tag `v0.1.0` once the trial passes (runs the release workflow); switch `toolbot-test` from the commit SHA to `@v0.1.0`
- [ ] **S** Enable GitHub Pages for the docs; run actionlint on the workflows
- [ ] **M** Live-test group scanning against a real GitLab group / GitHub organisation
- [ ] **M** Signature verification (cosign / GPG / GitHub attestations) as a stronger `checksum` type
- [ ] **S** `toolbot init TOOL --from <release URL>` to scaffold a `tool.yaml`
