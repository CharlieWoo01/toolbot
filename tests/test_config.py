from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from conftest import write_tool

from toolbot.config.control import ControlConfig
from toolbot.config.loader import list_tool_names, load_all, load_tool, parse_yaml, with_version
from toolbot.config.validate import validate_definition
from toolbot.errors import ConfigValidationError, ToolbotError
from toolbot.hosts import Host
from toolbot.http import HttpClient
from toolbot.providers.source import create_source_providers
from toolbot.services.context import build_host_registry

REPO = Path(__file__).resolve().parent.parent
INTERNAL_GITLAB = Host("internal", "gitlab", "https://gitlab.example.com", "INTERNAL_TOKEN")
PROVIDERS = create_source_providers(HttpClient(), build_host_registry(ControlConfig(hosts=(INTERNAL_GITLAB,)), {}))

VALID: dict[str, Any] = {
    "name": "widget",
    "version": "1.2.3",
    "source": {"provider": "github", "repository": "acme/widget"},
    "assets": {"linux-amd64": {"name": "widget-linux-amd64.tar.gz", "destination": "widget.tar.gz"}},
}


def with_changes(**changes: Any) -> dict[str, Any]:
    raw = {**VALID, **changes}
    return {key: value for key, value in raw.items() if value is not None}


def validate(raw: object):  # type: ignore[no-untyped-def]
    return validate_definition(
        raw, Path("tool.yaml"), Path("/repo/tools/widget"), PROVIDERS, PROVIDERS.hosts, expected_name="widget"
    )


def problems_for(raw: object) -> list[str]:
    with pytest.raises(ConfigValidationError) as error:
        validate(raw)
    return error.value.problems


def test_minimal_definition_defaults() -> None:
    tool = validate(VALID)
    assert tool.tag == "{version}"
    assert tool.versioning == "semver"
    assert tool.prereleases is False
    assert tool.ignore == () and tool.allow == ()
    assert tool.source.host == "https://github.com"
    assert tool.assets[0].checksum.type == "none"


def test_real_definitions_load() -> None:
    pact = load_tool(REPO, "pact-broker-cli", PROVIDERS)
    assert pact.source.provider == "github"
    assert pact.tag == "v{version}"
    assert pact.assets[0].checksum.file == "{asset}.sha256"
    glab = load_tool(REPO / "examples", "glab", PROVIDERS)
    assert glab.source.provider == "gitlab"
    assert [asset.id for asset in glab.assets] == ["linux-amd64", "windows-amd64"]


def test_every_example_definition_is_valid() -> None:
    loaded = load_all(REPO / "examples", PROVIDERS)
    assert loaded.errors == {}
    assert {tool.name for tool in loaded.tools} >= {
        "glab",
        "pact-cli",
        "pact-verifier-cli",
        "gitleaks",
        "trivy",
        "terraform",
        "helm",
        "kubectl",
    }


def test_prereleases_ignore_and_allow() -> None:
    tool = validate(with_changes(prereleases=True, ignore=["0.9.0", ">=2.0.0"], allow="<3.0.0"))
    assert tool.prereleases is True
    assert tool.ignore == ("0.9.0", ">=2.0.0")
    assert tool.allow == ("<3.0.0",)


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
    assert [asset.checksum.type for asset in tool.assets] == ["file", "none"]


def test_download_urls_for_tools_published_outside_releases() -> None:
    tool = validate(
        with_changes(
            source={"provider": "github", "repository": "hashicorp/terraform"},
            checksum={
                "type": "file",
                "url": "https://releases.hashicorp.com/terraform/{version}/terraform_{version}_SHA256SUMS",
            },
            assets={
                "linux-amd64": {
                    "url": "https://releases.hashicorp.com/terraform/{version}/terraform_{version}_linux_amd64.zip",
                    "destination": "terraform.zip",
                }
            },
        )
    )
    assert tool.assets[0].match.kind == "url"
    assert tool.assets[0].checksum.url.endswith("_SHA256SUMS")


def test_archives_repository_files_tags_and_version_pins() -> None:
    tool = validate(
        with_changes(
            source={"provider": "gitlab", "repository": "platform/cli", "versions": "tags"},
            checksum={"type": "file", "path": "dist/SHA256SUMS"},
            assets={
                "zip": {"url": "https://example.com/{version}/cli.zip", "extract": "./cli", "destination": "/bin/cli"},
                "zip-docs": {
                    "url": "https://example.com/{version}/cli.zip",
                    "extract": "README.md",
                    "destination": "README.md",
                },
                "committed": {"path": "dist/linux/cli-{version}", "destination": "cli-from-repo"},
            },
            **{"version-files": [{"path": "/.cli-version", "template": "{version}"}]},
        )
    )
    assert tool.source.versions == "tags"
    zip_asset, docs, committed = tool.assets
    assert (zip_asset.extract, zip_asset.destination, zip_asset.executable) == ("cli", "/bin/cli", True)
    assert docs.executable is False  # has an extension
    assert committed.match.kind == "path"
    assert zip_asset.checksum.path == "dist/SHA256SUMS"
    assert [(pin.path, pin.template) for pin in tool.version_files] == [("/.cli-version", "{version}")]


def test_version_files_alone_are_enough() -> None:
    tool = validate(
        with_changes(assets=None, **{"version-files": [{"path": "/.terraform-version", "template": "{version}"}]})
    )
    assert tool.assets == ()


def test_two_tools_cannot_commit_the_same_root_file(tmp_path: Path) -> None:
    for name in ("one", "two"):
        write_tool(
            tmp_path,
            name,
            f'name: {name}\nversion: "1.0.0"\nsource: {{ provider: github, repository: acme/{name} }}\n'
            "assets:\n  a: { name: a, destination: /bin/shared }\n",
        )
    loaded = load_all(tmp_path, PROVIDERS)
    assert [tool.name for tool in loaded.tools] == ["one"]
    assert 'also written by tool "one"' in loaded.errors["two"].message


def test_self_hosted_host_url_is_normalised() -> None:
    tool = validate(
        with_changes(source={"provider": "gitlab", "repository": "a/b/c", "host": "https://git.example.com/gitlab/"})
    )
    assert tool.source.host == "https://git.example.com/gitlab"


def test_host_name_from_toolbot_yaml() -> None:
    tool = validate(with_changes(source={"host": "internal", "repository": "platform/cli"}))
    assert (tool.source.provider, tool.source.host) == ("gitlab", "https://gitlab.example.com")


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
        (
            "host type mismatch",
            with_changes(source={"provider": "github", "host": "internal", "repository": "a/b"}),
            "is a gitlab host",
        ),
        (
            "unknown host name",
            with_changes(source={"provider": "gitlab", "host": "elsewhere", "repository": "a/b"}),
            "must be a host name",
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
        ("absolute path", with_changes(assets={"a": {"name": "a", "destination": "//etc/passwd"}}), 'single "/"'),
        ("drive letter", with_changes(assets={"a": {"name": "a", "destination": "C:/x"}}), 'single "/"'),
        ("root traversal", with_changes(assets={"a": {"name": "a", "destination": "/../x"}}), '".."'),
        ("other tool", with_changes(assets={"a": {"name": "a", "destination": "/tools/other/x"}}), "tools/"),
        ("git dir", with_changes(assets={"a": {"name": "a", "destination": "/.git/hooks/x"}}), "into .git"),
        ("bad extract", with_changes(assets={"a": {"name": "a", "destination": "a", "extract": "../x"}}), "extract"),
        ("bad path", with_changes(assets={"a": {"path": "/abs", "destination": "a"}}), "relative path"),
        (
            "tags need url or path",
            with_changes(source={"provider": "github", "repository": "a/b", "versions": "tags"}),
            'source.versions is "tags"',
        ),
        ("bad versions", with_changes(source={"provider": "github", "repository": "a/b", "versions": "x"}), "versions"),
        (
            "version file needs placeholder",
            with_changes(**{"version-files": [{"path": "x", "template": "a"}]}),
            "{version}",
        ),
        ("no assets", with_changes(assets=None), "or version-files"),
        ("backslash", with_changes(assets={"a": {"name": "a", "destination": "a\\b"}}), "separator"),
        ("hidden file", with_changes(assets={"a": {"name": "a", "destination": ".env"}}), "each path segment"),
        (
            "git dir in version file",
            with_changes(**{"version-files": [{"path": "/.git/config", "template": "{version}"}]}),
            "into .git",
        ),
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
        ("checksum source missing", with_changes(checksum={"type": "file"}), 'needs exactly one of "file"'),
        (
            "checksum file and url",
            with_changes(checksum={"type": "file", "file": "S", "url": "https://x.example/S"}),
            'needs exactly one of "file"',
        ),
        ("checksum url not https", with_changes(checksum={"type": "file", "url": "http://x.example/S"}), "https://"),
        (
            "provider digest on gitlab",
            with_changes(source={"provider": "gitlab", "repository": "a/b"}, checksum={"type": "provider"}),
            "needs digests published with release assets",
        ),
        (
            "provider digest for url asset",
            with_changes(
                checksum={"type": "provider"},
                assets={"a": {"url": "https://x.example/{version}/a.zip", "destination": "a.zip"}},
            ),
            "only works for release assets",
        ),
        (
            "asset url not https",
            with_changes(assets={"a": {"url": "http://x.example/a.zip", "destination": "a.zip"}}),
            "https://",
        ),
        (
            "asset url placeholder typo",
            with_changes(assets={"a": {"url": "https://x.example/{verison}/a.zip", "destination": "a.zip"}}),
            "unknown placeholder",
        ),
        ("misspelt key", with_changes(checksums={"type": "none"}), "checksums: unknown key"),
        (
            "name and pattern",
            with_changes(assets={"a": {"name": "a", "pattern": "a*", "destination": "a"}}),
            'set only one of "name", "pattern", "url" or "path"',
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
        ("bad allow", with_changes(allow=["~1.0"]), "allow:"),
        ("ignore not list", with_changes(ignore=5), "must be a list"),
    ],
)
def test_validation_errors(label: str, raw: object, expected: str) -> None:
    problems = problems_for(raw)
    assert any(re.search(expected, problem) for problem in problems), f"{label}: {problems}"


def test_duplicate_and_nested_destinations() -> None:
    duplicates = problems_for(
        with_changes(
            assets={
                "a": {"name": "a.zip", "destination": "Tool.zip"},
                "b": {"name": "b.zip", "destination": "tool.zip"},
            }
        )
    )
    assert any("duplicate destination" in problem for problem in duplicates)
    nested = problems_for(
        with_changes(
            assets={"a": {"name": "a.zip", "destination": "bin"}, "b": {"name": "b.zip", "destination": "bin/b.zip"}}
        )
    )
    assert any("one is inside the other" in problem for problem in nested)


def test_identical_matchers_are_ambiguous() -> None:
    problems = problems_for(
        with_changes(
            assets={"a": {"name": "x.zip", "destination": "a.zip"}, "b": {"name": "x.zip", "destination": "b.zip"}}
        )
    )
    assert any("ambiguous" in problem for problem in problems)


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
    assert [tool.name for tool in loaded.tools] == ["good"]
    assert "sourceforge" in loaded.errors["bad"].message
    only = load_all(tmp_path, PROVIDERS, ["good", "missing"])
    assert [tool.name for tool in only.tools] == ["good"]
    assert only.errors["missing"].code == "CONFIG_NOT_FOUND"


def test_unknown_tool(tmp_path: Path) -> None:
    with pytest.raises(ToolbotError) as error:
        load_tool(tmp_path, "missing", PROVIDERS)
    assert error.value.code == "CONFIG_NOT_FOUND"


def test_with_version_preserves_layout() -> None:
    text = '# header\nname: widget\nversion: "0.8.6"   # current\ntag: v{version}\nassets:\n  a:\n    version: nested\n'
    assert with_version(text, "f", "0.9.0") == text.replace('"0.8.6"', '"0.9.0"')
    assert with_version("version: 1.2.3\n", "f", "1.3.0") == 'version: "1.3.0"\n'
