"""Command line: python -m mirror sync --config mirror/config.yml"""

from __future__ import annotations

import argparse
import os
import smtplib
import sys
from collections.abc import Mapping
from pathlib import Path

from mirror.config import Config, load_config
from mirror.notify import load_mail_config, render_email, send_email, should_send
from mirror.plan import parse_limit
from mirror.report import write_reports
from mirror.run import RunReport, run
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
    sync.add_argument("--no-email", action="store_true", help="不发同步邮件")
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
    mail_failed = False if args.no_email else notify(config, report)
    return 1 if report.failed or mail_failed else 0


def run_url(env: Mapping[str, str]) -> str:
    server, repository, run_id = env.get("GITHUB_SERVER_URL", ""), env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_RUN_ID", "")
    return f"{server}/{repository}/actions/runs/{run_id}" if server and repository and run_id else ""


def notify(config: Config, report: RunReport, env: Mapping[str, str] | None = None, smtp_factory: object = None) -> bool:
    """Send the sync email. Returns True when sending failed; missing SMTP secrets only skip."""
    env = os.environ if env is None else env
    if not should_send(config.notify.email, report):
        print(f"邮件: notify.email={config.notify.email}, 本次不发送")
        return False
    mail = load_mail_config(env, config.notify.mail_to_env)
    if mail is None:
        print(f"邮件: 没有配置 SMTP_HOST / SMTP_USERNAME / SMTP_PASSWORD / SMTP_TO (或 {config.notify.mail_to_env}), 跳过")
        return False
    try:
        send_email(mail, render_email(config, report, run_url(env)), smtp_factory)  # type: ignore[arg-type]
    except (OSError, smtplib.SMTPException) as error:
        print(f"邮件发送失败: {type(error).__name__}: {error}", file=sys.stderr)
        return True
    print(f"邮件: 已发送到 {len(mail.mail_to)} 个收件地址")
    return False


if __name__ == "__main__":
    sys.exit(main())
