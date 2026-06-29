#!/usr/bin/env python3
"""Send Telegram sign-in report as Chinese HTML email."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MailConfig:
    host: str
    port: int
    username: str
    password: str
    mail_from: str
    mail_to: list[str]
    use_ssl: bool
    starttls: bool


def env_first(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_mail_config() -> MailConfig | None:
    host = env_first("SMTP_HOST", "MAIL_HOST")
    username = env_first("SMTP_USERNAME", "MAIL_USERNAME", "SMTP_USER", "MAIL_USER")
    password = env_first("SMTP_PASSWORD", "MAIL_PASSWORD", "SMTP_PASS", "MAIL_PASS")
    mail_to_raw = env_first("SMTP_TO", "MAIL_TO", "EMAIL_TO")
    if not (host and username and password and mail_to_raw):
        print("邮件配置不完整，跳过 HTML 邮件发送。需要 SMTP_HOST/SMTP_USERNAME/SMTP_PASSWORD/SMTP_TO。")
        return None

    port = int(env_first("SMTP_PORT", "MAIL_PORT") or "465")
    use_ssl = parse_bool(env_first("SMTP_USE_SSL", "MAIL_USE_SSL"), default=(port == 465))
    starttls = parse_bool(env_first("SMTP_STARTTLS", "MAIL_STARTTLS"), default=(not use_ssl))
    mail_from = env_first("SMTP_FROM", "MAIL_FROM") or username
    mail_to = [x.strip() for x in re.split(r"[,;\n]", mail_to_raw) if x.strip()]
    if not mail_to:
        print("SMTP_TO/MAIL_TO 为空，跳过 HTML 邮件发送。")
        return None
    return MailConfig(host, port, username, password, mail_from, mail_to, use_ssl, starttls)


def status_label(status: str) -> str:
    return {
        "success": "成功",
        "failure": "失败",
    }.get(status, status)


def build_html_report(report: dict[str, Any]) -> str:
    tasks = report.get("tasks", [])
    total = sum(len(task.get("accounts", [])) for task in tasks)
    success = sum(1 for task in tasks for item in task.get("accounts", []) if item.get("status") == "success")
    failure = total - success
    tabs = "\n".join(
        f'<a class="tab" href="#task-{html.escape(str(task.get("id")), quote=True)}">{html.escape(str(task.get("id")))}</a>'
        for task in tasks
    )
    sections: list[str] = []
    for task in tasks:
        task_id = str(task.get("id"))
        rows: list[str] = []
        for account in task.get("accounts", []):
            texts = [str(x) for x in account.get("messages", []) if str(x).strip()]
            latest = texts[0] if texts else str(account.get("summary", ""))
            detail = "\n\n".join(texts[:3]) or str(account.get("summary", ""))
            status = str(account.get("status", "unknown"))
            status_class = "ok" if status == "success" else "bad"
            rows.append(
                "<tr>"
                f"<td>账号 #{html.escape(str(account.get('account_index')))}</td>"
                f'<td><span class="badge {status_class}">{html.escape(status_label(status))}</span></td>'
                f"<td>{'是' if account.get('matched') else '否'}</td>"
                f"<td>{html.escape(latest[:220])}</td>"
                f"<td><pre>{html.escape(detail[:1600])}</pre></td>"
                "</tr>"
            )
        sections.append(
            f"""
            <section class="task-page" id="task-{html.escape(task_id, quote=True)}">
              <h2>{html.escape(task_id)}</h2>
              <table>
                <thead>
                  <tr>
                    <th>账号</th>
                    <th>状态</th>
                    <th>命中过滤</th>
                    <th>最新结果</th>
                    <th>消息详情</th>
                  </tr>
                </thead>
                <tbody>{''.join(rows)}</tbody>
              </table>
            </section>
            """
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <style>
    body {{ margin: 0; padding: 24px; background: #f6f7f9; color: #1f2937; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    .wrap {{ max-width: 1180px; margin: 0 auto; background: #ffffff; border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden; }}
    .head {{ padding: 22px 24px; border-bottom: 1px solid #e5e7eb; }}
    h1 {{ margin: 0 0 8px; font-size: 22px; }}
    .meta {{ color: #6b7280; font-size: 14px; }}
    .tabs {{ display: flex; gap: 8px; flex-wrap: wrap; padding: 14px 24px; border-bottom: 1px solid #e5e7eb; background: #fafafa; }}
    .tab {{ color: #111827; text-decoration: none; border: 1px solid #d1d5db; border-radius: 6px; padding: 7px 12px; background: #fff; font-size: 14px; }}
    .task-page {{ padding: 24px; border-top: 1px solid #eef0f3; }}
    .task-page:first-of-type {{ border-top: 0; }}
    h2 {{ margin: 0 0 14px; font-size: 18px; }}
    table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
    th, td {{ border: 1px solid #e5e7eb; padding: 10px; text-align: left; vertical-align: top; font-size: 13px; }}
    th {{ background: #f9fafb; color: #374151; }}
    th:nth-child(1), td:nth-child(1) {{ width: 82px; }}
    th:nth-child(2), td:nth-child(2) {{ width: 74px; }}
    th:nth-child(3), td:nth-child(3) {{ width: 78px; }}
    .badge {{ display: inline-block; border-radius: 999px; padding: 2px 9px; font-size: 12px; }}
    .ok {{ color: #065f46; background: #d1fae5; }}
    .bad {{ color: #991b1b; background: #fee2e2; }}
    pre {{ white-space: pre-wrap; word-break: break-word; margin: 0; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; line-height: 1.5; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="head">
      <h1>Telegram 每日签到结果</h1>
      <div class="meta">任务数：{len(tasks)} ｜ 账号执行：{total} ｜ 成功：{success} ｜ 失败：{failure}</div>
    </div>
    <nav class="tabs">{tabs}</nav>
    {''.join(sections)}
  </div>
</body>
</html>"""


def send_html_mail(subject: str, html_body: str) -> None:
    config = load_mail_config()
    if config is None:
        return
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.mail_from
    message["To"] = ", ".join(config.mail_to)
    message.set_content("请使用支持 HTML 的邮箱客户端查看 Telegram 签到结果。")
    message.add_alternative(html_body, subtype="html")

    smtp_cls = smtplib.SMTP_SSL if config.use_ssl else smtplib.SMTP
    with smtp_cls(config.host, config.port, timeout=30) as smtp:
        if config.starttls and not config.use_ssl:
            smtp.starttls()
        smtp.login(config.username, config.password)
        smtp.send_message(message)
    print(f"HTML 邮件已发送到: {', '.join(config.mail_to)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Send TG sign-in HTML report")
    parser.add_argument("report", help="TG JSON report path")
    parser.add_argument("--subject", default="Telegram 每日签到结果")
    args = parser.parse_args()

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    send_html_mail(args.subject, build_html_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
