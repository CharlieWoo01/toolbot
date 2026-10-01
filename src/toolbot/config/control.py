"""The control configuration: ``toolbot.yaml``.

Optional. Without it Toolbot manages the single repository it runs in. With it,
a *control repository* (GitOps) describes, in one reviewed place:

``hosts``
    The GitHub/GitLab instances involved (public or internal) and the
    environment variable holding each one's token.
``defaults``
    Settings for every change request (base branch, labels, commit author).
``rules``
    Central version policy per tool and/or repository: disable, ignore
    versions, only allow a range or pin a version, allow prereleases.
``targets``
    Named sets of repositories to maintain: single repositories and whole
    GitLab groups / GitHub organisations. Each schedule runs one set.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from toolbot.config.fields import (
    Problems,
    optional_bool,
    require_string,
    string_list,
    unknown_keys,
)
from toolbot.config.models import ToolDefinition
from toolbot.errors import ConfigValidationError, ToolbotError
from toolbot.hosts import ENVIRONMENT_VARIABLE_NAME, HOST_TYPES, Host, normalise_url
from toolbot.versions import check_constraint

CONTROL_FILE = "toolbot.yaml"

CONTROL_KEYS = ("hosts", "defaults", "rules", "targets")
HOST_KEYS = ("type", "url", "token-env")
DEFAULTS_KEYS = ("base-branch", "labels", "git-author")
GIT_AUTHOR_KEYS = ("name", "email")
RULE_KEYS = ("tools", "repositories", "enabled", "prereleases", "ignore", "allow")
TARGET_KEYS = ("host", "repository", "group", "include", "exclude", "base-branch")

NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
REPOSITORY_PATH = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$")
GROUP_PATH = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
BRANCH_TEXT = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")


@dataclass(frozen=True)
class Rule:
    tools: tuple[str, ...] = ("*",)
    """Globs matched against tool names."""
    repositories: tuple[str, ...] = ("*",)
    """Globs matched against repository paths, e.g. ``platform/*``."""
    enabled: bool | None = None
    prereleases: bool | None = None
    ignore: tuple[str, ...] = ()
    allow: tuple[str, ...] = ()

    def applies_to(self, tool: str, repository: str) -> bool:
        return any(fnmatch.fnmatchcase(tool, pattern) for pattern in self.tools) and any(
            fnmatch.fnmatchcase(repository, pattern) for pattern in self.repositories
        )


@dataclass(frozen=True)
class TargetSpec:
    host: str
    """Host name from ``hosts``."""
    repository: str | None = None
    """One repository, e.g. ``team/ci-tools`` or ``acme/tools``."""
    group: str | None = None
    """All repositories in a GitLab group (incl. subgroups) or a GitHub organisation/user."""
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    base_branch: str | None = None


@dataclass(frozen=True)
class Defaults:
    base_branch: str | None = None
    labels: tuple[str, ...] = ("toolbot", "tool-update")
    git_author_name: str = "Toolbot"
    git_author_email: str = "toolbot@noreply.invalid"


@dataclass(frozen=True)
class ControlConfig:
    hosts: tuple[Host, ...] = ()
    defaults: Defaults = field(default_factory=Defaults)
    rules: tuple[Rule, ...] = ()
    targets: dict[str, tuple[TargetSpec, ...]] = field(default_factory=dict)
    file: Path | None = None


def validate_control(raw: object, source: str) -> ControlConfig:
    problems: Problems = []
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigValidationError(source, ["the file must contain a YAML mapping"])
    unknown_keys(raw, CONTROL_KEYS, "", problems)
    hosts = _validate_hosts(raw.get("hosts", {}), problems)
    host_names = {host.name for host in hosts} | {"github", "gitlab"}
    defaults = _validate_defaults(raw.get("defaults", {}), problems)
    rules = _validate_rules(raw.get("rules", []), problems)
    targets = _validate_targets(raw.get("targets", {}), host_names, problems)
    if problems:
        raise ConfigValidationError(source, problems)
    return ControlConfig(tuple(hosts), defaults, tuple(rules), targets, Path(source))


def _validate_hosts(value: object, problems: Problems) -> list[Host]:
    if not isinstance(value, dict):
        problems.append("hosts: must be a mapping of host name to {type, url, token-env}")
        return []
    hosts: list[Host] = []
    for name, entry in value.items():
        where = f"hosts.{name}"
        if not isinstance(name, str) or not NAME.match(name):
            problems.append(f'{where}: host names must be lowercase letters, digits and "-"')
        if not isinstance(entry, dict):
            problems.append(f"{where}: must be a mapping with type, url and optional token-env")
            continue
        unknown_keys(entry, HOST_KEYS, f"{where}.", problems)
        host_type = entry.get("type")
        if host_type not in HOST_TYPES:
            problems.append(f"{where}.type: must be one of {', '.join(HOST_TYPES)}")
        url_value = require_string(entry, "url", problems, f"{where}.")
        url = normalise_url(url_value) if url_value else None
        if url_value and url is None:
            problems.append(f"{where}.url: must be an https:// URL without credentials, query or fragment")
        token_env = entry.get("token-env")
        if token_env is not None and (not isinstance(token_env, str) or not ENVIRONMENT_VARIABLE_NAME.match(token_env)):
            problems.append(
                f"{where}.token-env: must be the NAME of an environment variable, e.g. INTERNAL_GITLAB_TOKEN "
                "(never the token itself)"
            )
            token_env = None
        if isinstance(name, str) and host_type in HOST_TYPES and url:
            hosts.append(Host(name, str(host_type), url, token_env))
    return hosts


def _validate_defaults(value: object, problems: Problems) -> Defaults:
    if not isinstance(value, dict):
        problems.append("defaults: must be a mapping")
        return Defaults()
    unknown_keys(value, DEFAULTS_KEYS, "defaults.", problems)
    defaults = Defaults()
    if "base-branch" in value:
        branch = value["base-branch"]
        if not isinstance(branch, str) or not BRANCH_TEXT.match(branch):
            problems.append("defaults.base-branch: must be a branch name")
        else:
            defaults = replace(defaults, base_branch=branch)
    if "labels" in value:
        labels = string_list(value["labels"], "defaults.labels", problems, '["toolbot", "tool-update"]')
        if labels is not None:
            defaults = replace(defaults, labels=tuple(labels))
    if "git-author" in value:
        author = value["git-author"]
        if not isinstance(author, dict):
            problems.append("defaults.git-author: must be a mapping with name and email")
        else:
            unknown_keys(author, GIT_AUTHOR_KEYS, "defaults.git-author.", problems)
            name = require_string(author, "name", problems, "defaults.git-author.")
            email = require_string(author, "email", problems, "defaults.git-author.")
            if name and email:
                defaults = replace(defaults, git_author_name=name, git_author_email=email)
    return defaults


def _validate_rules(value: object, problems: Problems) -> list[Rule]:
    if not isinstance(value, list):
        problems.append("rules: must be a list")
        return []
    rules: list[Rule] = []
    for index, entry in enumerate(value):
        where = f"rules[{index}]"
        if not isinstance(entry, dict):
            problems.append(f"{where}: must be a mapping")
            continue
        unknown_keys(entry, RULE_KEYS, f"{where}.", problems)
        tools = string_list(entry.get("tools", ["*"]), f"{where}.tools", problems, '["terraform", "pact-*"]')
        repositories = string_list(
            entry.get("repositories", ["*"]), f"{where}.repositories", problems, '["platform/*"]'
        )
        ignore = string_list(entry.get("ignore", []), f"{where}.ignore", problems, '[">=2.0.0"]') or []
        allow = string_list(entry.get("allow", []), f"{where}.allow", problems, '"<2.0.0"') or []
        for constraint in (*ignore, *allow):
            # Tools may use either versioning scheme; accept what either can parse.
            if check_constraint(constraint, "semver") and check_constraint(constraint, "pep440"):
                problems.append(f'{where}: "{constraint}" is not a valid version constraint')
        rule = Rule(
            tools=tuple(tools or ["*"]),
            repositories=tuple(repositories or ["*"]),
            enabled=optional_bool(entry, "enabled", f"{where}.", problems),
            prereleases=optional_bool(entry, "prereleases", f"{where}.", problems),
            ignore=tuple(ignore),
            allow=tuple(allow),
        )
        if rule.enabled is None and rule.prereleases is None and not rule.ignore and not rule.allow:
            problems.append(f"{where}: sets nothing (use enabled, prereleases, ignore or allow)")
        rules.append(rule)
    return rules


def _validate_targets(value: object, host_names: set[str], problems: Problems) -> dict[str, tuple[TargetSpec, ...]]:
    if not isinstance(value, dict):
        problems.append("targets: must be a mapping of set name to a list of targets")
        return {}
    targets: dict[str, tuple[TargetSpec, ...]] = {}
    for set_name, entries in value.items():
        where_set = f"targets.{set_name}"
        if not isinstance(set_name, str) or not NAME.match(set_name) or set_name == "all":
            problems.append(f'{where_set}: set names must be lowercase letters, digits and "-" (and not "all")')
        if not isinstance(entries, list) or not entries:
            problems.append(f"{where_set}: must be a non-empty list of targets")
            continue
        specs: list[TargetSpec] = []
        for index, entry in enumerate(entries):
            spec = _validate_target(entry, f"{where_set}[{index}]", host_names, problems)
            if spec is not None:
                specs.append(spec)
        targets[str(set_name)] = tuple(specs)
    return targets


def _validate_target(entry: object, where: str, host_names: set[str], problems: Problems) -> TargetSpec | None:
    if not isinstance(entry, dict):
        problems.append(f"{where}: must be a mapping with host and repository or group")
        return None
    unknown_keys(entry, TARGET_KEYS, f"{where}.", problems)
    host = require_string(entry, "host", problems, f"{where}.")
    if host is not None and host not in host_names:
        problems.append(f'{where}.host: unknown host "{host}" (defined: {", ".join(sorted(host_names))})')
    repository, group = entry.get("repository"), entry.get("group")
    if (repository is None) == (group is None):
        problems.append(f'{where}: set exactly one of "repository" or "group"')
        return None
    if repository is not None and (not isinstance(repository, str) or not REPOSITORY_PATH.match(repository)):
        problems.append(f'{where}.repository: must be a path such as "team/ci-tools"')
    if group is not None and (not isinstance(group, (str, int)) or not GROUP_PATH.match(str(group))):
        problems.append(f'{where}.group: must be a group/organisation path such as "platform" or "platform/ci"')
    include = string_list(entry.get("include", []), f"{where}.include", problems, '["platform/ci-*"]') or []
    exclude = string_list(entry.get("exclude", []), f"{where}.exclude", problems, '["platform/legacy-*"]') or []
    if repository is not None and (include or exclude):
        problems.append(f"{where}: include/exclude only apply to a group")
    base_branch = entry.get("base-branch")
    if base_branch is not None and (not isinstance(base_branch, str) or not BRANCH_TEXT.match(base_branch)):
        problems.append(f"{where}.base-branch: must be a branch name")
        base_branch = None
    if host is None:
        return None
    return TargetSpec(
        host=host,
        repository=str(repository) if repository is not None else None,
        group=str(group) if group is not None else None,
        include=tuple(include),
        exclude=tuple(exclude),
        base_branch=base_branch,
    )


def load_control(path: Path) -> ControlConfig:
    from toolbot.config.loader import parse_yaml

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise ToolbotError("CONFIG_NOT_FOUND", f"control configuration {path} not found") from None
    return validate_control(parse_yaml(text, str(path)), str(path))


def apply_rules(tool: ToolDefinition, rules: tuple[Rule, ...], repository: str) -> ToolDefinition | None:
    """The tool with central rules applied, or None if a rule disables it.

    Rules are applied in order; later rules win for ``enabled`` and
    ``prereleases``; ``ignore`` and ``allow`` accumulate (every ``allow``
    constraint must hold).
    """
    enabled = True
    prereleases = tool.prereleases
    ignore = list(tool.ignore)
    allow = list(tool.allow)
    for rule in rules:
        if not rule.applies_to(tool.name, repository):
            continue
        if rule.enabled is not None:
            enabled = rule.enabled
        if rule.prereleases is not None:
            prereleases = rule.prereleases
        ignore.extend(rule.ignore)
        allow.extend(rule.allow)
    for constraint in (*ignore, *allow):
        problem = check_constraint(constraint, tool.versioning)
        if problem:
            raise ToolbotError("CONFIG_INVALID", f"{tool.name}: rule constraint {problem}")
    if not enabled:
        return None
    return replace(tool, prereleases=prereleases, ignore=tuple(ignore), allow=tuple(allow))


def select_targets(config: ControlConfig, set_name: str | None) -> list[tuple[str, TargetSpec]]:
    """Targets of one named set, or of every set for ``all``."""
    if not config.targets:
        raise ToolbotError("USAGE", f"{config.file or CONTROL_FILE} defines no targets")
    if set_name is None:
        if len(config.targets) == 1:
            set_name = next(iter(config.targets))
        else:
            raise ToolbotError("USAGE", f"choose a target set with --targets ({', '.join([*config.targets, 'all'])})")
    if set_name == "all":
        return [(name, spec) for name, specs in config.targets.items() for spec in specs]
    if set_name not in config.targets:
        raise ToolbotError("USAGE", f'unknown target set "{set_name}" (defined: {", ".join([*config.targets, "all"])})')
    return [(set_name, spec) for spec in config.targets[set_name]]
