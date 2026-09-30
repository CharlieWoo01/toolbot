# Releasing

Semantic versioning. The git tag, `[project].version` in `pyproject.toml` and the image tag always match.

1. Make sure the default branch pipeline is green, and run `pytest -m integration`.
2. Bump `version` in `pyproject.toml` (and the `v0.x.y` / `:0.x.y` examples in the docs if you want them to show the new release).
3. If runtime dependencies changed, regenerate `requirements.lock` (instructions in its header).
4. Merge, then tag the merge commit:

    ```sh
    git tag v0.2.0
    git push origin v0.2.0
    ```

5. The tag pipeline checks the tag against `pyproject.toml`, runs the tests, builds, smoke-tests and pushes `…/toolbot:0.2.0`. On GitHub it also creates a Release with the image digest and a provenance attestation.
6. Write release notes: changes, any `tool.yaml`/lock-file or CI-template changes, and migration steps.

## What counts as breaking

- Removing or renaming `tool.yaml` fields, or tightening validation so valid files fail
- A new `lockfileVersion`
- Changing branch naming (it's the identity of open update requests)
- Changing CI template job names or required variables
- Renaming environment variables
