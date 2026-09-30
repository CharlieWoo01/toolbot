"""Opt-in tests against real upstreams: ``pytest -m integration``.

Catch upstream API or release-layout changes that mocks cannot. Excluded from
the default run so the build never depends on GitHub/GitLab availability.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from toolbot.config.loader import load_tool
from toolbot.services.artifacts import apply_version
from toolbot.services.context import Context, create_context
from toolbot.services.discovery import discover
from toolbot.services.updater import plan

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def ctx() -> Iterator[Context]:
    context = create_context()
    yield context
    context.http.close()


def test_github_pact_broker_cli(tmp_path: Path, ctx: Context) -> None:
    shutil.copytree(REPO / "tools/pact-broker-cli", tmp_path / "tools/pact-broker-cli")
    found = discover(ctx, load_tool(tmp_path, "pact-broker-cli", ctx.providers))
    assert found.latest is not None and found.latest.release.tag.startswith("v")
    apply_version(ctx, tmp_path, "pact-broker-cli", "0.8.6")
    lock = json.loads((tmp_path / "tools/pact-broker-cli/tool.lock.json").read_text())
    # Published in the upstream sha256.sum for v0.8.6.
    assert (
        lock["assets"]["windows-amd64"]["sha256"] == "bb639caabdcfde754c4059b99f181efa9f64a5a87699bf185c8a75b7c7e8dc40"
    )


def test_gitlab_glab(tmp_path: Path, ctx: Context) -> None:
    shutil.copytree(REPO / "examples/tools/glab", tmp_path / "tools/glab")
    found = discover(ctx, load_tool(tmp_path, "glab", ctx.providers))
    assert found.status in ("update-available", "up-to-date")
    apply_version(ctx, tmp_path, "glab", "1.119.0")
    lock = json.loads((tmp_path / "tools/glab/tool.lock.json").read_text())
    assert lock["assets"]["linux-amd64"]["verifiedBy"] == "sha256:checksums.txt"


def test_every_example_resolves_against_its_live_release(ctx: Context) -> None:
    """Catches upstream projects renaming assets or checksum files."""
    outcomes = plan(ctx, REPO / "examples", None, None, None)
    failures = {o.tool: o.message for o in outcomes if o.status == "failed"}
    assert failures == {}
    assert len(outcomes) >= 8
