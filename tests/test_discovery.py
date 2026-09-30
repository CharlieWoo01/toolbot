from __future__ import annotations

from pathlib import Path

from conftest import GitHubRelease, Router, github_releases, make_context, widget_yaml, write_tool

from toolbot.config.loader import load_tool
from toolbot.services.discovery import discover


def run(tmp_path: Path, router: Router, releases: list[GitHubRelease], version: str = "1.0.0", extra: str = ""):  # type: ignore[no-untyped-def]
    write_tool(tmp_path, "widget", widget_yaml(version, extra))
    github_releases(router, releases)
    ctx = make_context(router)
    return discover(ctx, load_tool(tmp_path, "widget", ctx.providers))


def rel(tag: str, prerelease: bool = False, draft: bool = False) -> GitHubRelease:
    return GitHubRelease(tag, {}, prerelease=prerelease, draft=draft)


def test_update_available_uses_version_order_not_listing_order(tmp_path: Path, router: Router) -> None:
    found = run(tmp_path, router, [rel("v1.9.0"), rel("v1.10.0"), rel("v1.2.0")])
    assert found.status == "update-available"
    assert found.latest is not None and found.latest.version.raw == "1.10.0"


def test_no_update(tmp_path: Path, router: Router) -> None:
    assert run(tmp_path, router, [rel("v1.0.0"), rel("v0.9.0")]).status == "up-to-date"


def test_prereleases_ignored_by_default(tmp_path: Path, router: Router) -> None:
    found = run(tmp_path, router, [rel("v2.0.0-rc.1"), rel("v1.5.0", prerelease=True), rel("v1.1.0")])
    assert found.latest is not None and found.latest.version.raw == "1.1.0"


def test_prereleases_opt_in(tmp_path: Path, router: Router) -> None:
    found = run(tmp_path, router, [rel("v2.0.0-rc.1"), rel("v1.1.0")], extra="prereleases: true\n")
    assert found.latest is not None and found.latest.version.raw == "2.0.0-rc.1"


def test_drafts_ignored(tmp_path: Path, router: Router) -> None:
    found = run(tmp_path, router, [rel("v3.0.0", draft=True), rel("v1.1.0")])
    assert found.latest is not None and found.latest.version.raw == "1.1.0"


def test_tags_not_matching_template_or_unparseable_are_skipped(tmp_path: Path, router: Router) -> None:
    found = run(tmp_path, router, [rel("server-v9.0.0"), rel("vnext"), rel("9.9.9"), rel("v1.2.0")])
    assert found.latest is not None and found.latest.version.raw == "1.2.0"


def test_ignored_versions_and_constraints(tmp_path: Path, router: Router) -> None:
    found = run(
        tmp_path, router, [rel("v2.1.0"), rel("v1.3.0"), rel("v1.2.0")], extra='ignore:\n  - "1.3.0"\n  - ">=2.0.0"\n'
    )
    assert found.latest is not None and found.latest.version.raw == "1.2.0"
    assert sorted(found.ignored) == ["1.3.0", "2.1.0"]


def test_ahead_and_no_releases(tmp_path: Path, router: Router) -> None:
    assert run(tmp_path, router, [rel("v0.9.0")]).status == "ahead"
    router.routes.clear()
    assert run(tmp_path, router, []).status == "no-releases"
