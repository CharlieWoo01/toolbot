from __future__ import annotations

from pathlib import Path

import pytest

from toolbot.assets.checksums import find_expected_digest
from toolbot.assets.install import PendingFile, replace_files
from toolbot.assets.matching import glob_to_regex, resolve_assets
from toolbot.config.models import AssetConfig, AssetMatcher, ChecksumConfig
from toolbot.errors import ToolbotError
from toolbot.providers.source.base import ReleaseAsset

HEX, HEX2 = "a" * 64, "b" * 64
VALUES = {"version": "1.2.3", "tag": "v1.2.3"}


def asset(asset_id: str, kind: str, template: str) -> AssetConfig:
    return AssetConfig(asset_id, AssetMatcher(kind, template), f"{asset_id}.bin", ChecksumConfig("none"))  # type: ignore[arg-type]


def upstream(*names: str) -> list[ReleaseAsset]:
    return [ReleaseAsset(n, f"https://x/{n}") for n in names]


class TestMatching:
    def test_exact_name_after_substitution(self) -> None:
        [r] = resolve_assets(
            [asset("a", "name", "tool-{version}-linux-amd64.tar.gz")],
            upstream("tool-1.2.3-linux-amd64.tar.gz", "tool-1.2.3-linux-arm64.tar.gz"),
            VALUES,
            "rel",
        )
        assert r.upstream.name == "tool-1.2.3-linux-amd64.tar.gz"

    def test_exact_name_never_matches_similar_files(self) -> None:
        with pytest.raises(ToolbotError) as err:
            resolve_assets([asset("a", "name", "tool.zip")], upstream("tool.zip.sha256", "toolXzip"), VALUES, "rel")
        assert err.value.code == "ASSET_NOT_FOUND" and "tool.zip.sha256, toolXzip" in err.value.message

    def test_glob_matching_exactly_one(self) -> None:
        [r] = resolve_assets(
            [asset("a", "pattern", "tool-*-windows-amd64.zip")],
            upstream("tool-1.2.3+b7-windows-amd64.zip", "tool-1.2.3+b7-windows-amd64.zip.sha256"),
            VALUES,
            "rel",
        )
        assert r.upstream.name == "tool-1.2.3+b7-windows-amd64.zip"

    def test_ambiguous_glob(self) -> None:
        with pytest.raises(ToolbotError) as err:
            resolve_assets(
                [asset("a", "pattern", "tool-*-amd64.zip")],
                upstream("tool-linux-amd64.zip", "tool-windows-amd64.zip"),
                VALUES,
                "rel",
            )
        assert err.value.code == "ASSET_AMBIGUOUS" and "matches 2 assets" in err.value.message

    def test_two_assets_claiming_one_file(self) -> None:
        with pytest.raises(ToolbotError) as err:
            resolve_assets(
                [asset("a", "name", "tool.zip"), asset("b", "pattern", "tool.z?p")], upstream("tool.zip"), VALUES, "rel"
            )
        assert err.value.code == "ASSET_AMBIGUOUS"

    def test_glob_metacharacters_are_literal(self) -> None:
        assert glob_to_regex("a+b(c).zip").fullmatch("a+b(c).zip")
        assert not glob_to_regex("a.zip").fullmatch("abzip")


class TestChecksumFiles:
    def test_bare_digest(self) -> None:
        assert find_expected_digest(f"{HEX.upper()}\n", "x.zip", "sha256", "f") == HEX

    def test_sha256sum_binary_mode_as_published_by_pact(self) -> None:
        assert find_expected_digest(f"{HEX} *pact.zip\n", "pact.zip", "sha256", "f") == HEX

    def test_sha256sums_picks_exact_name(self) -> None:
        content = f"{HEX2}  tool.zip.sig\n{HEX}  tool.zip\n{HEX2}  ./other.zip\n"
        assert find_expected_digest(content, "tool.zip", "sha256", "SHA256SUMS") == HEX
        assert find_expected_digest(content, "other.zip", "sha256", "SHA256SUMS") == HEX2

    def test_bsd_style(self) -> None:
        assert find_expected_digest(f"SHA256 (tool.zip) = {HEX}\n", "tool.zip", "sha256", "f") == HEX

    def test_missing_and_conflicting_entries(self) -> None:
        with pytest.raises(ToolbotError) as missing:
            find_expected_digest(f"{HEX}  a.zip\n", "b.zip", "sha256", "SUMS")
        assert missing.value.code == "CHECKSUM_UNAVAILABLE"
        with pytest.raises(ToolbotError) as conflict:
            find_expected_digest(f"{HEX}  a.zip\n{HEX2}  a.zip\n", "a.zip", "sha256", "SUMS")
        assert conflict.value.code == "CHECKSUM_MISMATCH"

    def test_algorithm_length_is_enforced(self) -> None:
        with pytest.raises(ToolbotError, match="no sha512 entry"):
            find_expected_digest(f"{HEX}\n", "a", "sha512", "f")


class TestAtomicReplacement:
    def test_replaces_and_cleans_up(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.write_text("new")
        (tmp_path / "a").write_text("old")
        replace_files([PendingFile(tmp_path / "a", source_file=src), PendingFile(tmp_path / "b", content="text")])
        assert (tmp_path / "a").read_text() == "new" and (tmp_path / "b").read_text() == "text"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["a", "b", "src"]

    def test_rolls_back_files_already_placed(self, tmp_path: Path) -> None:
        (tmp_path / "a").write_text("old-a")
        (tmp_path / "b").write_text("old-b")
        src = tmp_path / "src"
        src.write_text("new")
        calls = 0

        def fail_third(_dest: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("disk full")

        with pytest.raises(OSError, match="disk full"):
            replace_files(
                [
                    PendingFile(tmp_path / "a", source_file=src),
                    PendingFile(tmp_path / "b", source_file=src),
                    PendingFile(tmp_path / "c", content="new-c"),
                ],
                before_place=fail_third,
            )
        assert (tmp_path / "a").read_text() == "old-a" and (tmp_path / "b").read_text() == "old-b"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["a", "b", "src"]

    def test_cleans_staged_files_when_staging_fails(self, tmp_path: Path) -> None:
        (tmp_path / "a").write_text("old")
        src = tmp_path / "src"
        src.write_text("new")
        with pytest.raises(OSError):
            replace_files(
                [
                    PendingFile(tmp_path / "a", source_file=src),
                    PendingFile(tmp_path / "n", source_file=tmp_path / "missing"),
                ]
            )
        assert (tmp_path / "a").read_text() == "old"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["a", "src"]
