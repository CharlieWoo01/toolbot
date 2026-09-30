# Container image

## Contents

| | |
| --- | --- |
| Base | `python:3.13.15-slim-trixie`, pinned by digest |
| Added | `git`, `ca-certificates`, the `toolbot` package with dependencies pinned by `requirements.lock` |
| User | `toolbot` (uid 10001), unprivileged |
| Default command | `toolbot --help` |

```sh
docker run --rm <image> toolbot --version
docker run --rm -v "$PWD:/work:ro" -w /work <image> toolbot outdated
```

## Tags

| Trigger | Tag | Use |
| --- | --- | --- |
| Git tag `vX.Y.Z` | `X.Y.Z` | **What other projects pin** |
| Default branch | `sha-<short commit>` | Testing unreleased changes |
| Default branch (GitLab only) | `latest-build-cache` | Build cache only; don't run it |

There's deliberately no `latest`. Pin a version, or `version@sha256:…` for full immutability. GHCR images (GitHub workflows) are multi-arch (`linux/amd64`, `linux/arm64`) with a build-provenance attestation.

## Building

=== "Locally"

    ```sh
    docker build -t toolbot:dev .
    sh scripts/smoke-test-image.sh toolbot:dev
    ```

=== "GitLab CI"

    The `image` job builds with Docker-in-Docker, runs `scripts/smoke-test-image.sh`, and pushes (not for MRs). It needs a runner that allows **privileged** containers. Without one, use Buildah:

    ```yaml
    image:
      stage: build
      image: quay.io/buildah/stable:v1.43.4
      variables:
        STORAGE_DRIVER: vfs
        BUILDAH_ISOLATION: chroot
      script:
        - |
          VERSION=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml)
          buildah login -u "$CI_REGISTRY_USER" -p "$CI_REGISTRY_PASSWORD" "$CI_REGISTRY"
          buildah build --format docker --build-arg TOOLBOT_VERSION="$VERSION" -t toolbot:ci .
          buildah run toolbot:ci -- toolbot --version
          if [ "$CI_PIPELINE_SOURCE" != "merge_request_event" ]; then
            buildah push toolbot:ci "docker://$CI_REGISTRY_IMAGE:sha-$CI_COMMIT_SHORT_SHA"
            [ -n "${CI_COMMIT_TAG:-}" ] && buildah push toolbot:ci "docker://$CI_REGISTRY_IMAGE:$VERSION"
          fi
          true
    ```

=== "GitHub Actions"

    `image.yml` (PRs, `main`) and `release.yml` (tags) call the reusable `image-build.yml`: build, smoke test, then push to GHCR with an attestation. See [GitHub Actions](github-actions.md).

## Letting other projects pull the image

Other GitLab projects can pull the image and `include:` the template if **one** of these holds:

- The Toolbot project is **internal** or **public**.
- The Toolbot project is private and the consuming projects (or their group) are on its **CI/CD job token allowlist** (*Settings → CI/CD → Job token permissions*).

## Mirroring

Copy a released tag to another registry without rebuilding:

```sh
skopeo copy --all docker://gitlab.example.com:5050/platform/toolbot:0.1.0 \
                  docker://artifactory.example.com/docker-local/toolbot:0.1.0
```

If Docker Hub is blocked, mirror the Python base image and pass `--build-arg PYTHON_IMAGE=<mirror>/python:3.13.15-slim-trixie@sha256:…`.
