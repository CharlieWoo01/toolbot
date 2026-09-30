from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import write_tool

from toolbot.config.loader import list_tool_names, load_all, load_tool, parse_yaml, with_version
from toolbot.config.validate import validate_definition
from toolbot.errors import ConfigValidationError, ToolbotError
from toolbot.http import HttpClient
from toolbot.providers.source import create_source_providers

REPO = Path(__file__).resolve().parent.parent
PROVIDERS = create_source_providers(HttpClient(), {})

VALID: dict[str, Any] = {
    "name": "widget",
    "version": "1.2.3",
    "source": {"provider": "github", "repository": "acme/widget"},
    "assets": {"linux-amd64": {"name": "widget-linux-amd64.tar.gz", "destination": "widget.tar.gz"}},
}


def with_changes(**changes: Any) -> dict[str, Any]:
    raw = {**VALID, **changes}
    return {k: v for k, v in raw.items() if v is not None}


def validate(raw: object):  # type: ignore[no-untyped-def]
    return validate_definition(raw, Path("tool.yaml"), Path("/repo/tools/widget"), PROVIDERS, "widget")


def problems_for(raw: object) -> list[str]:
    with pytest.raises(ConfigValidationError) as err:
        validate(raw)
    return err.value.problems


def test_minimal_definition_defaults() -> None:
    tool = validate(VALID)
    assert tool.tag == "{version}"
    assert tool.versioning == "semver"
    assert tool.prereleases is False
    assert tool.ignore == ()
    assert tool.source.host == "https://github.com"
    assert tool.assets[0].checksum.type == "none"


def test_real_definitions_load() -> None:
    pact = load_tool(REPO, "pact-broker-cli", PROVIDERS)
    assert pact.source.provider == "github"
    assert pact.tag == "v{version}"
    assert pact.assets[0].checksum.file == "{asset}.sha256"
    glab = load_tool(REPO / "examples", "glab", PROVIDERS)
    assert glab.source.provider == "gitlab"
    assert [a.id for a in glab.assets] == ["linux-amd64", "windows-amd64"]


def test_prereleases_and_ignore() -> None:
    tool = validate(with_changes(prereleases=True, ignore=["0.9.0", ">=2.0.0"]))
    assert tool.prereleases is True
    assert tool.ignore == ("0.9.0", ">=2.0.0")


def test_per_asset_checksum_overrides_default() -> None:
    tool = validate(
        with_changes(
            checksum={"type": "file", "file": "SHA256SUMS"},
            assets={
                "a": {"name": "a.zip", "destination": "a.zip"},
                "b": {"name": "b.zip", "destination": "b.zip", "checksum": {"type": "none"}},
            },
        )
    )
    assert [a.checksum.type for a in tool.assets] == ["file", "none"]


def test_self_hosted_host_is_normalised() -> None:
    tool = validate(
        with_changes(source={"provider": "gitlab", "repository": "a/b/c", "host": "https://git.example.com/gitlab/"})
    )
    assert tool.source.host == "https://git.example.com/gitlab"


@pytest.mark.parametrize(
    ("label", "raw", "expected"),
    [
        (
            "unknown provider",
            with_changes(source={"provider": "bitbucket", "repository": "a/b"}),
            r'unknown provider "bitbucket" \(supported: github, gitlab\)',
        ),
        ("missing repository", with_changes(source={"provider": "github"}), r"source\.repository: is required"),
        (
            "bad github repository",
            with_changes(source={"provider": "github", "repository": "a/b/c"}),
            'must be "owner/repository"',
        ),
        ("missing version", with_changes(version=None), r"version: is required"),
        ("numeric version", with_changes(version=1.1), "must be a quoted string"),
        ("unparseable version", with_changes(version="latest"), "not a valid"),
        ("no assets", with_changes(assets={}), "at least one asset is required"),
        (
            "path traversal",
            with_changes(assets={"a": {"name": "a", "destination": "../../etc/passwd"}}),
            r'must not contain "\." or "\.\." segments',
        ),
        ("absolute path", with_changes(assets={"a": {"name": "a", "destination": "/etc/passwd"}}), "not absolute"),
        ("drive letter", with_changes(assets={"a": {"name": "a", "destination": "C:/x"}}), "not absolute"),
        ("backslash", with_changes(assets={"a": {"name": "a", "destination": "a\\b"}}), "separator"),
        ("hidden file", with_changes(assets={"a": {"name": "a", "destination": ".git/config"}}), "each path segment"),
        (
            "overwrite definition",
            with_changes(assets={"a": {"name": "a", "destination": "tool.yaml"}}),
            "must not overwrite tool.yaml",
        ),
        ("tag without version", with_changes(tag="latest"), "exactly once"),
        ("tag typo", with_changes(tag="v{verison}"), r'unknown placeholder "\{verison\}"'),
        ("unquoted tag", with_changes(tag={"version": None}), "must be quoted in YAML"),
        ("tag shell chars", with_changes(tag="v{version};rm"), "may only contain"),
        ("unknown checksum type", with_changes(checksum={"type": "md5"}), 'unsupported checksum type "md5"'),
        (
            "unknown algorithm",
            with_changes(checksum={"type": "file", "file": "S", "algorithm": "md5"}),
            'unsupported "md5"',
        ),
        ("checksum file missing", with_changes(checksum={"type": "file"}), r"checksum\.file: is required"),
        (
            "provider digest on gitlab",
            with_changes(source={"provider": "gitlab", "repository": "a/b"}, checksum={"type": "provider"}),
            "not supported by this source provider",
        ),
        ("misspelt key", with_changes(checksums={"type": "none"}), "checksums: unknown key"),
        (
            "name and pattern",
            with_changes(assets={"a": {"name": "a", "pattern": "a*", "destination": "a"}}),
            'either "name" or "pattern"',
        ),
        ("too broad", with_changes(assets={"a": {"pattern": "*", "destination": "a"}}), "too broad"),
        (
            "pattern without wildcard",
            with_changes(assets={"a": {"pattern": "tool.zip", "destination": "a"}}),
            'use "name"',
        ),
        (
            "http host",
            with_changes(source={"provider": "github", "repository": "a/b", "host": "http://ghe"}),
            "https://",
        ),
        ("name mismatch", with_changes(name="other"), 'must match its directory name "widget"'),
        ("bad versioning", with_changes(versioning="loose"), "versioning: must be one of"),
        ("bad prereleases", with_changes(prereleases="yes"), "prereleases: must be true or false"),
        ("bad ignore", with_changes(ignore=[">=banana"]), "ignore:"),
        ("ignore not list", with_changes(ignore="0.9.0"), "must be a list"),
    ],
)
def test_validation_errors(label: str, raw: object, expected: str) -> None:
    import re

    problems = problems_for(raw)
    assert any(re.search(expected, p) for p in problems), f"{label}: {problems}"


def test_duplicate_and_nested_destinations() -> None:
    dup = problems_for(
        with_changes(
            assets={
                "a": {"name": "a.zip", "destination": "Tool.zip"},
                "b": {"name": "b.zip", "destination": "tool.zip"},
            }
        )
    )
    assert any("duplicate destination" in p for p in dup)
    nested = problems_for(
        with_changes(
            assets={"a": {"name": "a.zip", "destination": "bin"}, "b": {"name": "b.zip", "destination": "bin/b.zip"}}
        )
    )
    assert any("one is inside the other" in p for p in nested)


def test_identical_matchers_are_ambiguous() -> None:
    problems = problems_for(
        with_changes(
            assets={"a": {"name": "x.zip", "destination": "a.zip"}, "b": {"name": "x.zip", "destination": "b.zip"}}
        )
    )
    assert any("ambiguous" in p for p in problems)


def test_reports_every_problem() -> None:
    assert len(problems_for({"name": "widget", "source": {"provider": "nope"}, "assets": {}})) >= 3


def test_yaml_aliases_and_duplicate_keys_rejected() -> None:
    with pytest.raises(ConfigValidationError, match="aliases"):
        parse_yaml("a: &x 1\nb: *x\n", "f")
    with pytest.raises(ConfigValidationError, match="duplicate key"):
        parse_yaml("a: 1\na: 2\n", "f")


def test_yaml_is_safe() -> None:
    with pytest.raises(ConfigValidationError):
        parse_yaml("a: !!python/object/apply:os.system ['echo hi']\n", "f")


def test_load_all_isolates_failures(tmp_path: Path) -> None:
    write_tool(
        tmp_path,
        "good",
        'name: good\nversion: "1.0.0"\nsource: {provider: github, repository: a/b}\nassets: {x: {name: x, destination: x}}\n',
    )
    write_tool(
        tmp_path,
        "bad",
        'name: bad\nversion: "1.0.0"\nsource: {provider: sourceforge, repository: a/b}\nassets: {x: {name: x, destination: x}}\n',
    )
    assert list_tool_names(tmp_path) == ["bad", "good"]
    loaded = load_all(tmp_path, PROVIDERS)
    assert [t.name for t in loaded.tools] == ["good"]
    assert "sourceforge" in loaded.errors["bad"].message
    only = load_all(tmp_path, PROVIDERS, ["good", "missing"])
    assert [t.name for t in only.tools] == ["good"]
    assert only.errors["missing"].code == "CONFIG_NOT_FOUND"


def test_unknown_tool(tmp_path: Path) -> None:
    with pytest.raises(ToolbotError) as err:
        load_tool(tmp_path, "missing", PROVIDERS)
    assert err.value.code == "CONFIG_NOT_FOUND"


def test_with_version_preserves_layout() -> None:
    text = '# header\nname: widget\nversion: "0.8.6"   # current\ntag: v{version}\nassets:\n  a:\n    version: nested\n'
    assert with_version(text, "f", "0.9.0") == text.replace('"0.8.6"', '"0.9.0"')
    assert with_version("version: 1.2.3\n", "f", "1.3.0") == 'version: "1.3.0"\n'
