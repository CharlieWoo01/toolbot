# Running locally

Everything CI does can be done on your machine.

## Install

=== "pip"

    ```sh
    python -m venv .venv
    . .venv/bin/activate                 # Windows: .venv\Scripts\activate
    pip install -e ".[dev]"              # or: pip install .  (just the CLI)
    toolbot --version
    ```

=== "Docker"

    ```sh
    docker build -t toolbot:dev .
    sh scripts/smoke-test-image.sh toolbot:dev
    ```

    Run commands against the current directory:

    ```sh
    docker run --rm -v "$PWD:/work" -w /work --user "$(id -u):$(id -g)" toolbot:dev toolbot outdated
    ```

    `--user` makes files written by `toolbot update` belong to you. On Windows use PowerShell (`-v "${PWD}:/work"`) or prefix Git Bash commands with `MSYS_NO_PATHCONV=1`.

## Read-only commands

```sh
toolbot list
toolbot validate
toolbot verify
toolbot outdated [TOOL…] [--json]
toolbot check --dry-run [TOOL…]
```

`check --dry-run` needs no credentials. If `TOOLBOT_GITLAB_TOKEN` (plus `TOOLBOT_GITLAB_URL` and `TOOLBOT_GITLAB_PROJECT`) are set, it also looks up existing merge requests read-only, so you see "already open" and "rejected" decisions too.

## Changing your working tree

```sh
toolbot update pact-broker-cli                    # (re)fetch the declared version
toolbot update pact-broker-cli --latest           # newest acceptable release
toolbot update pact-broker-cli --version 0.8.7    # a specific version
git diff --stat
```

`update` never touches git. It downloads, verifies and replaces files, and nothing else.

## Testing a full run without waiting for a release

Point Toolbot at a **test project** you own:

1. Create a GitLab project (e.g. `you/toolbot-test`) with a tool pinned to an **older** version:

    ```sh
    toolbot update pact-broker-cli --version 0.8.5   # in a clone of the test project
    git add tools && git commit -m "pin pact 0.8.5" && git push
    ```

2. Create a personal or project access token (`api`) for that project, then:

    ```sh
    export TOOLBOT_GITLAB_URL=https://gitlab.example.com
    export TOOLBOT_GITLAB_PROJECT=you/toolbot-test
    export TOOLBOT_GITLAB_TOKEN=glpat-…
    toolbot check --dry-run
    toolbot check
    toolbot check        # again: "Update already proposed."
    ```

3. Try the lifecycle: close the MR and run again (**rejected**), reopen it, push a commit to the branch and pin an even older version (**blocked**), and so on.

`toolbot check` never touches your checkout. It works in a private temporary repository and fetches the target branch itself. You can run it from any directory.

## Tests and docs

```sh
pytest                      # unit + lifecycle tests (no network; needs git)
pytest -m integration       # live GitHub + GitLab downloads
ruff check src tests && ruff format --check src tests
mypy
pip install -e ".[docs]" && mkdocs serve   # http://127.0.0.1:8000
```

## Troubleshooting local Docker

| Symptom | Fix |
| --- | --- |
| `Docker Desktop is unable to start` | Often a full system disk. Docker's backend log (`%LOCALAPPDATA%\Docker\log\host\com.docker.backend.exe.log`) says `There is not enough space on the disk`. Free space and restart Docker Desktop. |
| Docker's `docker_data.vhdx` stays huge after pruning | WSL disks don't shrink by themselves. After `wsl --shutdown`, compact it with `diskpart` (`select vdisk file=…`, `attach vdisk readonly`, `compact vdisk`, `detach vdisk`) in an admin terminal. |
| `-w /work` becomes `C:/Program Files/Git/work` | Git Bash path conversion: prefix with `MSYS_NO_PATHCONV=1` or use PowerShell. |
