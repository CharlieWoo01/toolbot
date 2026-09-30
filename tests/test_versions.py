from __future__ import annotations

import pytest

from toolbot.errors import ToolbotError
from toolbot.versions import check_constraint, matches, parse_version, tag_for, version_from_tag


def v(text: str, scheme: str = "semver"):  # type: ignore[no-untyped-def]
    return parse_version(text, scheme)


class TestSemver:
    def test_numeric_ordering_not_lexical(self) -> None:
        assert v("1.9.0") < v("1.10.0")
        assert v("1.10.0") < v("2.0.0")
        assert v("0.8.6") < v("0.10.0")

    def test_prerelease_precedence(self) -> None:
        ordered = [
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-beta.1",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0",
        ]
        parsed = [v(x) for x in ordered]
        assert parsed == sorted(parsed)

    def test_prerelease_flag(self) -> None:
        assert v("1.0.0-rc.1").prerelease
        assert not v("1.0.0").prerelease

    def test_leading_v_is_tolerated(self) -> None:
        assert v("v1.2.3") == v("1.2.3")

    def test_missing_minor_patch_is_accepted(self) -> None:
        assert v("1.2") == v("1.2.0")

    def test_build_metadata_does_not_affect_precedence(self) -> None:
        assert v("1.2.3+build.5") == v("1.2.3")

    @pytest.mark.parametrize("text", ["latest", "1.2.3.4", "one", "1..2", "", "1.2.3;rm -rf /"])
    def test_invalid(self, text: str) -> None:
        with pytest.raises(ToolbotError) as err:
            v(text)
        assert err.value.code == "VERSION_INVALID"


class TestPep440:
    def test_four_part_versions(self) -> None:
        assert v("2024.1.3.1", "pep440") < v("2024.1.10", "pep440")

    def test_prerelease(self) -> None:
        assert v("1.0.0rc1", "pep440").prerelease
        assert v("1.0.0rc1", "pep440") < v("1.0.0", "pep440")


class TestTags:
    @pytest.mark.parametrize(
        ("template", "tag", "expected"),
        [
            ("{version}", "1.2.3", "1.2.3"),
            ("v{version}", "v1.2.3", "1.2.3"),
            ("release-{version}", "release-1.2.3", "1.2.3"),
            ("cli-v{version}", "cli-v1.2.3", "1.2.3"),
            ("cli/v{version}", "cli/v1.2.3", "1.2.3"),
            ("v{version}.final", "v1.2.3.final", "1.2.3"),
        ],
    )
    def test_version_from_tag(self, template: str, tag: str, expected: str) -> None:
        assert version_from_tag(tag, template) == expected
        assert tag_for(expected, template) == tag

    @pytest.mark.parametrize(
        ("template", "tag"), [("v{version}", "1.2.3"), ("cli-v{version}", "server-v1.2.3"), ("v{version}", "v")]
    )
    def test_non_matching_tags(self, template: str, tag: str) -> None:
        assert version_from_tag(tag, template) is None


class TestConstraints:
    def test_exact_and_ranges(self) -> None:
        assert matches(v("0.9.0"), "0.9.0")
        assert not matches(v("0.9.1"), "0.9.0")
        assert matches(v("2.1.0"), ">=2.0.0")
        assert matches(v("1.4.7"), ">=1.4, <1.5")
        assert not matches(v("1.5.0"), ">=1.4, <1.5")
        assert matches(v("1.0.0"), "!=2.0.0")

    @pytest.mark.parametrize("spec", ["", "=>1.0", ">=", "1.0,,2.0", "~1.0"])
    def test_invalid_constraints(self, spec: str) -> None:
        assert check_constraint(spec, "semver") is not None
