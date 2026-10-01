from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from toolbot.config.control import (
    ControlConfig,
    Rule,
    TargetSpec,
    apply_rules,
    load_control,
    select_targets,
    validate_control,
)
from toolbot.config.models import AssetConfig, AssetMatcher, ChecksumConfig, SourceConfig, ToolDefinition
from toolbot.errors import ConfigValidationError, ToolbotError

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "control-repo" / "toolbot.yaml"


def tool(name: str = "terraform", **changes: Any) -> ToolDefinition:
    definition = ToolDefinition(
        name=name,
        version="1.2.0",
        source=SourceConfig("github", "hashicorp/terraform", "https://github.com"),
        tag="v{version}",
        versioning="semver",
        prereleases=False,
        ignore=("1.3.0",),
        allow=(),
        assets=(AssetConfig("a", AssetMatcher("name", "a.zip"), "a.zip", ChecksumConfig("none")),),
        file=Path("tool.yaml"),
        dir=Path("."),
    )
    return definition.__class__(**{**definition.__dict__, **changes})


def problems_for(raw: object) -> list[str]:
    with pytest.raises(ConfigValidationError) as error:
        validate_control(raw, "toolbot.yaml")
    return error.value.problems


def test_example_control_repository_is_valid() -> None:
    control = load_control(EXAMPLE)
    assert {host.name for host in control.hosts} >= {"github", "internal-gitlab"}
    assert set(control.targets) >= {"nightly", "weekly"}
    assert control.rules


def test_full_configuration() -> None:
    control = validate_control(
        {
            "hosts": {
                "internal": {"type": "gitlab", "url": "https://gitlab.example.com/", "token-env": "INTERNAL_TOKEN"},
                "ghe": {"type": "github", "url": "https://ghe.example.com"},
            },
            "defaults": {
                "base-branch": "develop",
                "labels": ["deps"],
                "git-author": {"name": "Tool Bot", "email": "bot@example.com"},
            },
            "rules": [{"tools": ["terraform"], "repositories": ["platform/*"], "allow": "<2.0.0"}],
            "targets": {
                "nightly": [
                    {"host": "internal", "group": "platform", "include": ["platform/ci-*"], "base-branch": "main"},
                    {"host": "ghe", "repository": "acme/tools"},
                ]
            },
        },
        "toolbot.yaml",
    )
    assert control.hosts[0].url == "https://gitlab.example.com"
    assert control.hosts[1].token_env is None
    assert control.defaults.base_branch == "develop" and control.defaults.labels == ("deps",)
    assert control.defaults.git_author_email == "bot@example.com"
    assert control.rules[0].allow == ("<2.0.0",)
    assert control.targets["nightly"][0] == TargetSpec(
        "internal", group="platform", include=("platform/ci-*",), base_branch="main"
    )


def test_empty_file_is_valid() -> None:
    assert validate_control(None, "toolbot.yaml") == ControlConfig(file=Path("toolbot.yaml"))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"hosts": {"x": {"type": "bitbucket", "url": "https://b.example"}}}, "type: must be one of github, gitlab"),
        ({"hosts": {"x": {"type": "gitlab", "url": "http://g.example"}}}, "https://"),
        (
            {"hosts": {"x": {"type": "gitlab", "url": "https://g.example", "token-env": "glpat-abc123"}}},
            "NAME of an environment variable",
        ),
        ({"hosts": {"Bad Name": {"type": "gitlab", "url": "https://g.example"}}}, "host names must be"),
        ({"rules": [{"tools": ["x"]}]}, "sets nothing"),
        ({"rules": [{"ignore": ["~1.0"]}]}, "not a valid version constraint"),
        ({"rules": [{"enabled": "no"}]}, "enabled: must be true or false"),
        ({"targets": {"nightly": [{"host": "nowhere", "repository": "a/b"}]}}, 'unknown host "nowhere"'),
        (
            {"targets": {"nightly": [{"host": "github", "repository": "a/b", "group": "a"}]}},
            'exactly one of "repository" or "group"',
        ),
        (
            {"targets": {"nightly": [{"host": "github", "repository": "a/b", "include": ["x"]}]}},
            "only apply to a group",
        ),
        ({"targets": {"all": [{"host": "github", "group": "acme"}]}}, 'not "all"'),
        ({"targets": {"nightly": []}}, "non-empty list"),
        ({"targetz": {}}, "targetz: unknown key"),
    ],
)
def test_validation_errors(raw: object, expected: str) -> None:
    problems = problems_for(raw)
    assert any(re.search(re.escape(expected), problem) for problem in problems), problems


class TestRules:
    def test_rules_for_other_tools_or_repositories_do_not_apply(self) -> None:
        rules = (Rule(tools=("helm",), enabled=False), Rule(repositories=("legacy/*",), enabled=False))
        assert apply_rules(tool(), rules, "platform/ci") == tool()

    def test_disable(self) -> None:
        assert apply_rules(tool(), (Rule(tools=("terra*",), enabled=False),), "platform/ci") is None

    def test_later_rules_win_and_constraints_accumulate(self) -> None:
        rules = (
            Rule(enabled=False, repositories=("legacy/*",)),
            Rule(tools=("*",), prereleases=True, ignore=(">=3.0.0",)),
            Rule(tools=("terraform",), prereleases=False, allow=("<2.0.0",)),
        )
        applied = apply_rules(tool(), rules, "platform/ci")
        assert applied is not None
        assert applied.prereleases is False
        assert applied.ignore == ("1.3.0", ">=3.0.0")
        assert applied.allow == ("<2.0.0",)
        assert apply_rules(tool(), rules, "legacy/app") is None

    def test_constraint_invalid_for_the_tools_scheme(self) -> None:
        with pytest.raises(ToolbotError, match="rule constraint"):
            apply_rules(tool(), (Rule(allow=("<2024.1.3.1",)),), "x")


class TestTargetSelection:
    CONTROL = ControlConfig(
        targets={
            "nightly": (TargetSpec("github", repository="acme/a"),),
            "weekly": (TargetSpec("github", repository="acme/b"),),
        }
    )

    def test_choose_a_set(self) -> None:
        assert [spec.repository for _, spec in select_targets(self.CONTROL, "weekly")] == ["acme/b"]
        assert [name for name, _ in select_targets(self.CONTROL, "all")] == ["nightly", "weekly"]

    def test_a_single_set_is_the_default(self) -> None:
        single = ControlConfig(targets={"only": (TargetSpec("github", repository="acme/a"),)})
        assert [name for name, _ in select_targets(single, None)] == ["only"]

    def test_ambiguous_or_unknown_sets(self) -> None:
        with pytest.raises(ToolbotError, match="choose a target set"):
            select_targets(self.CONTROL, None)
        with pytest.raises(ToolbotError, match='unknown target set "hourly"'):
            select_targets(self.CONTROL, "hourly")
        with pytest.raises(ToolbotError, match="defines no targets"):
            select_targets(ControlConfig(), None)
