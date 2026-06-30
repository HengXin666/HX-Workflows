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


EMOJI_RE = re.compile(
    "["
    "\U0001f000-\U0001faff"
    "\U00002700-\U000027bf"
    "\U00002600-\U000026ff"
    "\U00002b00-\U00002bff"
    "]+"
)
TEXT_EMOTE_RE = re.compile(
    r"(?<![\w/])"
    r"(?:"
    r"[:;=8xX][-']?[)(DPpOo/\\]|"
    r"[-^]_[\-^]|"
    r"\^[_oO\.]\^|"
    r"T[_-]?T"
    r")"
    r"(?![\w/])"
)
LAUGH_TEXT_RE = re.compile(r"(?<![\w.])(?:233+|www+)(?![\w.])", flags=re.I)


def display_text(value: Any, limit: int | None = None) -> str:
    text = str(value or "")
    text = EMOJI_RE.sub("", text)
    text = TEXT_EMOTE_RE.sub("", text)
    text = LAUGH_TEXT_RE.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if limit is not None:
        text = text[:limit]
    return text


def build_html_report(report: dict[str, Any]) -> str:
    tasks = report.get("tasks", [])
    total = sum(len(task.get("accounts", [])) for task in tasks)
    success = sum(1 for task in tasks for item in task.get("accounts", []) if item.get("status") == "success")
    failure = total - success
    matched = sum(1 for task in tasks for item in task.get("accounts", []) if item.get("matched"))
    tabs = "\n".join(
        f'<a class="tab" href="#task-{html.escape(display_text(task.get("id")), quote=True)}" '
        'style="display:inline-block;margin:0 6px 8px 0;padding:8px 12px;border:1px solid #2d3a4f;'
        'border-radius:8px;background:#101827;color:#d7e4f5;text-decoration:none;font-size:13px;line-height:18px;">'
        f'{html.escape(display_text(task.get("id")))}</a>'
        for task in tasks
    )
    sections: list[str] = []
    for task in tasks:
        task_id = display_text(task.get("id"))
        cards: list[str] = []
        for account in task.get("accounts", []):
            texts = [display_text(x) for x in account.get("messages", []) if display_text(x)]
            latest = texts[0] if texts else display_text(account.get("summary", ""))
            detail = "\n\n".join(texts[:3]) or display_text(account.get("summary", ""))
            status = str(account.get("status", "unknown"))
            status_class = "ok" if status == "success" else "bad"
            badge_style = (
                "color:#8af0c7;background:#113b31;border-color:#236b58;"
                if status_class == "ok"
                else "color:#ffb4b4;background:#441818;border-color:#7f2a2a;"
            )
            cards.append(
                f"""
                <div class="account-card" style="margin:0 0 14px;border:1px solid #233047;border-radius:8px;background:#101827;overflow:hidden;">
                  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">
                    <tr>
                      <td class="account-head" style="padding:14px 16px;border-bottom:1px solid #233047;">
                        <span style="display:inline-block;margin-right:10px;color:#f4f8ff;font-size:15px;font-weight:700;">账号 #{html.escape(display_text(account.get('account_index')))}</span>
                        <span class="badge" style="display:inline-block;padding:3px 9px;border:1px solid;{badge_style}border-radius:999px;font-size:12px;line-height:16px;font-weight:700;">{html.escape(status_label(status))}</span>
                      </td>
                    </tr>
                    <tr>
                      <td class="account-body" style="padding:14px 16px;">
                        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" class="account-grid" style="border-collapse:collapse;">
                          <tr>
                            <td class="label" style="width:92px;padding:0 14px 10px 0;color:#8ea0ba;font-size:12px;line-height:18px;vertical-align:top;">命中过滤</td>
                            <td style="padding:0 0 10px;color:#e8eef8;font-size:13px;line-height:20px;vertical-align:top;">{'是' if account.get('matched') else '否'}</td>
                          </tr>
                          <tr>
                            <td class="label" style="width:92px;padding:0 14px 10px 0;color:#8ea0ba;font-size:12px;line-height:18px;vertical-align:top;">最新结果</td>
                            <td style="padding:0 0 10px;color:#e8eef8;font-size:13px;line-height:20px;vertical-align:top;">{html.escape(display_text(latest, 260))}</td>
                          </tr>
                          <tr>
                            <td class="label" style="width:92px;padding:0 14px 0 0;color:#8ea0ba;font-size:12px;line-height:18px;vertical-align:top;">消息详情</td>
                            <td style="padding:0;color:#cbd7ea;font-size:13px;line-height:20px;vertical-align:top;">
                              <pre style="white-space:pre-wrap;word-break:break-word;overflow-wrap:anywhere;margin:0;font-family:Menlo,Consolas,'Courier New',monospace;font-size:12px;line-height:19px;color:#cbd7ea;">{html.escape(display_text(detail, 1600))}</pre>
                            </td>
                          </tr>
                        </table>
                      </td>
                    </tr>
                  </table>
                </div>
                """
            )
        sections.append(
            f"""
            <tr>
              <td class="section" id="task-{html.escape(task_id, quote=True)}" style="padding:6px 24px 22px;">
                <h2 style="margin:14px 0 12px;color:#f4f8ff;font-size:18px;line-height:24px;font-weight:800;">{html.escape(task_id)}</h2>
                {''.join(cards) or '<div style="padding:16px;border:1px solid #233047;border-radius:8px;background:#101827;color:#8ea0ba;font-size:13px;">没有账号执行结果。</div>'}
              </td>
            </tr>
            """
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="x-apple-disable-message-reformatting">
  <meta name="color-scheme" content="dark">
  <meta name="supported-color-schemes" content="dark">
  <style>
    body, table, td, a {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
    table, td {{ mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
    @media screen and (max-width: 640px) {{
      body {{ padding: 0 !important; }}
      .container {{ width: 100% !important; border-radius: 0 !important; }}
      .pad {{ padding-left: 16px !important; padding-right: 16px !important; }}
      .metric {{ display: block !important; width: auto !important; margin: 0 0 10px !important; }}
      .section {{ padding-left: 16px !important; padding-right: 16px !important; }}
      .account-head, .account-body {{ padding-left: 14px !important; padding-right: 14px !important; }}
      .account-grid, .account-grid tbody, .account-grid tr, .account-grid td {{ display: block !important; width: 100% !important; }}
      .label {{ padding: 0 0 4px !important; }}
      .tabs {{ white-space: normal !important; }}
      h1 {{ font-size: 22px !important; line-height: 28px !important; }}
    }}
  </style>
</head>
<body style="margin:0;padding:24px;background:#07111f;color:#e8eef8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',Arial,sans-serif;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">Telegram 每日签到结果，成功 {success}，失败 {failure}。</div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;background:#07111f;">
    <tr>
      <td align="center" style="padding:0;">
        <table role="presentation" width="760" cellspacing="0" cellpadding="0" class="container" style="width:760px;max-width:760px;border-collapse:collapse;background:#0b1424;border:1px solid #1d2a3f;border-radius:8px;overflow:hidden;">
          <tr>
            <td class="pad" style="padding:26px 24px 18px;border-bottom:1px solid #1d2a3f;background:#0d182a;">
              <h1 style="margin:0 0 9px;color:#f7fbff;font-size:24px;line-height:31px;font-weight:800;letter-spacing:0;">Telegram 每日签到结果</h1>
              <div style="color:#9fb0c9;font-size:13px;line-height:20px;">自动任务执行报告</div>
            </td>
          </tr>
          <tr>
            <td class="pad" style="padding:18px 24px 10px;border-bottom:1px solid #1d2a3f;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">
                <tr>
                  <td class="metric" style="width:25%;padding:0 8px 8px 0;">
                    <div style="padding:12px;border:1px solid #233047;border-radius:8px;background:#101827;color:#8ea0ba;font-size:12px;line-height:18px;">任务数<br><strong style="display:block;margin-top:4px;color:#f4f8ff;font-size:20px;line-height:24px;">{len(tasks)}</strong></div>
                  </td>
                  <td class="metric" style="width:25%;padding:0 8px 8px 0;">
                    <div style="padding:12px;border:1px solid #233047;border-radius:8px;background:#101827;color:#8ea0ba;font-size:12px;line-height:18px;">账号执行<br><strong style="display:block;margin-top:4px;color:#f4f8ff;font-size:20px;line-height:24px;">{total}</strong></div>
                  </td>
                  <td class="metric" style="width:25%;padding:0 8px 8px 0;">
                    <div style="padding:12px;border:1px solid #235141;border-radius:8px;background:#0f241f;color:#8af0c7;font-size:12px;line-height:18px;">成功<br><strong style="display:block;margin-top:4px;color:#b9ffe2;font-size:20px;line-height:24px;">{success}</strong></div>
                  </td>
                  <td class="metric" style="width:25%;padding:0 0 8px 0;">
                    <div style="padding:12px;border:1px solid #5a2a2e;border-radius:8px;background:#2a1519;color:#ffb4b4;font-size:12px;line-height:18px;">失败<br><strong style="display:block;margin-top:4px;color:#ffd0d0;font-size:20px;line-height:24px;">{failure}</strong></div>
                  </td>
                </tr>
              </table>
              <div style="margin:2px 0 8px;color:#9fb0c9;font-size:13px;line-height:20px;">命中过滤：{matched}</div>
            </td>
          </tr>
          <tr>
            <td class="pad tabs" style="padding:14px 24px 8px;border-bottom:1px solid #1d2a3f;white-space:normal;">
              {tabs or '<span style="color:#8ea0ba;font-size:13px;">没有任务结果。</span>'}
            </td>
          </tr>
          {''.join(sections)}
        </table>
      </td>
    </tr>
  </table>
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
