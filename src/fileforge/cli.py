"""The CLI requires an explicit --apply before changing file names."""

from __future__ import annotations

import argparse
import json
import logging
import re
import tomllib
from dataclasses import asdict
from pathlib import Path

from fileforge.core import SafetyError, apply_plan, plan_rename, plan_sort, scan, size_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safe file inspection and batch rename")
    parser.add_argument("--config", type=Path, help="TOML file with a [scan] section")
    parser.add_argument("--verbose", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("scan", "duplicates", "rename", "sort", "sizes"):
        child = commands.add_parser(command)
        child.add_argument("root", type=Path)
        if command == "rename":
            child.add_argument("pattern")
            child.add_argument("replacement")
        if command in {"rename", "sort"}:
            child.add_argument("--apply", action="store_true", help="apply with verified backups")
        if command == "sizes":
            child.add_argument("--min-size", type=int, default=0, help="minimum file size in bytes")
            child.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s"
    )
    try:
        config = tomllib.loads(args.config.read_text()) if args.config else {}
        settings = config.get("scan", {})
        if set(settings) - {"recursive", "max_size"}:
            raise ValueError("Unknown scan setting")
        if args.command in {"rename", "sort"}:
            plan = (
                plan_rename(args.root, args.pattern, args.replacement)
                if args.command == "rename"
                else plan_sort(args.root)
            )
            backup = apply_plan(args.root, plan, apply=args.apply)
            result = {
                "applied": args.apply,
                "renames": [asdict(item) for item in plan],
                "backup": str(backup) if backup else None,
            }
        elif args.command == "sizes":
            result = size_report(args.root, min_size=args.min_size, limit=args.limit)
        else:
            report = scan(args.root, **settings)
            result = (
                asdict(report)
                if args.command == "scan"
                else {
                    "duplicates": [
                        [asdict(item) for item in group] for group in report.duplicates()
                    ],
                    "errors": report.errors,
                }
            )
        print(json.dumps(result, indent=2))
        logging.info(json.dumps({"event": "operation_completed", "command": args.command}))
        return int(bool(result.get("errors")))
    except (OSError, ValueError, SafetyError, re.error) as exc:
        parser.exit(2, f"fileforge: {exc}\n")
    return 2
