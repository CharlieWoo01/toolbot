"""Command-line interface.

Read-only:   list · validate · verify · outdated
Local:       update   (fetch/verify/install into the working tree; no Git, no MR)
Automation:  check    (full lifecycle: branch, commit, push, merge request; --dry-run to preview)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from toolbot import __version__
from toolbot.config.loader import load_all
from toolbot.errors import ToolbotError
from toolbot.git import GitIdentity, Repository
from toolbot.providers.target import TargetProvider, create_target
from toolbot.services.artifacts import apply_version
from toolbot.services.context import Context, create_context
from toolbot.services.discovery import discover
from toolbot.services.updater import FAILURE_STATUSES, Outcome, Updater, UpdateSettings, plan
from toolbot.services.verify import verify_tool

EXIT_OK, EXIT_FAILURES, EXIT_USAGE = 0, 1, 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="toolbot",
        description="Keeps standalone tools and binaries up to date with automated merge requests.",
    )
    parser.add_argument("--version", action="version", version=f"toolbot {__version__}")
    parser.add_argument("--root", type=Path, default=Path("."), help="repository root containing tools/ (default: .)")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("list", help="list configured tools")
    p.add_argument("--json", action="store_true", help="machine-readable output")

    sub.add_parser("validate", help="validate every tools/*/tool.yaml (offline)")
    sub.add_parser("verify", help="check committed artifacts match their lock files (offline)")

    p = sub.add_parser("outdated", help="show tools with newer upstream releases (read-only)")
    p.add_argument("tools", nargs="*", metavar="TOOL")
    p.add_argument("--json", action="store_true", help="machine-readable output")

    p = sub.add_parser(
        "update",
        help="download, verify and install a tool's assets into the working tree (no Git, no merge request)",
    )
    p.add_argument("tool", metavar="TOOL")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--version", dest="target_version", metavar="VERSION", help="install this version")
    group.add_argument("--latest", action="store_true", help="install the newest acceptable release")

    p = sub.add_parser("check", help="find updates and raise merge requests for them")
    p.add_argument("tools", nargs="*", metavar="TOOL")
    p.add_argument("--dry-run", action="store_true", help="report what would happen; change nothing")
    p.add_argument(
        "--base", metavar="BRANCH", help="target branch (default: TOOLBOT_BASE_BRANCH or the default branch)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # Never crash on a console that can't print ✓ / →.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    ctx = create_context()
    root: Path = args.root.resolve()
    try:
        handler = {
            "list": _list,
            "validate": _validate,
            "verify": _verify,
            "outdated": _outdated,
            "update": _update,
            "check": _check,
        }[args.command]
        return handler(ctx, root, args)
    except ToolbotError as err:
        ctx.log(f"error [{err.code}]: {err.message}")
        return EXIT_USAGE if err.code == "USAGE" else EXIT_FAILURES
    finally:
        ctx.http.close()


def _list(ctx: Context, root: Path, args: argparse.Namespace) -> int:
    loaded = load_all(root, ctx.providers)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "name": t.name,
                        "version": t.version,
                        "provider": t.source.provider,
                        "repository": t.source.repository,
                        "assets": [a.id for a in t.assets],
                    }
                    for t in loaded.tools
                ],
                indent=2,
            )
        )
    else:
        _table(
            ["TOOL", "VERSION", "SOURCE", "ASSETS"],
            [
                [t.name, t.version, f"{t.source.provider}:{t.source.repository}", ", ".join(a.id for a in t.assets)]
                for t in loaded.tools
            ],
        )
    for name, err in loaded.errors.items():
        ctx.log(f"error: {name}: {err.message}")
    return EXIT_FAILURES if loaded.errors else EXIT_OK


def _validate(ctx: Context, root: Path, args: argparse.Namespace) -> int:
    loaded = load_all(root, ctx.providers)
    for err in loaded.errors.values():
        ctx.log(f"error: {err.message}")
    print(f"{len(loaded.tools)} valid, {len(loaded.errors)} invalid")
    return EXIT_FAILURES if loaded.errors else EXIT_OK


def _verify(ctx: Context, root: Path, args: argparse.Namespace) -> int:
    loaded = load_all(root, ctx.providers)
    problems = [err.message for err in loaded.errors.values()]
    for tool in loaded.tools:
        problems.extend(verify_tool(root, tool))
    for problem in problems:
        ctx.log(f"error: {problem}")
    print(f"verified {len(loaded.tools)} tool(s), {len(problems)} problem(s)")
    return EXIT_FAILURES if problems else EXIT_OK


def _outdated(ctx: Context, root: Path, args: argparse.Namespace) -> int:
    loaded = load_all(root, ctx.providers, args.tools or None)
    rows: list[dict[str, str | None]] = []
    for tool in loaded.tools:
        try:
            found = discover(ctx, tool)
            status = found.status.replace("-", " ")
            if found.ignored:
                status += f" (ignored: {', '.join(found.ignored)})"
            rows.append(
                {
                    "tool": tool.name,
                    "current": tool.version,
                    "latest": found.latest.version.raw if found.latest else None,
                    "status": status,
                }
            )
        except ToolbotError as err:
            rows.append({"tool": tool.name, "current": tool.version, "latest": None, "status": f"error: {err.message}"})
    rows += [
        {"tool": n, "current": None, "latest": None, "status": f"error: {e.message}"} for n, e in loaded.errors.items()
    ]
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        _table(
            ["TOOL", "CURRENT", "LATEST", "STATUS"],
            [[str(r["tool"]), r["current"] or "-", r["latest"] or "-", str(r["status"]).splitlines()[0]] for r in rows],
        )
    return EXIT_FAILURES if any(str(r["status"]).startswith("error") for r in rows) else EXIT_OK


def _update(ctx: Context, root: Path, args: argparse.Namespace) -> int:
    version = args.target_version
    release = None
    if args.latest:
        loaded = load_all(root, ctx.providers, [args.tool])
        if args.tool in loaded.errors:
            raise loaded.errors[args.tool]
        found = discover(ctx, loaded.tools[0])
        if found.latest is None:
            raise ToolbotError("RELEASE_NOT_FOUND", f"no acceptable release found for {args.tool}")
        version, release = found.latest.version.raw, found.latest.release
    apply_version(ctx, root, args.tool, version, release)
    return EXIT_OK


def _check(ctx: Context, root: Path, args: argparse.Namespace) -> int:
    env = ctx.env
    if args.dry_run:
        target: TargetProvider | None = None
        base = args.base or env.get("TOOLBOT_BASE_BRANCH")
        if env.get("TOOLBOT_GITLAB_TOKEN"):
            target = create_target(ctx.http, env, ctx.warn)
            base = base or target.default_branch()
        outcomes = plan(ctx, root, args.tools or None, target, base)
        _print_outcomes(outcomes, dry_run=True)
        return EXIT_FAILURES if any(o.status in FAILURE_STATUSES for o in outcomes) else EXIT_OK

    target = create_target(ctx.http, env, ctx.warn)
    identity = GitIdentity(
        env.get("TOOLBOT_GIT_AUTHOR_NAME") or "Toolbot",
        env.get("TOOLBOT_GIT_AUTHOR_EMAIL") or "toolbot@noreply.invalid",
    )
    repo = Repository(target.repository_url(), identity, target.git_auth())
    labels_env = env.get("TOOLBOT_MR_LABELS")
    labels = (
        tuple(x.strip() for x in labels_env.split(",") if x.strip())
        if labels_env is not None
        else ("toolbot", "tool-update")
    )
    settings = UpdateSettings(
        base_branch=args.base or env.get("TOOLBOT_BASE_BRANCH") or target.default_branch(),
        labels=labels,
    )
    outcomes = Updater(ctx, target, repo, settings).run(args.tools or None)
    _print_outcomes(outcomes, dry_run=False)
    return EXIT_FAILURES if any(o.status in FAILURE_STATUSES for o in outcomes) else EXIT_OK


def _print_outcomes(outcomes: list[Outcome], dry_run: bool) -> None:
    headlines = {
        "would-create": "Update available.",
        "created": "Update proposed.",
        "superseded": "Update proposed, superseding the previous one.",
        "exists": "Update already proposed.",
        "up-to-date": "Up to date.",
        "ahead": "Ahead of upstream.",
        "no-releases": "No acceptable releases.",
        "rejected": "Skipped: version was rejected.",
        "reverted": "Skipped: update was merged and reverted.",
        "blocked": "Blocked: needs attention.",
        "failed": "FAILED.",
    }
    for o in outcomes:
        print(f"\n{o.tool}")
        if o.current is not None:
            print(f"  Current: {o.current}")
        if o.latest is not None:
            print(f"  Latest:  {o.latest}")
        print(f"  {headlines[o.status]}")
        if o.message and o.status not in ("up-to-date",):
            for line in o.message.splitlines():
                print(f"  {line}")
        if o.details:
            print("  Assets:" if dry_run else "  Details:")
            for line in o.details:
                print(f"    {line}")
        if o.branch and o.status == "would-create":
            print(f"  Would create: {o.branch}")
        elif o.branch and o.status in ("created", "superseded"):
            print(f"  Branch: {o.branch}")
        if o.url:
            print(f"  {o.url}")
    counts: dict[str, int] = {}
    for o in outcomes:
        counts[o.status] = counts.get(o.status, 0) + 1
    summary = ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "no tools"
    print(f"\n{'Dry run: ' if dry_run else ''}{len(outcomes)} tool(s): {summary}")


def _table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)).rstrip())
    for row in rows:
        print("  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
