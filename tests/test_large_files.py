"""Files too large for a plain commit: automatic Git LFS, or fetch (not committed)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeTarget, GitSetup, Router, commit_all, git, make_context, sha256, write_tool

from toolbot.config.control import ControlConfig, Defaults, LargeFileMode, validate_control
from toolbot.config.loader import load_tool
from toolbot.errors import ConfigValidationError, ToolbotError
from toolbot.git import GitIdentity, Repository
from toolbot.services.artifacts import apply_version
from toolbot.services.context import Context
from toolbot.services.fetch import fetch_tool
from toolbot.services.updater import Updater, UpdateSettings
from toolbot.services.verify import verify_tool
from toolbot.storage import MarkerFile, marker_updates

DOWNLOADS = "https://dl.example.com"
BIG = 2 * 1024 * 1024  # over the 1 MB limit used in these tests
BIG_YAML = """\
name: big
version: "{version}"
source: {{provider: github, repository: acme/big, versions: tags}}
checksum: {{type: file, url: "{downloads}/{{version}}/SHA256SUMS"}}
assets:
  linux: {{url: "{downloads}/{{version}}/big", destination: "{destination}"{storage}}}
  small: {{url: "{downloads}/{{version}}/small.txt", destination: small.txt}}
"""


def big_yaml(version: str = "1.0.0", destination: str = "bin/big", storage: str = "") -> str:
    storage_entry = f", storage: {storage}" if storage else ""
    return BIG_YAML.format(version=version, downloads=DOWNLOADS, destination=destination, storage=storage_entry)


def publish(router: Router, version: str, size: int = BIG) -> bytes:
    data = (version.encode() * (size // len(version) + 1))[:size]
    router.bytes(f"{DOWNLOADS}/{version}/big", data)
    router.bytes(f"{DOWNLOADS}/{version}/small.txt", f"small {version}")
    sums = f"{sha256(data)}  big\n{sha256(f'small {version}')}  small.txt\n"
    router.bytes(f"{DOWNLOADS}/{version}/SHA256SUMS", sums)
    router.json(f"https://api.github.com/repos/acme/big/git/ref/tags/{version}", {"ref": f"refs/tags/{version}"})
    return data


def context(router: Router, large_files: LargeFileMode = "lfs", max_file_mb: int = 1) -> Context:
    control = ControlConfig(defaults=Defaults(max_file_mb=max_file_mb, large_files=large_files))
    return make_context(router, control=control)


def lock_of(root: Path) -> Any:
    return json.loads((root / "tools/big/tool.lock.json").read_text())


class TestChoosingStorage:
    def test_small_files_stay_plain_git(self, tmp_path: Path, router: Router) -> None:
        write_tool(tmp_path, "big", big_yaml())
        publish(router, "1.0.0", size=100)
        apply_version(context(router), tmp_path, "big")
        assert "storage" not in lock_of(tmp_path)["assets"]["linux"]
        assert not (tmp_path / "tools/big/bin/.gitattributes").exists()

    def test_large_files_go_to_lfs_automatically(self, tmp_path: Path, router: Router) -> None:
        write_tool(tmp_path, "big", big_yaml())
        publish(router, "1.0.0")
        result = apply_version(context(router), tmp_path, "big")
        assets = lock_of(tmp_path)["assets"]
        assert assets["linux"]["storage"] == "lfs"
        assert "storage" not in assets["small"]
        attributes = (tmp_path / "tools/big/bin/.gitattributes").read_text()
        assert attributes == "/big filter=lfs diff=lfs merge=lfs -text\n"
        assert "tools/big/bin/.gitattributes" in result.changed
        assert verify_tool(tmp_path, load_tool(tmp_path, "big", make_context(router).providers)) == []

    def test_explicit_storage_wins_over_size(self, tmp_path: Path, router: Router) -> None:
        write_tool(tmp_path, "big", big_yaml(storage="git"))
        publish(router, "1.0.0")
        apply_version(context(router), tmp_path, "big")
        assert "storage" not in lock_of(tmp_path)["assets"]["linux"]

    def test_switching_back_removes_only_toolbots_lines(self, tmp_path: Path, router: Router) -> None:
        write_tool(tmp_path, "big", big_yaml())
        publish(router, "1.0.0")
        apply_version(context(router), tmp_path, "big")
        attributes = tmp_path / "tools/big/bin/.gitattributes"
        attributes.write_text("*.sh text eol=lf\n" + attributes.read_text())
        apply_version(context(router, max_file_mb=10), tmp_path, "big")  # now small enough for git
        assert attributes.read_text() == "*.sh text eol=lf\n"
        assert "storage" not in lock_of(tmp_path)["assets"]["linux"]

    def test_marker_file_holding_only_toolbots_line_is_deleted(self, tmp_path: Path) -> None:
        file = tmp_path / "bin/big"
        (tmp_path / "bin").mkdir()
        (tmp_path / "bin/.gitignore").write_text("/big\n")
        assert marker_updates({file: "fetch"}, {file: "git"}) == [MarkerFile(tmp_path / "bin/.gitignore", None)]

    def test_verify_reports_pointers_and_missing_attributes(self, tmp_path: Path, router: Router) -> None:
        write_tool(tmp_path, "big", big_yaml())
        publish(router, "1.0.0")
        apply_version(context(router), tmp_path, "big")
        providers = make_context(router).providers
        (tmp_path / "tools/big/bin/big").write_text("version https://git-lfs.github.com/spec/v1\noid sha256:x\n")
        assert "is a Git LFS pointer" in verify_tool(tmp_path, load_tool(tmp_path, "big", providers))[0]
        (tmp_path / "tools/big/bin/.gitattributes").unlink()
        assert any(
            "not tracked by Git LFS" in problem
            for problem in verify_tool(tmp_path, load_tool(tmp_path, "big", providers))
        )


class TestConfiguration:
    def test_defaults(self) -> None:
        config = validate_control({"defaults": {"max-file-mb": 90, "large-files": "fetch"}}, "toolbot.yaml")
        assert (config.defaults.max_file_mb, config.defaults.large_files) == (90, "fetch")
        assert (Defaults().max_file_mb, Defaults().large_files) == (50, "lfs")

    @pytest.mark.parametrize(
        ("defaults", "expected"),
        [({"max-file-mb": "big"}, "max-file-mb"), ({"large-files": "s3"}, "large-files")],
    )
    def test_invalid_defaults(self, defaults: dict[str, object], expected: str) -> None:
        with pytest.raises(ConfigValidationError, match=expected):
            validate_control({"defaults": defaults}, "toolbot.yaml")

    def test_invalid_storage(self, tmp_path: Path, router: Router) -> None:
        write_tool(tmp_path, "big", big_yaml(storage="s3"))
        with pytest.raises(ConfigValidationError, match="storage: must be one of auto, git, lfs, fetch"):
            load_tool(tmp_path, "big", make_context(router).providers)


def run_updater(setup: GitSetup, router: Router, ctx: Context) -> str:
    router.json(
        "https://api.github.com/repos/acme/big/git/matching-refs/tags/",
        [{"ref": "refs/tags/1.0.0"}, {"ref": "refs/tags/2.0.0"}],
    )
    repository = Repository(setup.url, GitIdentity("Toolbot", "toolbot@example.com"))
    [outcome] = Updater(ctx, FakeTarget(setup.url), repository, UpdateSettings("main")).run()
    assert outcome.status == "created", outcome.message
    return "toolbot/big/2.0.0"


def git_lfs_available() -> bool:
    try:
        return subprocess.run(["git", "lfs", "version"], capture_output=True, check=False).returncode == 0
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not git_lfs_available(), reason="git-lfs not installed")
def test_update_commits_lfs_pointer_and_uploads_the_object(git_setup: GitSetup, router: Router) -> None:
    write_tool(git_setup.work, "big", big_yaml(destination="/bin/big"))
    publish(router, "1.0.0", size=100)  # 1.0.0 was small: a plain blob on main
    apply_version(context(router), git_setup.work, "big")
    commit_all(git_setup.work)
    data = publish(router, "2.0.0")

    branch = run_updater(git_setup, router, context(router))

    pointer = git_setup.show(branch, "bin/big")
    assert pointer.startswith("version https://git-lfs.github.com/spec/v1")
    assert f"oid sha256:{sha256(data)}" in pointer
    assert git_setup.show(branch, "bin/.gitattributes") == "/big filter=lfs diff=lfs merge=lfs -text"
    oid = sha256(data)
    stored = git_setup.remote / "lfs" / "objects" / oid[:2] / oid[2:4] / oid
    assert stored.read_bytes() == data  # uploaded before the branch was pushed
    assert git_setup.show(branch, "tools/big/small.txt") == "small 2.0.0"  # small files stay plain


def test_fetch_storage_is_not_committed_and_toolbot_fetch_restores_it(
    git_setup: GitSetup, router: Router, tmp_path: Path
) -> None:
    write_tool(git_setup.work, "big", big_yaml(destination="/bin/big"))
    publish(router, "1.0.0", size=100)
    apply_version(context(router, "fetch"), git_setup.work, "big")
    commit_all(git_setup.work)  # 1.0.0: committed as a plain file
    data = publish(router, "2.0.0")

    branch = run_updater(git_setup, router, context(router, "fetch"))

    tree = git(git_setup.remote, "ls-tree", "-r", "--name-only", branch).split()
    assert "bin/big" not in tree  # removed from the index, now ignored
    assert git_setup.show(branch, "bin/.gitignore") == "/big"
    locked = json.loads(git_setup.show(branch, "tools/big/tool.lock.json"))["assets"]["linux"]
    assert (locked["storage"], locked["sha256"]) == ("fetch", sha256(data))

    # A checkout of the branch restores the file with toolbot fetch.
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "--quiet", "--branch", branch, git_setup.url, str(clone))
    assert not (clone / "bin/big").exists()
    tool = load_tool(clone, "big", make_context(router).providers)
    assert verify_tool(clone, tool) == []  # a missing fetch file is fine before fetching
    assert fetch_tool(make_context(router), clone, tool) == ["bin/big"]
    assert (clone / "bin/big").read_bytes() == data
    router.requests.clear()
    assert fetch_tool(make_context(router), clone, tool) == []  # already present: nothing downloaded
    assert router.requests == []
    assert git(clone, "status", "--porcelain") == ""  # ignored, not untracked


def test_fetch_refuses_a_file_that_differs_from_the_lock(tmp_path: Path, router: Router) -> None:
    write_tool(tmp_path, "big", big_yaml(storage="fetch"))
    publish(router, "1.0.0")
    apply_version(context(router), tmp_path, "big")
    (tmp_path / "tools/big/bin/big").unlink()
    # Upstream replaced the file and its checksum after the update was reviewed.
    tampered = b"x" * 10
    router.bytes(f"{DOWNLOADS}/1.0.0/big", tampered)
    router.bytes(f"{DOWNLOADS}/1.0.0/SHA256SUMS", f"{sha256(tampered)}  big\n")
    with pytest.raises(ToolbotError) as error:
        fetch_tool(make_context(router), tmp_path, load_tool(tmp_path, "big", make_context(router).providers))
    assert error.value.code == "CHECKSUM_MISMATCH"
    assert "tool.lock.json" in error.value.message
    assert not (tmp_path / "tools/big/bin/big").exists()
