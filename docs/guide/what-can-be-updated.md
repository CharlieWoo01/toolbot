# What can be updated

Every tool answers two questions: **where do versions come from**, and **what changes in my repository** when there's a new one. Pick one row from each table.

## Where versions come from

| Upstream publishes… | `source` | Example |
| --- | --- | --- |
| GitHub / GitLab **releases** | `provider` + `repository` (default `versions: releases`) | Pact CLIs, glab, Terraform (versions only) |
| Only **git tags**, no releases | add `versions: tags` | internal tools that just tag `v1.4.0` |
| On an internal / Enterprise instance | add `host:` (a [host](hosts.md) name or URL) | `host: internal-gitlab` |

Tags must match the `tag` template (`v{version}`, `cli-v{version}` …); everything else (other components' tags, `latest`, nightlies) is ignored. See [Versions](versions.md).

## What changes in your repository

| You want to commit… | Asset fields | Typical tools |
| --- | --- | --- |
| A file attached to the release | `name` or `pattern` | Pact CLIs, gitleaks, trivy |
| A file from a download site | `url` | kubectl |
| **The binary inside a ZIP / tar.gz** | `url`/`name` + **`extract`** | Terraform, Helm, glab, trivy |
| A file **committed in the upstream repository** | `path` | internal CLIs whose builds are checked in |
| Nothing: just bump **version pins** | `version-files` (no `assets`) | `.terraform-version`, Dockerfile `ARG`, CI variables |
| Binaries **and** version pins | `assets` + `version-files` | Terraform binary plus `.terraform-version` |

Files go to `tools/<name>/…` by default, or anywhere in the repository with a leading `/` (`destination: /bin/terraform`). Each update is one merge request with every change for that tool and version.

## Archives: commit the binary, not the ZIP

```yaml
name: terraform
version: "1.16.3"
tag: v{version}
source: { provider: github, repository: hashicorp/terraform }
checksum:
  type: file
  url: https://releases.hashicorp.com/terraform/{version}/terraform_{version}_SHA256SUMS
assets:
  linux-amd64:
    url: https://releases.hashicorp.com/terraform/{version}/terraform_{version}_linux_amd64.zip
    extract: terraform                 # the file inside the ZIP
    destination: /bin/terraform        # repository root
  windows-amd64:
    url: https://releases.hashicorp.com/terraform/{version}/terraform_{version}_windows_amd64.zip
    extract: terraform.exe
    destination: /bin/terraform.exe
```

1. The ZIP is downloaded to a private temp folder and its SHA-256 checked against `SHA256SUMS` (upstream checksums cover the archive).
2. Only `terraform` is read from it and written to a temp file. Paths inside the archive never become local paths, so `../` entries, absolute paths, symlinks and device files can't write anywhere. Symlink members are refused, and the size limit (`TOOLBOT_MAX_DOWNLOAD_MB`) applies to the extracted file too.
3. `/bin/terraform` is replaced atomically and committed with the executable bit (through Git LFS, as it's over 50 MB: see [Large binaries](#large-binaries-terraform-is-120-mb)). `tool.lock.json` records `…linux_amd64.zip!terraform` and the SHA-256 of the committed binary.

zip, tar, tar.gz/.tgz, tar.xz and tar.bz2 are detected from the content. If the member isn't found, the error lists the files in the archive. Several assets may extract different files from the same archive; it's downloaded once.

Prefer committing archives as-is? Leave out `extract`.

## Large binaries (Terraform is ~120 MB)

GitHub rejects files over 100 MB, warns from 50 MB, and every platform limits push sizes. Toolbot handles this automatically. Each asset has a `storage`, default `auto`:

| `storage` | What's committed | Getting the file |
| --- | --- | --- |
| `auto` *(default)* | Files up to `defaults.max-file-mb` (50) as normal blobs; bigger ones as `defaults.large-files` | |
| `git` | The file | `git clone` |
| `lfs` | A Git LFS pointer; the file goes to the platform's LFS storage | `git clone` with git-lfs (GitLab CI does this by default; GitHub: `actions/checkout` with `lfs: true`) |
| `fetch` | Nothing; the file is git-ignored and pinned in `tool.lock.json` | `toolbot fetch` |

```yaml
# toolbot.yaml (optional; these are the defaults)
defaults:
  max-file-mb: 50
  large-files: lfs        # or fetch
```

**Git LFS** (`lfs`): Toolbot adds `/terraform filter=lfs diff=lfs merge=lfs -text` to the `.gitattributes` next to the file, commits the pointer, and uploads the object itself before pushing the branch. The LFS object id is the file's SHA-256, the same value recorded in `tool.lock.json` and shown in the MR. Nobody needs to run `git lfs track`. The Toolbot image includes git-lfs. LFS must be enabled on the project (it is by default on GitHub and GitLab), and LFS storage counts towards quotas: GitHub's free tier is small, so for big tools updated often, `fetch` may suit better.

**Fetch** (`fetch`): the file is never committed. Toolbot adds `/terraform` to the `.gitignore` next to it, and the lock file pins its SHA-256. Anything that needs the binary runs:

```sh
toolbot fetch            # every tool; or: toolbot fetch terraform
```

which downloads it exactly as the update did (same release, checksum file, extraction) and then requires the SHA-256 from `tool.lock.json`, so the file is byte-for-byte the one reviewed in the merge request. Files already present with the right hash aren't downloaded again. In CI, run it as the first step of jobs that use the tool.

Switching between modes, for example when a tool grows past the limit, is handled in the same update. Toolbot moves the file in or out of LFS or the index and adds or removes its own `.gitattributes`/`.gitignore` lines, never anyone else's.

## Files committed in a repository

Some internal tools are built and checked in rather than released:

```yaml
name: deploy-cli
version: "4.2.0"
tag: v{version}
source:
  host: internal-gitlab
  repository: platform/deploy-cli
  versions: tags                       # the project tags, but has no releases
checksum:
  type: file
  path: dist/SHA256SUMS                # also committed upstream
assets:
  linux-amd64:
    path: dist/linux-amd64/deploy-cli  # read at tag v4.2.0
    destination: /bin/deploy-cli
```

Files are fetched through the platform API at the release tag (GitHub contents API, up to 100 MB; GitLab repository files API, with Git LFS content resolved). A Git LFS *pointer* is never committed by mistake: if GitHub returns one, the update fails with a hint to use `url` instead.

## Version pins only

Many repositories don't commit binaries at all; they pin a version that something else installs:

```yaml
name: terraform-pin
version: "1.16.3"
tag: v{version}
source: { provider: github, repository: hashicorp/terraform }
version-files:
  - path: /.terraform-version                  # tfenv / tfswitch
    template: "{version}"
  - path: /Dockerfile
    template: "ARG TERRAFORM_VERSION={version}"
  - path: /.gitlab-ci.yml
    template: 'TERRAFORM_VERSION: "{version}"'
```

On a new release Toolbot replaces `ARG TERRAFORM_VERSION=1.16.3` with `ARG TERRAFORM_VERSION=1.16.4` (and so on) in one merge request. There are no regular expressions from configuration and no guessing:

- the text for the **current** version must be in the file, or the update fails. `toolbot verify` checks this on every MR, so `tool.yaml` and the pins can't drift apart;
- a template that starts or ends with `{version}` never matches inside a longer version (`1.2.0` doesn't match `11.2.0` or `1.2.0-rc.1`);
- line endings and everything else in the file are preserved;
- several tools may pin versions in the same file (one Dockerfile, many `ARG`s).

`{tag}` is available too, e.g. `template: "image: hashicorp/terraform:{version}"` or `ref: {tag}`.
