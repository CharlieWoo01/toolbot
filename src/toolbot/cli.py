"""Command-line interface.

Read-only:   list · validate · verify · outdated · targets
Local:       update   (fetch/verify/install into the working tree; no Git, no change request)
Automation:  check    (full lifecycle: branch, commit, push, change request; --dry-run to preview)

``check`` maintains either the repositories listed under ``targets`` in
toolbot.yaml (control repository / GitOps), or, without targets, the single
repository described by the CI environment.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from toolbot import __version__
from toolbot.config.control import CONTROL_FILE, ControlConfig, apply_rules, load_control, select_targets
from toolbot.config.loader import load_all
from toolbot.errors import ToolbotError
from toolbot.services.artifacts import apply_version
from toolbot.services.context import Context, create_context
from toolbot.services.discovery import discover
from toolbot.services.fetch import fetch_tool
from toolbot.services.runner import RepositoryResult, check_repositories
from toolbot.services.targets import TargetRepository, environment_target, resolve_targets
from toolbot.services.updater import FAILURE_STATUSES, Outcome, plan_local
from toolbot.services.verify import verify_tool

EXIT_OK, EXIT_FAILURES, EXIT_USAGE = 0, 1, 2

HEADLINES = {
    "would-create": "Update available.",
    "created": "Update proposed.",
    "superseded": "Update proposed, superseding the previous one.",
    "exists": "Update already proposed.",
    "up-to-date": "Up to date.",
    "ahead": "Ahead of upstream.",
    "no-releases": "No acceptable releases.",
    "disabled": "Disabled.",
    "rejected": "Skipped: version was rejected.",
    "reverted": "Skipped: update was merged and reverted.",
    "blocked": "Blocked: needs attention.",
    "failed": "FAILED.",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="toolbot",
        description="Keeps standalone tools and binaries up to date with automated merge/pull requests.",
    )
    parser.add_argument("--version", action="version", version=f"toolbot {__version__}")
    parser.add_argument("--root", type=Path, default=Path("."), help="repository root containing tools/ (default: .)")
    parser.add_argument(
        "--config",
        type=Path,
        help=f"control configuration (default: {CONTROL_FILE} in --root, if present)",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    list_command = commands.add_parser("list", help="list configured tools")
    list_command.add_argument("--json", action="store_true", help="machine-readable output")

    commands.add_parser("validate", help=f"validate tools/*/tool.yaml and {CONTROL_FILE} (offline)")
    commands.add_parser("verify", help="check committed artifacts match their lock files (offline)")

    outdated_command = commands.add_parser("outdated", help="show tools with newer upstream releases (read-only)")
    outdated_command.add_argument("tools", nargs="*", metavar="TOOL")
    outdated_command.add_argument("--json", action="store_true", help="machine-readable output")

    targets_command = commands.add_parser(
        "targets", help=f"list the repositories a target set in {CONTROL_FILE} resolves to (read-only)"
    )
    targets_command.add_argument("--targets", dest="target_set", metavar="SET", help='target set name, or "all"')
    targets_command.add_argument("--json", action="store_true", help="machine-readable output")

    fetch_command = commands.add_parser(
        "fetch", help="download files stored with storage: fetch and verify them against the lock files"
    )
    fetch_command.add_argument("tools", nargs="*", metavar="TOOL")

    update_command = commands.add_parser(
        "update", help="download, verify and install a tool's assets into the working tree (no Git)"
    )
    update_command.add_argument("tool", metavar="TOOL")
    version_choice = update_command.add_mutually_exclusive_group()
    version_choice.add_argument("--version", dest="target_version", metavar="VERSION", help="install this version")
    version_choice.add_argument("--latest", action="store_true", help="install the newest acceptable release")

    check_command = commands.add_parser("check", help="find updates and raise merge/pull requests for them")
    check_command.add_argument("tools", nargs="*", metavar="TOOL")
    check_command.add_argument("--targets", dest="target_set", metavar="SET", help='target set name, or "all"')
    check_command.add_argument("--dry-run", action="store_true", help="report what would happen; change nothing")
    check_command.add_argument(
        "--base", metavar="BRANCH", help="target branch (default: configured base branch, else the default branch)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # Never crash on a console that can't print ✓ / →.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")
    arguments = build_parser().parse_args(argv)
    root: Path = arguments.root.resolve()
    try:
        control = _load_control(root, arguments.config)
    except ToolbotError as error:
        print(f"error [{error.code}]: {error.message}", file=sys.stderr)
        return EXIT_USAGE if error.code == "USAGE" else EXIT_FAILURES
    ctx = create_context(control=control)
    handlers = {
        "list": _list,
        "validate": _validate,
        "verify": _verify,
        "outdated": _outdated,
        "targets": _targets,
        "fetch": _fetch,
        "update": _update,
        "check": _check,
    }
    try:
        return handlers[arguments.command](ctx, root, arguments)
    except ToolbotError as error:
        ctx.log(f"error [{error.code}]: {error.message}")
        return EXIT_USAGE if error.code == "USAGE" else EXIT_FAILURES
    finally:
        ctx.http.close()


def _load_control(root: Path, explicit_path: Path | None) -> ControlConfig:
    if explicit_path is not None:
        return load_control(explicit_path.resolve())
    default_path = root / CONTROL_FILE
    return load_control(default_path) if default_path.is_file() else ControlConfig()


def _list(ctx: Context, root: Path, arguments: argparse.Namespace) -> int:
    loaded = load_all(root, ctx.providers)
    if arguments.json:
        print(
            json.dumps(
                [
                    {
                        "name": tool.name,
                        "version": tool.version,
                        "provider": tool.source.provider,
                        "host": tool.source.host,
                        "repository": tool.source.repository,
                        "assets": [asset.id for asset in tool.assets],
                    }
                    for tool in loaded.tools
                ],
                indent=2,
            )
        )
    else:
        _print_table(
            ["TOOL", "VERSION", "SOURCE", "ASSETS"],
            [
                [
                    tool.name,
                    tool.version,
                    f"{tool.source.provider}:{tool.source.repository}",
                    ", ".join(asset.id for asset in tool.assets) or "(version pins only)",
                ]
                for tool in loaded.tools
            ],
        )
    for name, error in loaded.errors.items():
        ctx.log(f"error: {name}: {error.message}")
    return EXIT_FAILURES if loaded.errors else EXIT_OK


def _validate(ctx: Context, root: Path, arguments: argparse.Namespace) -> int:
    # toolbot.yaml was already validated while loading; reaching here means it is valid.
    if ctx.control.file is not None:
        target_count = sum(len(specs) for specs in ctx.control.targets.values())
        print(
            f"{ctx.control.file.name}: valid ({len(ctx.control.hosts)} host(s), {len(ctx.control.rules)} rule(s), "
            f"{len(ctx.control.targets)} target set(s) with {target_count} target(s))"
        )
    loaded = load_all(root, ctx.providers)
    for error in loaded.errors.values():
        ctx.log(f"error: {error.message}")
    print(f"tools: {len(loaded.tools)} valid, {len(loaded.errors)} invalid")
    return EXIT_FAILURES if loaded.errors else EXIT_OK


def _verify(ctx: Context, root: Path, arguments: argparse.Namespace) -> int:
    loaded = load_all(root, ctx.providers)
    problems = [error.message for error in loaded.errors.values()]
    for tool in loaded.tools:
        problems.extend(verify_tool(root, tool))
    for problem in problems:
        ctx.log(f"error: {problem}")
    print(f"verified {len(loaded.tools)} tool(s), {len(problems)} problem(s)")
    return EXIT_FAILURES if problems else EXIT_OK


def _outdated(ctx: Context, root: Path, arguments: argparse.Namespace) -> int:
    loaded = load_all(root, ctx.providers, arguments.tools or None)
    rows: list[dict[str, str | None]] = []
    for definition in loaded.tools:
        try:
            tool = apply_rules(definition, ctx.control.rules, "")
            if tool is None:
                rows.append(
                    {"tool": definition.name, "current": definition.version, "latest": None, "status": "disabled"}
                )
                continue
            found = discover(ctx, tool)
            status = found.status.replace("-", " ")
            if found.held_back:
                status += f" (held back: {found.held_back_summary()})"
            latest = found.latest.version.raw if found.latest else None
            rows.append({"tool": tool.name, "current": tool.version, "latest": latest, "status": status})
        except ToolbotError as error:
            rows.append(
                {
                    "tool": definition.name,
                    "current": definition.version,
                    "latest": None,
                    "status": f"error: {error.message}",
                }
            )
    rows += [
        {"tool": name, "current": None, "latest": None, "status": f"error: {error.message}"}
        for name, error in loaded.errors.items()
    ]
    if arguments.json:
        print(json.dumps(rows, indent=2))
    else:
        _print_table(
            ["TOOL", "CURRENT", "LATEST", "STATUS"],
            [
                [str(row["tool"]), row["current"] or "-", row["latest"] or "-", str(row["status"]).splitlines()[0]]
                for row in rows
            ],
        )
    return EXIT_FAILURES if any(str(row["status"]).startswith("error") for row in rows) else EXIT_OK


def _targets(ctx: Context, root: Path, arguments: argparse.Namespace) -> int:
    resolution = resolve_targets(ctx, select_targets(ctx.control, arguments.target_set))
    if arguments.json:
        print(
            json.dumps(
                [
                    {
                        "repository": repository.path,
                        "host": repository.host.name,
                        "url": f"{repository.host.url}/{repository.path}",
                        "set": repository.target_set,
                        "base_branch": repository.base_branch,
                    }
                    for repository in resolution.repositories
                ],
                indent=2,
            )
        )
    else:
        _print_table(
            ["REPOSITORY", "HOST", "SET", "BASE BRANCH"],
            [
                [
                    repository.path,
                    repository.host.name,
                    repository.target_set or "-",
                    repository.base_branch or "(default)",
                ]
                for repository in resolution.repositories
            ],
        )
    for problem in resolution.problems:
        ctx.log(f"error: {problem}")
    return EXIT_FAILURES if resolution.problems else EXIT_OK


def _fetch(ctx: Context, root: Path, arguments: argparse.Namespace) -> int:
    loaded = load_all(root, ctx.providers, arguments.tools or None)
    problems = [error.message for error in loaded.errors.values()]
    fetched: list[str] = []
    for tool in loaded.tools:
        try:
            fetched += fetch_tool(ctx, root, tool)
        except ToolbotError as error:
            problems.append(f"{tool.name}: {error.message}")
    for problem in problems:
        ctx.log(f"error: {problem}")
    print(f"fetched {len(fetched)} file(s), {len(problems)} problem(s)")
    return EXIT_FAILURES if problems else EXIT_OK


def _update(ctx: Context, root: Path, arguments: argparse.Namespace) -> int:
    version = arguments.target_version
    release = None
    if arguments.latest:
        loaded = load_all(root, ctx.providers, [arguments.tool])
        if arguments.tool in loaded.errors:
            raise loaded.errors[arguments.tool]
        tool = apply_rules(loaded.tools[0], ctx.control.rules, "") or loaded.tools[0]
        found = discover(ctx, tool)
        if found.latest is None:
            raise ToolbotError("RELEASE_NOT_FOUND", f"no acceptable release found for {arguments.tool}")
        version, release = found.latest.version.raw, found.latest.release
    apply_version(ctx, root, arguments.tool, version, release)
    return EXIT_OK


def _check(ctx: Context, root: Path, arguments: argparse.Namespace) -> int:
    only = arguments.tools or None
    problems: list[str] = []
    if ctx.control.targets:
        resolution = resolve_targets(ctx, select_targets(ctx.control, arguments.target_set))
        repositories = resolution.repositories
        problems = resolution.problems
    elif arguments.target_set:
        raise ToolbotError("USAGE", f"--targets needs targets defined in {CONTROL_FILE}")
    else:
        repository = _environment_repository(ctx, arguments.dry_run)
        if repository is None:  # dry run with no target configured: read the local checkout
            outcomes = plan_local(ctx, root, only)
            _print_outcomes(outcomes)
            print(f"\nDry run: {_summary(outcomes)}")
            return EXIT_FAILURES if any(outcome.status in FAILURE_STATUSES for outcome in outcomes) else EXIT_OK
        repositories = [repository]

    results = check_repositories(ctx, repositories, only, arguments.dry_run, arguments.base)
    _print_results(results, arguments.dry_run)
    for problem in problems:
        ctx.log(f"error: {problem}")
    failed = problems or any(
        result.error or any(outcome.status in FAILURE_STATUSES for outcome in result.outcomes) for result in results
    )
    return EXIT_FAILURES if failed else EXIT_OK


def _environment_repository(ctx: Context, dry_run: bool) -> TargetRepository | None:
    if dry_run:
        try:
            repository = environment_target(ctx.env, ctx.hosts)
        except ToolbotError:
            return None
        return repository if ctx.hosts.token(repository.host) else None
    return environment_target(ctx.env, ctx.hosts)


def _print_results(results: list[RepositoryResult], dry_run: bool) -> None:
    all_outcomes: list[Outcome] = []
    for result in results:
        print(f"\n== {result.repository.label}")
        if result.error:
            print(f"  FAILED: {result.error}")
            continue
        _print_outcomes(result.outcomes)
        all_outcomes.extend(result.outcomes)
    failed_repositories = sum(1 for result in results if result.error)
    repository_note = f"{len(results)} repositor{'y' if len(results) == 1 else 'ies'}"
    if failed_repositories:
        repository_note += f" ({failed_repositories} failed)"
    print(f"\n{'Dry run: ' if dry_run else ''}{repository_note}, {_summary(all_outcomes)}")


def _print_outcomes(outcomes: list[Outcome]) -> None:
    for outcome in outcomes:
        print(f"\n{outcome.tool}")
        if outcome.current is not None:
            print(f"  Current: {outcome.current}")
        if outcome.latest is not None:
            print(f"  Latest:  {outcome.latest}")
        print(f"  {HEADLINES[outcome.status]}")
        if outcome.message and outcome.status != "up-to-date":
            for line in outcome.message.splitlines():
                print(f"  {line}")
        if outcome.details:
            print("  Changes:" if outcome.status == "would-create" else "  Details:")
            for line in outcome.details:
                print(f"    {line}")
        if outcome.branch and outcome.status == "would-create":
            print(f"  Would create: {outcome.branch}")
        elif outcome.branch and outcome.status in ("created", "superseded"):
            print(f"  Branch: {outcome.branch}")
        if outcome.url:
            print(f"  {outcome.url}")


def _summary(outcomes: list[Outcome]) -> str:
    counts: dict[str, int] = {}
    for outcome in outcomes:
        counts[outcome.status] = counts.get(outcome.status, 0) + 1
    breakdown = ", ".join(f"{count} {status}" for status, count in sorted(counts.items()))
    return f"{len(outcomes)} tool(s)" + (f": {breakdown}" if breakdown else "")


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [
        max(len(header), *(len(row[index]) for row in rows)) if rows else len(header)
        for index, header in enumerate(headers)
    ]
    print("  ".join(header.ljust(width) for header, width in zip(headers, widths, strict=True)).rstrip())
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
