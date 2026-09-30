# Quick start

Try Toolbot on your machine. You need **Python 3.11+** and **git**, or Docker.

## 1. Install

=== "pip (from a checkout)"

    ```sh
    git clone <toolbot repository url> toolbot && cd toolbot
    python -m venv .venv
    . .venv/bin/activate            # Windows: .venv\Scripts\activate
    pip install -e .
    ```

=== "Docker"

    ```sh
    git clone <toolbot repository url> toolbot && cd toolbot
    docker build -t toolbot:dev .
    alias toolbot='docker run --rm -v "$PWD:/work" -w /work --user "$(id -u):$(id -g)" toolbot:dev toolbot'
    ```

## 2. Look around (read-only)

```console
$ toolbot list
TOOL             VERSION  SOURCE                                  ASSETS
pact-broker-cli  0.8.6    github:pact-foundation/pact-broker-cli  windows-amd64

$ toolbot validate
1 valid, 0 invalid

$ toolbot verify
verified 1 tool(s), 0 problem(s)

$ toolbot outdated
TOOL             CURRENT  LATEST  STATUS
pact-broker-cli  0.8.6    0.8.8   update available
```

## 3. Preview an automated run

```console
$ toolbot check --dry-run

pact-broker-cli
  Current: 0.8.6
  Latest:  0.8.8
  Update available.
  Assets:
    ✓ pact-broker-cli-x86_64-pc-windows-msvc.zip → pact-broker-cli-x86_64-pc-windows-msvc.zip [sha256 via pact-broker-cli-x86_64-pc-windows-msvc.zip.sha256]
    (target not configured: existing merge requests not checked)
  Would create: toolbot/pact-broker-cli/0.8.8
  https://github.com/pact-foundation/pact-broker-cli/releases/tag/v0.8.8

Dry run: 1 tool(s): 1 would-create
```

Nothing was downloaded, written, committed or pushed.

## 4. Apply an update to your working tree

```console
$ toolbot update pact-broker-cli --latest
toolbot: pact-broker-cli 0.8.6 -> 0.8.8 (github:pact-foundation/pact-broker-cli@v0.8.8)
  windows-amd64: pact-broker-cli-x86_64-pc-windows-msvc.zip (3188345 bytes, verified: sha256:pact-broker-cli-x86_64-pc-windows-msvc.zip.sha256)
toolbot: updated tools/pact-broker-cli/tool.yaml, tools/pact-broker-cli/tool.lock.json, tools/pact-broker-cli/pact-broker-cli-x86_64-pc-windows-msvc.zip
```

`git diff --stat` shows exactly what a merge request would contain. Undo with `git checkout -- tools/`.

## Next

- [Set it up in GitLab CI](../deploy/gitlab.md) for automatic merge requests.
- [Add your own tool](adding-a-tool.md).
