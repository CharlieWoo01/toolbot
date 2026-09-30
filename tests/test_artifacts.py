"""apply_version: download → verify → atomic install, and every failure leaving files untouched."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest
from conftest import (
    GH_API,
    GitHubRelease,
    Router,
    github_releases,
    make_context,
    sha256,
    widget_files,
    widget_yaml,
    write_tool,
)

from toolbot.config.loader import load_tool
from toolbot.errors import ToolbotError
from toolbot.services.artifacts import apply_version
from toolbot.services.verify import verify_tool


def snapshot(directory: Path) -> dict[str, bytes]:
    return {str(p.relative_to(directory)): p.read_bytes() for p in sorted(directory.rglob("*")) if p.is_file()}


@pytest.fixture
def installed(tmp_path: Path, router: Router) -> tuple[Path, Path, Path]:
    """widget 1.0.0 installed; returns (root, tool dir, temp root)."""
    root, temp = tmp_path / "repo", tmp_path / "tmp"
    temp.mkdir()
    tool_dir = write_tool(root, "widget", widget_yaml("1.0.0"))
    github_releases(router, [GitHubRelease("v1.0.0", widget_files("1.0.0"))])
    apply_version(make_context(router, temp_root=temp), root, "widget")
    return root, tool_dir, temp


def test_installs_asset_and_writes_lock(installed: tuple[Path, Path, Path], router: Router) -> None:
    root, tool_dir, temp = installed
    assert (tool_dir / "widget.zip").read_text() == "zip-1.0.0"
    lock = json.loads((tool_dir / "tool.lock.json").read_text())
    assert lock["version"] == "1.0.0" and lock["tag"] == "v1.0.0"
    assert lock["assets"]["windows-amd64"] == {
        "upstream": "widget-1.0.0-windows-amd64.zip",
        "destination": "tools/widget/widget.zip",
        "size": len("zip-1.0.0"),
        "sha256": sha256("zip-1.0.0"),
        "verifiedBy": "sha256:widget-1.0.0-windows-amd64.zip.sha256",
    }
    assert list(temp.iterdir()) == []
    assert verify_tool(root, load_tool(root, "widget", make_context(router).providers)) == []


def test_new_version_updates_definition_lock_and_artifact(installed: tuple[Path, Path, Path], router: Router) -> None:
    root, tool_dir, temp = installed
    github_releases(router, [GitHubRelease("v2.0.0", widget_files("2.0.0"))])
    result = apply_version(make_context(router, temp_root=temp), root, "widget", "2.0.0")
    assert result.previous_version == "1.0.0"
    assert 'version: "2.0.0"' in (tool_dir / "tool.yaml").read_text()
    assert (tool_dir / "widget.zip").read_text() == "zip-2.0.0"
    assert sorted(result.changed) == [
        "tools/widget/tool.lock.json",
        "tools/widget/tool.yaml",
        "tools/widget/widget.zip",
    ]


def test_rerun_is_idempotent(installed: tuple[Path, Path, Path], router: Router) -> None:
    root, _, temp = installed
    assert apply_version(make_context(router, temp_root=temp), root, "widget").changed == []


def test_multiple_assets_verified_by_shared_checksum_file(tmp_path: Path, router: Router) -> None:
    root = tmp_path / "repo"
    tool_dir = write_tool(
        root,
        "glab",
        """\
name: glab
version: "1.2.0"
tag: v{version}
source: {provider: gitlab, repository: gitlab-org/cli}
checksum: {type: file, file: checksums.txt}
assets:
  linux-amd64: {name: "glab_{version}_linux_amd64.tar.gz", destination: linux-amd64/glab.tar.gz}
  windows-amd64: {name: "glab_{version}_windows_amd64.zip", destination: windows-amd64/glab.zip}
""",
    )
    pkg = "https://gitlab.com/api/v4/projects/gitlab-org%2Fcli/packages/generic/glab/1.2.0"
    names = ["glab_1.2.0_linux_amd64.tar.gz", "glab_1.2.0_windows_amd64.zip", "checksums.txt"]
    router.json(
        "https://gitlab.com/api/v4/projects/gitlab-org%2Fcli/releases/v1.2.0",
        {"tag_name": "v1.2.0", "assets": {"links": [{"name": n, "url": f"{pkg}/{n}"} for n in names]}},
    )
    router.bytes(f"{pkg}/{names[0]}", "linux").bytes(f"{pkg}/{names[1]}", "windows")
    router.bytes(f"{pkg}/checksums.txt", f"{sha256('linux')}  {names[0]}\n{sha256('windows')}  {names[1]}\n")
    apply_version(make_context(router), root, "glab")
    assert (tool_dir / "linux-amd64/glab.tar.gz").read_text() == "linux"
    assert (tool_dir / "windows-amd64/glab.zip").read_text() == "windows"
    assert len(router.to(f"{pkg}/checksums.txt")) == 1


class TestFailuresLeaveFilesUntouched:
    def expect_untouched(
        self, installed: tuple[Path, Path, Path], router: Router, code: str, env: dict[str, str] | None = None
    ) -> None:
        root, tool_dir, temp = installed
        before = snapshot(tool_dir)
        with pytest.raises(ToolbotError) as err:
            apply_version(make_context(router, env, temp_root=temp), root, "widget", "2.0.0")
        assert err.value.code == code, err.value.message
        assert snapshot(tool_dir) == before
        assert list(temp.iterdir()) == []

    def test_checksum_mismatch(self, installed: tuple[Path, Path, Path], router: Router) -> None:
        files = widget_files("2.0.0")
        files["widget-2.0.0-windows-amd64.zip"] = "tampered"
        github_releases(router, [GitHubRelease("v2.0.0", files)])
        self.expect_untouched(installed, router, "CHECKSUM_MISMATCH")

    def test_checksum_file_missing(self, installed: tuple[Path, Path, Path], router: Router) -> None:
        github_releases(router, [GitHubRelease("v2.0.0", {"widget-2.0.0-windows-amd64.zip": "zip"})])
        self.expect_untouched(installed, router, "CHECKSUM_UNAVAILABLE")

    def test_http_404_on_download(self, installed: tuple[Path, Path, Path], router: Router) -> None:
        github_releases(router, [GitHubRelease("v2.0.0", widget_files("2.0.0"))])
        cdn = [k for k in router.routes if "release-assets" in k[1]]
        for key in cdn:
            router.routes[key] = lambda _r: httpx.Response(404)
        self.expect_untouched(installed, router, "HTTP_ERROR")

    def test_release_not_found(self, installed: tuple[Path, Path, Path], router: Router) -> None:
        self.expect_untouched(installed, router, "RELEASE_NOT_FOUND")

    def test_authentication_failure(self, installed: tuple[Path, Path, Path], router: Router) -> None:
        router.json(f"{GH_API}/releases/tags/v2.0.0", {"message": "Bad credentials"}, status=401)
        self.expect_untouched(installed, router, "AUTH_FAILED", {"TOOLBOT_GITHUB_TOKEN": "bad"})

    def test_asset_renamed_upstream(self, installed: tuple[Path, Path, Path], router: Router) -> None:
        github_releases(router, [GitHubRelease("v2.0.0", {"widget-2.0.0-win64.zip": "x"})])
        self.expect_untouched(installed, router, "ASSET_NOT_FOUND")

    def test_second_asset_failing_leaves_first_untouched(self, tmp_path: Path, router: Router) -> None:
        root = tmp_path / "repo"
        tool_dir = write_tool(
            root,
            "duo",
            """\
name: duo
version: "1.0.0"
tag: v{version}
source: {provider: github, repository: acme/widget}
checksum: {type: provider}
assets:
  a: {name: "a-{version}.zip", destination: a.zip}
  b: {name: "b-{version}.zip", destination: b.zip}
""",
        )
        github_releases(router, [GitHubRelease("v1.0.0", {"a-1.0.0.zip": "a1", "b-1.0.0.zip": "b1"})])
        apply_version(make_context(router), root, "duo")
        before = snapshot(tool_dir)
        files: dict[str, bytes | str] = {"a-2.0.0.zip": "a2", "b-2.0.0.zip": "b2"}
        github_releases(router, [GitHubRelease("v2.0.0", files)])
        # Corrupt only asset b's download.
        b_cdn = next(k for k in router.routes if "release-assets" in k[1] and router.routes[k](None).content == b"b2")  # type: ignore[arg-type]
        router.routes[b_cdn] = lambda _r: httpx.Response(200, content=b"evil")
        with pytest.raises(ToolbotError) as err:
            apply_version(make_context(router), root, "duo", "2.0.0")
        assert err.value.code == "CHECKSUM_MISMATCH"
        assert snapshot(tool_dir) == before

    def test_path_traversal_via_symlink(self, tmp_path: Path, router: Router) -> None:
        root = tmp_path / "repo"
        tool_dir = write_tool(
            root, "widget", widget_yaml("1.0.0").replace("destination: widget.zip", "destination: bin/widget.zip")
        )
        outside = tmp_path / "outside"
        outside.mkdir()
        try:
            os.symlink(outside, tool_dir / "bin", target_is_directory=True)
        except OSError:
            pytest.skip("symlinks not permitted")
        github_releases(router, [GitHubRelease("v1.0.0", widget_files("1.0.0"))])
        with pytest.raises(ToolbotError) as err:
            apply_version(make_context(router), root, "widget")
        assert err.value.code == "UNSAFE_PATH"
        assert list(outside.iterdir()) == []
        assert not router.to(f"{GH_API}/releases/assets")


def test_verify_detects_problems(installed: tuple[Path, Path, Path], router: Router) -> None:
    root, tool_dir, _ = installed
    providers = make_context(router).providers
    (tool_dir / "widget.zip").write_text("tampered")
    assert "does not match the lock file" in verify_tool(root, load_tool(root, "widget", providers))[0]
    (tool_dir / "widget.zip").write_text("zip-1.0.0")
    (tool_dir / "tool.yaml").write_text(widget_yaml("2.0.0"))
    assert (
        "declares version 2.0.0 but committed artifacts are for 1.0.0"
        in verify_tool(root, load_tool(root, "widget", providers))[0]
    )
