"""Command line: python -m mirror sync --config mirror/config.yml"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mirror.config import load_config
from mirror.plan import parse_limit
from mirror.report import write_reports
from mirror.run import run
from mirror.source import GitHubSource


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 GitHub 仓库 (含私有) 镜像同步到 GitLab / Gitee / Codeberg / CNB")
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync", help="同步全部仓库")
    sync.add_argument("--config", type=Path, required=True)
    sync.add_argument("--repo-limit", default="", help="只同步这些仓库, 逗号分隔")
    sync.add_argument("--dry-run", action="store_true", help="只读: 不建仓, 不改可见性, 不推送")
    sync.add_argument("--cache", type=Path, default=Path(".mirror-cache"), help="源仓库裸镜像缓存目录")
    sync.add_argument("--output", type=Path, default=Path("mirror/output"))
    sync.add_argument("--reveal-private", action="store_true", help="报告里显示私有仓库真名 (只在本地用)")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except (OSError, ValueError) as error:
        print(f"配置错误: {error}", file=sys.stderr)
        return 2
    token = os.environ.get(config.source.token_env, "")
    if not token:
        print(f"缺少环境变量 {config.source.token_env}: 需要一个能读取全部仓库 (含私有) 的 GitHub 令牌", file=sys.stderr)
        return 2

    report = run(
        config,
        GitHubSource(config.source, token),
        repo_limit=parse_limit(args.repo_limit),
        dry_run=args.dry_run,
        cache=args.cache,
    )
    write_reports(report, args.output, os.environ.get("GITHUB_STEP_SUMMARY"), args.reveal_private)
    print((args.output / "report.md").read_text(encoding="utf-8"))
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
