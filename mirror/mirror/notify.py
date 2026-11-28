"""Daily sync email: which repositories are configured, which changed today, what failed, and where each copy lives.

The email goes only to the owner's mailbox, so it shows real private repository names; nothing here is written to
logs or artifacts. Styling follows the HX-UI 2 dark theme tokens, converted to hex and inlined for mail clients.
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

# HX-UI 2 (next/src/styles/theme.css) dark tokens (.dark), OKLCH converted to sRGB hex; soft = 16% over card
INK = {
    "background": "#090a0c",
    "card": "#111114",
    "foreground": "#f4f5f6",
    "muted": "#1c1d20",
    "muted_fg": "#9b9ea6",
    "border": "#262729",
    "primary": "#f1f2f3",
    "primary_fg": "#111114",
    "brand": "#7989ff",
    "brand_soft": "#212539",
    "success": "#42c070",
    "success_soft": "#192d23",
    "warning": "#f5ae39",
    "warning_soft": "#352b1a",
    "destructive": "#fc5855",
    "destructive_soft": "#361d1e",
}
FONT = '"Geist", ui-sans-serif, system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif'
MONO = '"Geist Mono", "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace'
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
        "ok": (INK["success_soft"], INK["success"]),
        "warning": (INK["warning_soft"], INK["warning"]),
        "failed": (INK["destructive_soft"], INK["destructive"]),
        "skipped": (INK["muted"], INK["muted_fg"]),
        "brand": (INK["brand_soft"], INK["brand"]),
    }
    background, color = colors.get(kind, colors["skipped"])
    return (
        f'<span style="display:inline-block;padding:1px 7px;border-radius:999px;background:{background};'
        f'color:{color};font-size:11px;line-height:17px;font-weight:500;white-space:nowrap;vertical-align:1px;">{escape(text)}</span>'
    )


def _link(url: str, text: str, mono: bool = False, muted: bool = False) -> str:
    family = f"font-family:{MONO};" if mono else ""
    color = INK["muted_fg"] if muted else INK["foreground"]
    return (
        f'<a href="{escape(url, quote=True)}" style="color:{color};text-decoration:none;word-break:break-all;{family}">'
        f"{escape(text)}</a>"
    )


def _dot(kind: str) -> str:
    color = {"ok": INK["success"], "warning": INK["warning"], "failed": INK["destructive"], "brand": INK["brand"]}.get(
        kind, INK["muted_fg"]
    )
    return (
        f'<span style="display:inline-block;width:7px;height:7px;border-radius:999px;background:{color};'
        'margin-right:7px;vertical-align:1px;"></span>'
    )


def _card(title: str, body: str, aside: str = "") -> str:
    side = f'<span style="float:right;color:{INK["muted_fg"]};font-size:12px;font-weight:400;">{aside}</span>' if aside else ""
    return (
        f'<tr><td style="padding:0 0 12px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{INK["card"]};border:1px solid {INK["border"]};border-radius:12px;">'
        f'<tr><td style="padding:14px 16px 4px;font-size:14px;font-weight:600;color:{INK["foreground"]};">{side}'
        f"{escape(title)}</td></tr>"
        f'<tr><td style="padding:4px 16px 14px;">{body}</td></tr></table></td></tr>'
    )


def _empty(text: str) -> str:
    return f'<div style="padding:4px 0;color:{INK["muted_fg"]};font-size:13px;">{escape(text)}</div>'


def _stat(label: str, value: int, kind: str) -> str:
    color = {"failed": INK["destructive"], "warning": INK["warning"], "brand": INK["brand"]}.get(kind, INK["foreground"])
    return (
        f'<td width="25%" align="center" style="padding:10px 2px;"><div style="font-size:20px;line-height:24px;'
        f'font-weight:600;color:{color};">{value}</div><div style="margin-top:2px;color:{INK["muted_fg"]};font-size:11px;'
        f'white-space:nowrap;">{escape(label)}</div></td>'
    )


def _tree(lines: list[tuple[str, str]]) -> str:
    """Folder-style rows: (content, note). The last child gets a closing branch."""
    rows = []
    for index, (content, note) in enumerate(lines):
        branch = "└" if index == len(lines) - 1 else "├"
        right = f'<td align="right" valign="top" style="padding:3px 0 3px 8px;white-space:nowrap;">{note}</td>' if note else "<td></td>"
        rows.append(
            f'<tr><td width="18" valign="top" style="padding:3px 0;color:{INK["border"]};font-family:{MONO};font-size:13px;'
            f'line-height:20px;">{branch}</td><td valign="top" style="padding:3px 0;font-size:13px;line-height:20px;">'
            f"{content}</td>{right}</tr>"
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 4px 2px;">'
        + "".join(rows)
        + "</table>"
    )


def _folder(title: str, meta: str, children: str) -> str:
    return (
        f'<div style="margin:8px 0 2px;font-size:13px;line-height:20px;color:{INK["foreground"]};">'
        f'<span style="font-size:14px;margin-right:6px;">📁</span>{title}'
        f'<span style="color:{INK["muted_fg"]};"> {meta}</span></div>{children}'
    )


def _private_mark(private: bool) -> str:
    return f'<span style="color:{INK["muted_fg"]};font-size:11px;margin-left:6px;">私有</span>' if private else ""


def _actions_text(items: list[Outcome]) -> str:
    seen: list[str] = []
    for item in items:
        for action in item.actions:
            if not action.startswith("推送: 已是最新") and action not in seen:
                seen.append(action)
    return ", ".join(seen) or "已是最新"


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

    def mirror_links(repo_name: str) -> str:
        parts = []
        for item in by_repo.get(repo_name, []):
            target = targets.get(item.target)
            if target is None or item.skipped:
                continue
            label = PROVIDER_LABELS.get(target.provider, target.name)
            parts.append(_dot(_status(item)) + _link(repo_url(target, item.target_repo), label, muted=True))
        return '<span style="margin-left:10px;"></span>'.join(parts)

    sections = []

    if report.source_error or failed or warned:
        rows = []
        if report.source_error:
            rows.append((f'{_dot("failed")}<b style="font-weight:500;">GitHub</b>', ""))
            rows.append((f'<span style="color:{INK["muted_fg"]};">{escape(report.source_error)}</span>', ""))
        for item in failed + warned:
            target = targets.get(item.target)
            where = _link(repo_url(target, item.target_repo), item.target, muted=True) if target else escape(item.target)
            detail = (item.error or "; ".join(item.warnings))[:300]
            rows.append(
                (
                    f'{_dot("failed" if item.error else "warning")}{escape(item.repo)}'
                    f'<span style="color:{INK["muted_fg"]};"> › </span>{where}'
                    f'<div style="margin-top:2px;font-family:{MONO};font-size:11px;line-height:16px;color:{INK["muted_fg"]};'
                    f'word-break:break-all;">{escape(detail)}</div>',
                    "",
                )
            )
        sections.append(_card("失败与提醒", _tree(rows), str(len(failed) + len(warned) + (1 if report.source_error else 0))))

    if changed:
        rows = [
            (
                f'{_link(f"{source_url}/{repo.name}", repo.name, True)}{_private_mark(repo.private)}'
                f'<div style="color:{INK["muted_fg"]};font-size:12px;">{escape(_actions_text(by_repo.get(repo.name, [])))}</div>'
                f'<div style="font-size:12px;">{mirror_links(repo.name)}</div>',
                "",
            )
            for repo in changed
        ]
        sections.append(_card(f"今日同步 · {len(changed)} 个仓库有更新", _tree(rows)))
    else:
        sections.append(_card("今日同步", _empty("今天没有仓库有新的提交、标签或设置变化, 各平台都已是最新.")))

    folders = []
    private_names = {repo.name for repo in repos if repo.private}
    for target in config.targets:
        provider = PROVIDER_LABELS.get(target.provider, target.provider)
        location = _link(owner_url(target), f"{provider} · {target.owner}")
        problem = report.target_problems.get(target.name, "")
        items = [item for item in report.outcomes if item.target == target.name]
        if problem:
            kind_ = "skipped" if problem.startswith("跳过") else "failed"
            folders.append(_folder(location, _badge(kind_, "跳过" if kind_ == "skipped" else "失败"), _tree([(
                f'<span style="color:{INK["muted_fg"]};font-size:12px;">{escape(problem.removeprefix("跳过: "))}</span>', "")])))
            continue
        ok = sum(1 for item in items if _status(item) == "ok")
        rows = []
        for item in sorted(items, key=lambda i: i.repo.lower()):
            if item.skipped:
                continue
            note = _badge("brand", "更新") if item.changed else ""
            rows.append((
                _dot(_status(item)) + _link(repo_url(target, item.target_repo), item.target_repo, True)
                + _private_mark(item.repo in private_names), note))
        folders.append(_folder(location, f"{ok}/{len(rows)}", _tree(rows) if rows else _empty("没有仓库")))
    excluded = len(report.skipped_repos)
    tail = (
        f'<div style="margin-top:8px;color:{INK["muted_fg"]};font-size:12px;">另有 {excluded} 个仓库按配置规则不同步</div>'
        if excluded
        else ""
    )
    sections.append(_card(f"已配置的仓库 · {len(repos)} 个", "".join(folders) + tail, "点仓库名打开镜像"))

    button = (
        f'<a href="{escape(run_url, quote=True)}" style="display:inline-block;padding:9px 16px;border-radius:10px;'
        f'background:{INK["primary"]};color:{INK["primary_fg"]};font-size:13px;font-weight:500;text-decoration:none;">查看本次运行</a>'
        if run_url
        else ""
    )
    stats = (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{INK["card"]};'
        f'border:1px solid {INK["border"]};border-radius:12px;"><tr>'
        + _stat("已配置", len(repos), "")
        + _stat("今日更新", len(changed), "brand")
        + _stat("失败", len(failed) + (1 if report.source_error else 0), "failed" if failed or report.source_error else "")
        + _stat("提醒", len(warned), "warning" if warned else "")
        + "</tr></table>"
    )
    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><meta name="supported-color-schemes" content="dark">
<title>{escape(day)} 镜像同步</title>
<style>:root{{color-scheme:dark;}} @media (max-width:480px){{.wrap{{padding:14px 8px !important;}}}}</style></head>
<body style="margin:0;padding:0;background:{INK["background"]};font-family:{FONT};color:{INK["foreground"]};-webkit-text-size-adjust:100%;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{INK["background"]}" style="background:{INK["background"]};">
<tr><td class="wrap" align="center" style="padding:24px 12px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;">
<tr><td style="padding:0 2px 14px;">
  <div style="font-size:12px;color:{INK["muted_fg"]};">{_dot("brand")}HX-Workflows · 多平台镜像同步</div>
  <div style="margin-top:8px;font-size:20px;line-height:28px;font-weight:600;color:{INK["foreground"]};">{escape(day)} 同步报告{escape(dry)}</div>
  <div style="margin-top:6px;">{_badge(kind, verdict)}</div>
  <div style="margin-top:8px;font-size:12px;color:{INK["muted_fg"]};">{_link(source_url, f"GitHub · {config.source.owner}", muted=True)} → {escape(platform_names)}</div>
</td></tr>
<tr><td style="padding:0 0 12px;">{stats}</td></tr>
{"".join(sections)}
<tr><td style="padding:4px 2px 0;">{button}
  <div style="margin-top:12px;font-size:11px;line-height:17px;color:{INK["muted_fg"]};">
    含私有仓库名, 只发到你配置的邮箱, 不会出现在公开的运行日志里. 生成于 {escape(when.strftime("%Y-%m-%d %H:%M"))} (北京时间).</div>
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
