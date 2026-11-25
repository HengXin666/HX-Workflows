"""Command line for repository governance scan and apply."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from governance.apply_run import apply_issue
from governance.scan_run import scan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="统一个人仓库的设置、模板、标签和提交规范")
    commands = parser.add_subparsers(dest="command", required=True)

    scan_parser = commands.add_parser("scan", help="只读扫描并更新审阅单")
    scan_parser.add_argument("--config", type=Path, required=True)
    scan_parser.add_argument("--repo-limit", default="")
    scan_parser.add_argument("--force-new", choices=["true", "false"], default="false")
    scan_parser.add_argument("--dry-run", action="store_true", help="不写审阅单, 只输出 report.md / report.json")
    scan_parser.add_argument("--output", type=Path, default=Path("governance/output"))

    apply_parser = commands.add_parser("apply", help="执行审阅单里勾选的仓库")
    apply_parser.add_argument("--config", type=Path, required=True)
    apply_parser.add_argument("--issue", type=int, required=True)

    args = parser.parse_args(argv)
    if args.command == "scan":
        return scan(args.config, args.repo_limit, args.force_new == "true", args.dry_run, args.output)
    return apply_issue(args.config, args.issue)


if __name__ == "__main__":
    sys.exit(main())
