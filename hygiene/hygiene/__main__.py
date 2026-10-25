"""Command line for repository hygiene scan and apply."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hygiene.apply_run import apply_issue
from hygiene.scan_run import scan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="扫描个人仓库里已合入的分支和过期 Skill")
    commands = parser.add_subparsers(dest="command", required=True)

    scan_parser = commands.add_parser("scan", help="扫描并更新审阅单与状态卡片")
    scan_parser.add_argument("--config", type=Path, required=True)
    scan_parser.add_argument("--badges", type=Path, required=True)
    scan_parser.add_argument("--repo-limit", default="")
    scan_parser.add_argument("--force-new", choices=["true", "false"], default="false")

    apply_parser = commands.add_parser("apply", help="执行审阅单里勾选的修复")
    apply_parser.add_argument("--config", type=Path, required=True)
    apply_parser.add_argument("--issue", type=int, required=True)

    args = parser.parse_args(argv)
    if args.command == "scan":
        return scan(args.config, args.badges, args.repo_limit, args.force_new == "true")
    return apply_issue(args.config, args.issue)


if __name__ == "__main__":
    sys.exit(main())
