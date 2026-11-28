"""Daily sync email: which repositories are configured, which changed today, what failed, and where each copy lives.

The email goes only to the owner's mailbox, so it shows real private repository names; nothing here is written to
logs or artifacts. Styling follows the HX-UI 2 light theme tokens, converted to hex and inlined for mail clients.
"""

from __future__ import annotations

import smtplib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape

from mirror.config import Config, TargetConfig
from mirror.plan import SourceRepo
from mirror.run import Outcome, RunReport

BEIJING = timezone(timedelta(hours=8))

# HX-UI 2 (next/src/styles/theme.css) light tokens, OKLCH converted to sRGB hex
INK = {
    "background": "#fdfdfd",
    "card": "#ffffff",
    "foreground": "#0e0f12",
    "muted": "#f3f4f5",
    "muted_fg": "#666970",
    "border": "#e3e4e7",
    "primary": "#17181b",
    "brand": "#5d67ee",
    "brand_soft": "#ebecfd",
    "success": "#16a356",
    "success_soft": "#e3f4eb",
    "success_text": "#0f7a40",
    "warning": "#f09c17",
    "warning_soft": "#fdf2e1",
    "warning_text": "#a8650a",
    "destructive": "#df2225",
    "destructive_soft": "#fbe4e4",
    "destructive_text": "#b81c1f",
}
FONT = '"Geist", ui-sans-serif, system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif'
MONO = '"Geist Mono", "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace'
SHADOW = "0 1px 2px rgba(0,0,0,0.04), 0 4px 16px -4px rgba(0,0,0,0.08)"
PROVIDER_LABELS = {
    "gitlab": "GitLab",
    "gitee": "Gitee",
    "codeberg": "Codeberg",
    "forgejo": "Forgejo",
    "gitea": "Gitea",
    "cnb": "CNB",
}


@dataclass(frozen=True)
class MailConfig:
    host: str
    port: int
    username: str
    password: str
    mail_from: str
    mail_to: tuple[str, ...]
    use_ssl: bool
    starttls: bool


@dataclass(frozen=True)
class Email:
    subject: str
    html: str
    text: str


def _flag(raw: str, default: bool) -> bool:
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_mail_config(env: Mapping[str, str], mail_to_env: str = "MIRROR_MAIL_TO") -> MailConfig | None:
    """Same SMTP_* secrets as emall; mail_to_env picks a different mailbox for mirror reports."""
    host = env.get("SMTP_HOST", "").strip()
    username = env.get("SMTP_USERNAME", "").strip()
    password = env.get("SMTP_PASSWORD", "")
    recipients = env.get(mail_to_env, "").strip() or env.get("SMTP_TO", "").strip()
    mail_to = tuple(item.strip() for item in recipients.replace(";", ",").split(",") if item.strip())
    if not host or not username or not password or not mail_to:
        return None
    port = int(env.get("SMTP_PORT", "").strip() or "465")
    use_ssl = _flag(env.get("SMTP_USE_SSL", ""), port == 465)
    return MailConfig(
        host=host,
        port=port,
        username=username,
        password=password,
        mail_from=env.get("SMTP_FROM", "").strip() or username,
        mail_to=mail_to,
        use_ssl=use_ssl,
        starttls=_flag(env.get("SMTP_STARTTLS", ""), not use_ssl),
    )


def should_send(mode: str, report: RunReport) -> bool:
    if mode == "never":
        return False
    if mode == "failure":
        return report.failed or any(item.warnings for item in report.outcomes)
    return True


def owner_url(target: TargetConfig) -> str:
    return f"{target.base_url}/{target.owner}"


def repo_url(target: TargetConfig, name: str) -> str:
    return f"{target.base_url}/{target.owner}/{name}"


def _status(item: Outcome) -> str:
    if item.error:
        return "failed"
    if item.skipped:
        return "skipped"
    if item.warnings:
        return "warning"
    return "ok"


def _overall(report: RunReport) -> tuple[str, str]:
    failed = sum(1 for item in report.outcomes if item.error)
    if report.source_error:
        return "failed", "同步没有运行"
    if failed or report.target_problems_failed:
        problems = failed + sum(1 for text in report.target_problems.values() if not text.startswith("跳过"))
        return "failed", f"{problems} 项失败"
    if any(item.warnings for item in report.outcomes):
        return "warning", "完成, 有提醒"
    return "ok", "全部成功"


def _badge(kind: str, text: str) -> str:
    colors = {
        "ok": (INK["success_soft"], INK["success_text"]),
        "warning": (INK["warning_soft"], INK["warning_text"]),
        "failed": (INK["destructive_soft"], INK["destructive_text"]),
        "skipped": (INK["muted"], INK["muted_fg"]),
        "brand": (INK["brand_soft"], INK["brand"]),
    }
    background, color = colors.get(kind, colors["skipped"])
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:999px;background:{background};'
        f'color:{color};font-size:12px;line-height:18px;font-weight:500;white-space:nowrap;">{escape(text)}</span>'
    )


def _link(url: str, text: str, mono: bool = False) -> str:
    family = f"font-family:{MONO};" if mono else ""
    return (
        f'<a href="{escape(url, quote=True)}" style="color:{INK["foreground"]};text-decoration:none;'
        f'border-bottom:1px solid {INK["border"]};{family}">{escape(text)}</a>'
    )


def _chip(url: str, text: str, kind: str) -> str:
    dot = {"ok": INK["success"], "warning": INK["warning"], "failed": INK["destructive"]}.get(kind, INK["muted_fg"])
    return (
        f'<a href="{escape(url, quote=True)}" style="display:inline-block;margin:2px 4px 2px 0;padding:3px 10px;'
        f'border:1px solid {INK["border"]};border-radius:8px;background:{INK["card"]};color:{INK["foreground"]};'
        f'font-size:12px;line-height:18px;text-decoration:none;white-space:nowrap;">'
        f'<span style="display:inline-block;width:6px;height:6px;border-radius:999px;background:{dot};'
        f'margin-right:6px;vertical-align:middle;"></span>{escape(text)}</a>'
    )


def _card(title: str, body: str, subtitle: str = "") -> str:
    sub = f'<div style="margin-top:2px;color:{INK["muted_fg"]};font-size:13px;">{escape(subtitle)}</div>' if subtitle else ""
    return (
        f'<tr><td style="padding:0 0 16px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{INK["card"]};border:1px solid {INK["border"]};border-radius:12px;box-shadow:{SHADOW};">'
        f'<tr><td style="padding:18px 20px 6px;"><div style="font-size:15px;font-weight:600;color:{INK["foreground"]};">'
        f"{escape(title)}</div>{sub}</td></tr>"
        f'<tr><td style="padding:6px 20px 18px;">{body}</td></tr></table></td></tr>'
    )


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(
        f'<th align="left" style="padding:8px 10px;border-bottom:1px solid {INK["border"]};color:{INK["muted_fg"]};'
        f'font-size:12px;font-weight:500;">{escape(text)}</th>'
        for text in headers
    )
    body = "".join(
        "<tr>"
        + "".join(
            f'<td valign="top" style="padding:10px;border-bottom:1px solid {INK["muted"]};font-size:13px;'
            f'color:{INK["foreground"]};">{cell}</td>'
            for cell in row
        )
        + "</tr>"
        for row in rows
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">'
        f"<tr>{head}</tr>{body}</table>"
    )


def _empty(text: str) -> str:
    return f'<div style="padding:8px 0;color:{INK["muted_fg"]};font-size:13px;">{escape(text)}</div>'


def _stat(label: str, value: int, kind: str) -> str:
    color = {"failed": INK["destructive"], "warning": INK["warning_text"], "brand": INK["brand"]}.get(kind, INK["foreground"])
    return (
        f'<td width="25%" style="padding:4px;"><div style="background:{INK["card"]};border:1px solid {INK["border"]};'
        f'border-radius:12px;padding:14px 16px;box-shadow:{SHADOW};"><div style="color:{INK["muted_fg"]};font-size:12px;">'
        f'{escape(label)}</div><div style="margin-top:4px;font-size:24px;font-weight:600;color:{color};">{value}</div>'
        "</div></td>"
    )


def _visibility(private: bool) -> str:
    return _badge("skipped", "私有") if private else _badge("brand", "公开")


def _target_rows(config: Config, report: RunReport) -> list[list[str]]:
    rows = []
    for target in config.targets:
        problem = report.target_problems.get(target.name, "")
        if not problem:
            state = _badge("ok", "正常")
        elif problem.startswith("跳过"):
            state = _badge("skipped", "跳过") + f'<div style="margin-top:4px;color:{INK["muted_fg"]};">{escape(problem.removeprefix("跳过: "))}</div>'
        else:
            state = _badge("failed", "失败") + f'<div style="margin-top:4px;color:{INK["destructive_text"]};">{escape(problem)}</div>'
        provider = PROVIDER_LABELS.get(target.provider, target.provider)
        label = provider if target.name.lower() == target.provider else f"{target.name} ({provider})"
        rows.append([escape(label), _link(owner_url(target), f"{target.base_url.split('://', 1)[-1]}/{target.owner}", True), state])
    return rows


def _actions_text(items: list[Outcome]) -> str:
    seen: list[str] = []
    for item in items:
        for action in item.actions:
            if not action.startswith("推送: 已是最新") and action not in seen:
                seen.append(action)
    return escape(", ".join(seen) or "已是最新")


def render_email(config: Config, report: RunReport, run_url: str = "", now: datetime | None = None) -> Email:
    """
    Render the daily sync email from an unmasked report
    .agents/notes/implemented/feature/2026-11-28-mirror-email-report.md
    """
    when = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    day = when.strftime("%Y-%m-%d")
    targets = {target.name: target for target in config.targets}
    by_repo: dict[str, list[Outcome]] = {}
    for item in report.outcomes:
        by_repo.setdefault(item.repo, []).append(item)
    repos = report.repos or [SourceRepo(name, private=items[0].private) for name, items in sorted(by_repo.items())]
    changed = [repo for repo in repos if any(item.changed for item in by_repo.get(repo.name, []))]
    failed = [item for item in report.outcomes if item.error]
    warned = [item for item in report.outcomes if item.warnings and not item.error]
    kind, verdict = _overall(report)
    source_url = f"{config.source.base_url}/{config.source.owner}"
    dry = " (试跑, 未写入)" if report.dry_run else ""
    platform_names = " · ".join(
        PROVIDER_LABELS.get(t.provider, t.name) for t in config.targets if t.name not in report.target_problems
    ) or "无可用平台"

    def chips(repo_name: str) -> str:
        parts = []
        for item in by_repo.get(repo_name, []):
            target = targets.get(item.target)
            if target is None or item.skipped:
                continue
            parts.append(_chip(repo_url(target, item.target_repo), PROVIDER_LABELS.get(target.provider, target.name), _status(item)))
        return "".join(parts) or _badge("skipped", "无")

    def repo_cell(repo_name: str, private: bool) -> str:
        return f"{_link(f'{source_url}/{repo_name}', repo_name, True)} {_visibility(private)}"

    sections = []
    sections.append(_card("目标平台", _table(["平台", "存放位置", "状态"], _target_rows(config, report))))

    if report.source_error or failed or warned:
        rows = []
        if report.source_error:
            rows.append([escape("GitHub"), "-", _badge("failed", "失败"), escape(report.source_error)])
        for item in failed + warned:
            target = targets.get(item.target)
            where = _link(repo_url(target, item.target_repo), item.target) if target else escape(item.target)
            detail = item.error or "; ".join(item.warnings)
            rows.append(
                [
                    escape(item.repo),
                    where,
                    _badge("failed", "失败") if item.error else _badge("warning", "提醒"),
                    f'<span style="font-family:{MONO};font-size:12px;color:{INK["muted_fg"]};">{escape(detail[:400])}</span>',
                ]
            )
        sections.append(_card("失败与提醒", _table(["仓库", "平台", "状态", "原因"], rows)))

    if changed:
        rows = [
            [repo_cell(repo.name, repo.private), _actions_text(by_repo.get(repo.name, [])), chips(repo.name)]
            for repo in changed
        ]
        sections.append(_card(f"今日同步 · {len(changed)} 个仓库有更新", _table(["仓库", "做了什么", "镜像"], rows)))
    else:
        sections.append(_card("今日同步", _empty("今天没有仓库有新的提交、标签或设置变化, 各平台都已是最新.")))

    excluded = len(report.skipped_repos)
    note = f"另有 {excluded} 个仓库按配置规则不同步" if excluded else ""
    rows = [[repo_cell(repo.name, repo.private), chips(repo.name)] for repo in repos]
    sections.append(
        _card(
            f"已配置的仓库 · {len(repos)} 个",
            _table(["仓库", "镜像 (点击打开)"], rows) if rows else _empty("没有仓库在同步范围内."),
            note,
        )
    )

    button = (
        f'<a href="{escape(run_url, quote=True)}" style="display:inline-block;padding:9px 16px;border-radius:10px;'
        f'background:{INK["primary"]};color:#ffffff;font-size:13px;font-weight:500;text-decoration:none;">查看本次运行</a>'
        if run_url
        else ""
    )
    stats = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        + _stat("已配置仓库", len(repos), "")
        + _stat("今日有更新", len(changed), "brand")
        + _stat("失败", len(failed) + (1 if report.source_error else 0), "failed" if failed or report.source_error else "")
        + _stat("提醒", len(warned), "warning" if warned else "")
        + "</tr></table>"
    )
    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(day)} 镜像同步</title></head>
<body style="margin:0;padding:0;background:{INK["background"]};font-family:{FONT};color:{INK["foreground"]};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{INK["background"]};">
<tr><td align="center" style="padding:28px 12px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:680px;">
<tr><td style="padding:0 4px 18px;">
  <div style="font-size:12px;color:{INK["muted_fg"]};letter-spacing:0.02em;">
    <span style="display:inline-block;width:8px;height:8px;border-radius:999px;background:{INK["brand"]};margin-right:6px;"></span>HX-Workflows · 多平台镜像同步</div>
  <div style="margin-top:10px;font-size:22px;font-weight:600;letter-spacing:-0.01em;">{escape(day)} 同步报告{escape(dry)} {_badge(kind, verdict)}</div>
  <div style="margin-top:6px;font-size:13px;color:{INK["muted_fg"]};">{_link(source_url, f"GitHub {config.source.owner}")} → {escape(platform_names)}</div>
</td></tr>
<tr><td style="padding:0 0 16px;">{stats}</td></tr>
{"".join(sections)}
<tr><td style="padding:4px 4px 0;">{button}
  <div style="margin-top:14px;font-size:12px;line-height:18px;color:{INK["muted_fg"]};">
    这封邮件含私有仓库名, 只发到你配置的邮箱, 不会出现在公开的运行日志里. 生成于 {escape(when.strftime("%Y-%m-%d %H:%M"))} (北京时间).</div>
</td></tr>
</table></td></tr></table></body></html>"""

    lines = [f"{day} 多平台镜像同步{dry}: {verdict}", f"来源: {source_url}", "", "目标平台:"]
    for target in config.targets:
        lines.append(f"- {target.name}: {owner_url(target)} {report.target_problems.get(target.name, '正常')}")
    if report.source_error:
        lines += ["", f"错误: {report.source_error}"]
    if failed or warned:
        lines += ["", "失败与提醒:"]
        lines += [f"- {item.repo} @ {item.target}: {item.error or '; '.join(item.warnings)}" for item in failed + warned]
    lines += ["", f"今日有更新 ({len(changed)}):"]
    for repo in changed:
        links = [repo_url(targets[i.target], i.target_repo) for i in by_repo.get(repo.name, []) if i.target in targets and not i.skipped]
        lines.append(f"- {repo.name}: {' '.join(links)}")
    lines += ["", f"已配置的仓库 ({len(repos)}): " + ", ".join(repo.name for repo in repos)]
    if run_url:
        lines += ["", f"运行记录: {run_url}"]

    subject = f"[镜像同步] {day} {'✅' if kind == 'ok' else '⚠️' if kind == 'warning' else '❌'} {verdict} · 已配置 {len(repos)} 个, 今日更新 {len(changed)} 个{dry}"
    return Email(subject=subject, html=html, text="\n".join(lines))


def send_email(
    mail: MailConfig,
    email: Email,
    smtp_factory: Callable[..., smtplib.SMTP] | None = None,
) -> None:
    message = EmailMessage()
    message["Subject"] = email.subject
    message["From"] = mail.mail_from
    message["To"] = ", ".join(mail.mail_to)
    message.set_content(email.text)
    message.add_alternative(email.html, subtype="html")
    factory = smtp_factory or (smtplib.SMTP_SSL if mail.use_ssl else smtplib.SMTP)
    with factory(mail.host, mail.port, timeout=30) as smtp:
        if mail.starttls and not mail.use_ssl:
            smtp.starttls()
        smtp.login(mail.username, mail.password)
        smtp.send_message(message)
