"""Archives (extract), files committed in a source repository (path), plain git
tags (versions: tags), repository-root destinations and version-file pins."""

from __future__ import annotations

import io
import json
import os
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest
from conftest import FakeTarget, GitSetup, Router, commit_all, git, make_context, sha256, write_tool

from toolbot.config.loader import load_tool
from toolbot.errors import ToolbotError
from toolbot.git import GitIdentity, Repository
from toolbot.services.artifacts import apply_version
from toolbot.services.discovery import discover
from toolbot.services.updater import Updater, UpdateSettings
from toolbot.services.verify import verify_tool

DOWNLOADS = "https://releases.example.com/terraform"
TERRAFORM_API = "https://api.github.com/repos/hashicorp/terraform"
TERRAFORM_YAML = """\
name: terraform
version: "{version}"
tag: v{{version}}
source: {{provider: github, repository: hashicorp/terraform, versions: tags}}
checksum: {{type: file, url: "{downloads}/{{version}}/terraform_{{version}}_SHA256SUMS"}}
assets:
  linux-amd64:
    url: "{downloads}/{{version}}/terraform_{{version}}_linux_amd64.zip"
    extract: terraform
    destination: /bin/terraform
version-files:
  - {{path: /.terraform-version, template: "{{version}}"}}
  - {{path: /Dockerfile, template: "ARG TERRAFORM_VERSION={{version}}"}}
"""


def make_zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def make_tar_gz(files: dict[str, str], symlinks: dict[str, str] | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        for name, target in (symlinks or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            archive.addfile(info)
    return buffer.getvalue()


def snapshot(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and ".git" not in path.parts
    }


def publish_terraform(router: Router, version: str, binary: str | None = None) -> bytes:
    archive = make_zip({"terraform": binary or f"tf-{version}", "LICENSE.txt": "MPL-2.0"})
    name = f"terraform_{version}_linux_amd64.zip"
    router.bytes(f"{DOWNLOADS}/{version}/{name}", archive)
    router.bytes(f"{DOWNLOADS}/{version}/terraform_{version}_SHA256SUMS", f"{sha256(archive)}  {name}\n")
    router.json(f"{TERRAFORM_API}/git/ref/tags/v{version}", {"ref": f"refs/tags/v{version}"})
    return archive


def terraform_repository(root: Path, router: Router) -> Path:
    write_tool(root, "terraform", TERRAFORM_YAML.format(version="1.0.0", downloads=DOWNLOADS))
    (root / ".terraform-version").write_text("1.0.0\n")
    (root / "Dockerfile").write_bytes(b"FROM alpine\r\nARG TERRAFORM_VERSION=1.0.0\r\nARG HELM_VERSION=11.0.0\r\n")
    publish_terraform(router, "1.0.0")
    apply_version(make_context(router), root, "terraform")
    return root


class TestArchivesRootDestinationsAndVersionFiles:
    def test_extracts_member_to_repository_root(self, tmp_path: Path, router: Router) -> None:
        root = terraform_repository(tmp_path / "repo", router)
        assert (root / "bin/terraform").read_text() == "tf-1.0.0"
        if os.name == "posix":
            assert (root / "bin/terraform").stat().st_mode & stat.S_IXUSR
        lock = json.loads((root / "tools/terraform/tool.lock.json").read_text())
        locked = lock["assets"]["linux-amd64"]
        assert locked["upstream"] == f"{DOWNLOADS}/1.0.0/terraform_1.0.0_linux_amd64.zip!terraform"
        assert locked["destination"] == "bin/terraform"
        assert locked["sha256"] == sha256("tf-1.0.0")  # of the committed file, not the archive
        assert locked["verifiedBy"] == f"sha256:{DOWNLOADS}/1.0.0/terraform_1.0.0_SHA256SUMS"
        assert verify_tool(root, load_tool(root, "terraform", make_context(router).providers)) == []

    def test_update_bumps_binary_and_version_pins(self, tmp_path: Path, router: Router) -> None:
        root = terraform_repository(tmp_path / "repo", router)
        publish_terraform(router, "2.0.0")
        result = apply_version(make_context(router), root, "terraform", "2.0.0")
        assert (root / "bin/terraform").read_text() == "tf-2.0.0"
        assert (root / ".terraform-version").read_text() == "2.0.0\n"
        # Line endings preserved; HELM_VERSION=11.0.0 is not a 1.0.0 pin.
        assert (root / "Dockerfile").read_bytes() == (
            b"FROM alpine\r\nARG TERRAFORM_VERSION=2.0.0\r\nARG HELM_VERSION=11.0.0\r\n"
        )
        assert sorted(result.changed) == [
            ".terraform-version",
            "Dockerfile",
            "bin/terraform",
            "tools/terraform/tool.lock.json",
            "tools/terraform/tool.yaml",
        ]
        assert result.paths == ["tools/terraform", "bin/terraform", ".terraform-version", "Dockerfile"]
        assert result.executable_paths == ["bin/terraform"]

    def test_checksum_mismatch_leaves_whole_repository_untouched(self, tmp_path: Path, router: Router) -> None:
        root = terraform_repository(tmp_path / "repo", router)
        publish_terraform(router, "2.0.0")
        router.bytes(f"{DOWNLOADS}/2.0.0/terraform_2.0.0_linux_amd64.zip", make_zip({"terraform": "evil"}))
        before = snapshot(root)
        with pytest.raises(ToolbotError) as error:
            apply_version(make_context(router), root, "terraform", "2.0.0")
        assert error.value.code == "CHECKSUM_MISMATCH"
        assert snapshot(root) == before

    def test_version_file_out_of_sync_fails_before_downloading(self, tmp_path: Path, router: Router) -> None:
        root = terraform_repository(tmp_path / "repo", router)
        (root / ".terraform-version").write_text("1.0.0-rc.1\n")  # not a 1.0.0 pin
        publish_terraform(router, "2.0.0")
        router.requests.clear()
        with pytest.raises(ToolbotError) as error:
            apply_version(make_context(router), root, "terraform", "2.0.0")
        assert error.value.code == "VERSION_FILE_MISMATCH"
        assert '.terraform-version does not contain "1.0.0"' in error.value.message
        assert not router.to(DOWNLOADS)
        problems = verify_tool(root, load_tool(root, "terraform", make_context(router).providers))
        assert problems == ['terraform: version-files: /.terraform-version does not contain "1.0.0"']

    def test_tags_are_discovered_without_releases(self, tmp_path: Path, router: Router) -> None:
        root = terraform_repository(tmp_path / "repo", router)
        refs = [{"ref": f"refs/tags/v{version}"} for version in ("0.9.0", "1.0.0", "2.0.0", "2.1.0-beta1")]
        router.json(f"{TERRAFORM_API}/git/matching-refs/tags/v", refs)
        found = discover(make_context(router), load_tool(root, "terraform", make_context(router).providers))
        assert found.latest is not None and found.latest.version.raw == "2.0.0"
        assert found.latest.release.url == "https://github.com/hashicorp/terraform/tree/v2.0.0"
        assert not router.to(f"{TERRAFORM_API}/releases")


class TestArchiveSafety:
    def write(self, root: Path, extract: str, extra_asset: str = "") -> None:
        write_tool(
            root,
            "cli",
            f"""\
name: cli
version: "1.0.0"
source: {{provider: github, repository: acme/cli, versions: tags}}
checksum: {{type: file, url: "https://dl.example.com/{{version}}/SHA256SUMS"}}
assets:
  linux:
    url: "https://dl.example.com/{{version}}/cli.tar.gz"
    extract: "{extract}"
    destination: cli
{extra_asset}""",
        )

    def serve(self, router: Router, archive: bytes) -> None:
        router.bytes("https://dl.example.com/1.0.0/cli.tar.gz", archive)
        router.bytes("https://dl.example.com/1.0.0/SHA256SUMS", f"{sha256(archive)}  cli.tar.gz\n")
        router.json("https://api.github.com/repos/acme/cli/git/ref/tags/1.0.0", {"ref": "refs/tags/1.0.0"})

    def test_several_members_from_one_download(self, tmp_path: Path, router: Router) -> None:
        extra = '  completion:\n    url: "https://dl.example.com/{version}/cli.tar.gz"\n'
        extra += "    extract: cli-{version}/completion.bash\n    destination: completion.bash\n"
        self.write(tmp_path, "cli-{version}/bin/cli", extra)
        self.serve(router, make_tar_gz({"cli-1.0.0/bin/cli": "binary", "cli-1.0.0/completion.bash": "complete"}))
        apply_version(make_context(router), tmp_path, "cli")
        assert (tmp_path / "tools/cli/cli").read_text() == "binary"
        assert (tmp_path / "tools/cli/completion.bash").read_text() == "complete"
        assert len(router.to("https://dl.example.com/1.0.0/cli.tar.gz")) == 1

    def test_missing_member_lists_what_is_there(self, tmp_path: Path, router: Router) -> None:
        self.write(tmp_path, "bin/cli")
        self.serve(router, make_tar_gz({"cli-1.0.0/bin/cli": "binary"}))
        with pytest.raises(ToolbotError) as error:
            apply_version(make_context(router), tmp_path, "cli")
        assert error.value.code == "ASSET_NOT_FOUND"
        assert "files: cli-1.0.0/bin/cli" in error.value.message
        assert not (tmp_path / "tools/cli/cli").exists()

    def test_symlink_members_are_refused(self, tmp_path: Path, router: Router) -> None:
        self.write(tmp_path, "cli")
        self.serve(router, make_tar_gz({}, symlinks={"cli": "/etc/passwd"}))
        with pytest.raises(ToolbotError) as error:
            apply_version(make_context(router), tmp_path, "cli")
        assert error.value.code == "ARCHIVE_INVALID"

    def test_zip_symlink_members_are_refused(self, tmp_path: Path, router: Router) -> None:
        self.write(tmp_path, "cli")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            link = zipfile.ZipInfo("cli")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, "/etc/passwd")
        self.serve(router, buffer.getvalue())  # format is detected from content, not the name
        with pytest.raises(ToolbotError, match="not a regular file"):
            apply_version(make_context(router), tmp_path, "cli")

    def test_not_an_archive(self, tmp_path: Path, router: Router) -> None:
        self.write(tmp_path, "cli")
        self.serve(router, b"just a binary")
        with pytest.raises(ToolbotError, match="not a zip or tar archive"):
            apply_version(make_context(router), tmp_path, "cli")

    def test_oversized_member_is_refused(self, tmp_path: Path, router: Router) -> None:
        self.write(tmp_path, "cli")
        self.serve(router, make_tar_gz({"cli": "x" * 2048}))
        ctx = make_context(router)
        ctx.max_download_bytes = 1024
        with pytest.raises(ToolbotError, match="over the 1024 byte limit"):
            apply_version(ctx, tmp_path, "cli")


class TestFilesCommittedInTheSourceRepository:
    GITHUB_YAML = """\
name: widget
version: "1.0.0"
tag: v{version}
source: {provider: github, repository: acme/widget}
checksum: {type: file, path: "dist/SHA256SUMS"}
assets:
  linux: {path: "dist/linux/widget", destination: widget}
"""

    def test_github_contents_api(self, tmp_path: Path, router: Router) -> None:
        write_tool(tmp_path, "widget", self.GITHUB_YAML)
        contents = "https://api.github.com/repos/acme/widget/contents/dist"
        router.json("https://api.github.com/repos/acme/widget/releases/tags/v1.0.0", {"tag_name": "v1.0.0"})
        router.bytes(f"{contents}/linux/widget", "binary")
        router.bytes(f"{contents}/SHA256SUMS", f"{sha256('binary')}  widget\n")
        apply_version(make_context(router), tmp_path, "widget")
        assert (tmp_path / "tools/widget/widget").read_text() == "binary"
        [request] = router.to(f"{contents}/linux/widget")
        assert request.url.params["ref"] == "v1.0.0"
        assert request.headers["accept"] == "application/vnd.github.raw"
        lock = json.loads((tmp_path / "tools/widget/tool.lock.json").read_text())
        assert lock["assets"]["linux"]["upstream"] == "dist/linux/widget"
        assert lock["assets"]["linux"]["verifiedBy"] == "sha256:dist/SHA256SUMS"

    def test_git_lfs_pointer_is_never_committed(self, tmp_path: Path, router: Router) -> None:
        write_tool(
            tmp_path, "widget", self.GITHUB_YAML.replace('{type: file, path: "dist/SHA256SUMS"}', "{type: none}")
        )
        router.json("https://api.github.com/repos/acme/widget/releases/tags/v1.0.0", {"tag_name": "v1.0.0"})
        pointer = "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 123\n"
        router.bytes("https://api.github.com/repos/acme/widget/contents/dist/linux/widget", pointer)
        with pytest.raises(ToolbotError, match="Git LFS pointer"):
            apply_version(make_context(router), tmp_path, "widget")
        assert not (tmp_path / "tools/widget/widget").exists()

    def test_gitlab_repository_files_api_with_tags(self, tmp_path: Path, router: Router) -> None:
        write_tool(
            tmp_path,
            "cli",
            """\
name: cli
version: "1.0.0"
tag: release-{version}
source: {provider: gitlab, repository: platform/cli, versions: tags}
assets:
  linux: {path: "bin/cli-linux", destination: cli}
""",
        )
        project = "https://gitlab.com/api/v4/projects/platform%2Fcli"
        router.json(f"{project}/repository/tags", [{"name": "release-1.1.0"}, {"name": "release-1.0.0"}])
        router.json(f"{project}/repository/tags/release-1.1.0", {"name": "release-1.1.0"})
        router.bytes(f"{project}/repository/files/bin%2Fcli-linux/raw", "cli 1.1.0")
        ctx = make_context(router)
        found = discover(ctx, load_tool(tmp_path, "cli", ctx.providers))
        assert found.latest is not None and found.latest.release.tag == "release-1.1.0"
        [listing] = router.to(f"{project}/repository/tags?")
        assert listing.url.params["search"] == "^release-"
        apply_version(ctx, tmp_path, "cli", "1.1.0")
        assert (tmp_path / "tools/cli/cli").read_text() == "cli 1.1.0"
        [download] = router.to(f"{project}/repository/files/")
        assert (download.url.params["ref"], download.url.params["lfs"]) == ("release-1.1.0", "true")


def test_update_request_commits_root_files_with_executable_bit(git_setup: GitSetup, router: Router) -> None:
    terraform_repository(git_setup.work, router)
    commit_all(git_setup.work)
    publish_terraform(router, "2.0.0")
    router.json(f"{TERRAFORM_API}/git/matching-refs/tags/v", [{"ref": "refs/tags/v1.0.0"}, {"ref": "refs/tags/v2.0.0"}])
    target = FakeTarget(git_setup.url)
    repository = Repository(git_setup.url, GitIdentity("Toolbot", "toolbot@example.com"))
    [outcome] = Updater(make_context(router), target, repository, UpdateSettings("main")).run()
    assert outcome.status == "created", outcome.message
    branch = "toolbot/terraform/2.0.0"
    assert git_setup.show(branch, "bin/terraform") == "tf-2.0.0"
    assert git_setup.show(branch, ".terraform-version") == "2.0.0"
    assert "ARG TERRAFORM_VERSION=2.0.0" in git_setup.show(branch, "Dockerfile")
    mode = git(git_setup.remote, "ls-tree", branch, "bin/terraform").split()[0]
    assert mode == "100755"
    description = target.descriptions[target.requests[0].id]
    assert "terraform_2.0.0_linux_amd64.zip!terraform" in description
    assert "### Version pins" in description and "`.terraform-version`" in description


def test_dry_run_previews_archives_and_version_pins(git_setup: GitSetup, router: Router) -> None:
    terraform_repository(git_setup.work, router)
    commit_all(git_setup.work)
    publish_terraform(router, "2.0.0")
    router.json(f"{TERRAFORM_API}/git/matching-refs/tags/v", [{"ref": "refs/tags/v2.0.0"}])
    repository = Repository(git_setup.url, GitIdentity("Toolbot", "toolbot@example.com"))
    settings = UpdateSettings("main", dry_run=True)
    [outcome] = Updater(make_context(router), FakeTarget(git_setup.url), repository, settings).run()
    assert outcome.status == "would-create"
    assert outcome.details == [
        "✓ terraform_2.0.0_linux_amd64.zip (extract terraform) → /bin/terraform "
        f"[sha256 via {DOWNLOADS}/2.0.0/terraform_2.0.0_SHA256SUMS]",
        "✓ .terraform-version: 1 version pin(s)",
        "✓ Dockerfile: 1 version pin(s)",
    ]
    assert git_setup.branches() == ["main"]
