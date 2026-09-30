# Development

Requires Python 3.11+ and git.

```sh
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev,docs]"

pytest                                 # unit + lifecycle tests (no network)
pytest -m integration                  # live GitHub + GitLab
ruff check src tests && ruff format src tests
mypy
mkdocs serve                           # docs on http://127.0.0.1:8000
docker build -t toolbot:dev . && sh scripts/smoke-test-image.sh toolbot:dev
```

## Layout

```text
src/toolbot/
├── cli.py                  commands and output
├── config/                 models, loader (safe YAML), validation, placeholders
├── versions.py             SemVer/PEP 440 parsing, tag templates, ignore constraints
├── http.py                 HTTPS-only httpx wrapper (timeouts, retries, redirects)
├── git.py                  git via argument lists; private temp repo; leased pushes
├── lock.py                 tool.lock.json
├── paths.py                destination safety
├── assets/                 matching, checksums, transactional install
├── providers/
│   ├── source/             SourceProvider + github, gitlab
│   └── target/             TargetProvider + gitlab
└── services/
    ├── context.py          injectable dependencies
    ├── discovery.py        newest acceptable release
    ├── artifacts.py        apply a version to a working tree
    ├── verify.py           lock ⇄ definition ⇄ files
    ├── messages.py         branch names, commit/MR text
    └── updater.py          the check lifecycle and dry-run plan
```

## Tests

| File | Covers |
| --- | --- |
| `test_versions.py` | ordering (`1.9.0 < 1.10.0`), prereleases, tags, constraints, invalid versions |
| `test_config.py` | parsing, every validation rule, safe YAML |
| `test_http.py` | redirects and credential stripping, HTTPS only, retries (and no retries for 4xx/POST), limits |
| `test_source_providers.py` | GitHub (stable, prerelease, draft, assets, pagination, errors) and GitLab (releases, links, token scoping, errors) |
| `test_assets.py` | matching, checksum formats, atomic replacement and rollback |
| `test_artifacts.py` | downloads, multiple assets, and every failure leaving files untouched |
| `test_discovery.py` | choosing the release: ordering, prereleases, drafts, templates, ignore |
| `test_target_gitlab.py` | MR lookup, labels, creation (idempotent on 409), closing |
| `test_git.py` | worktree, commit, leased push/delete, trailers, credentials via env, ref validation |
| `test_updater.py` | the lifecycle against a real bare git remote and a fake target |
| `test_cli.py` | dry run changes nothing; outdated/list/validate/verify/update output and exit codes |
| `test_messages.py`, `test_schema.py` | MR text safety; schema ⇄ validator sync |

HTTP is mocked with `httpx.MockTransport` (see `tests/conftest.py`). Git tests use real `git` against temporary bare repositories.

## Conventions

- Expected failures raise `ToolbotError(code, message)` with a message that says what to do.
- Never build shell commands; never put tokens in URLs, argv or messages.
- New `tool.yaml` keys go in the validator, `schema/tool.schema.json` and `docs/reference/tool-yaml.md` together (a test enforces the schema part).
