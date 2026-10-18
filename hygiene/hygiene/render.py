"""Chinese review issue and status cards."""

from __future__ import annotations

import json
import re
from typing import Any

from hygiene.models import BranchFinding, Report, SkillFinding

CHECK_LINE = re.compile(r"^- \[(?P<mark>[ xX])\] (?P<text>.*)$", re.M)
ITEM_ID = re.compile(r"<!--id:(?P<id>[^>]+?)-->")
DATA_START = "<!-- hygiene-data\n"
DATA_END = "\n-->"
ISSUE_LIMIT = 60000

CARD_WIDTH = 640
CARD_HEIGHT = 228


def _scope(report: Report) -> str:
    parts = ["未归档", "非 fork"]
    if report.repos_private:
        parts.append("包含私有仓库, 名称会随这个公开仓库公开")
    else:
        parts.append("非私有")
    if report.active_within_days > 0:
        parts.append(f"最近 {report.active_within_days} 天有推送")
    return "、".join(parts)


def _apply_hint(report: Report) -> str:
    url = one_line(report.apply_url)
    if not url:
        return "给这张 Issue 加上 `apply` 标签, 或到 Actions 里手动运行「仓库卫生修复」."
    return (
        f"打开 [执行修复]({url}) , 点 Run workflow. "
        "审阅单编号留空会使用最新一张审阅单. 也可以给这张 Issue 加上 `apply` 标签."
    )


def one_line(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ").replace("-->", "")
    text = text.replace("`", "")
    return " ".join(text.split())


def render_issue(report: Report, limit: int = ISSUE_LIMIT) -> str:
    while True:
        body = _render_issue(report)
        if len(body) <= limit:
            return body
        shrunk = False
        for items, label in (
            (report.branches_deletable, "可删分支"),
            (report.skills_outdated, "过期 Skill"),
            (report.skills_diverged, "同版本分叉"),
            (report.skills_unversioned, "没有版本号"),
            (report.skills_version_only, "仅版本号不同"),
            (report.errors, "扫描失败"),
        ):
            if items:
                items.pop()
                note = f"{label}因审阅单长度被截断"
                if note not in report.truncated:
                    report.truncated.append(note)
                shrunk = True
                break
        if not shrunk:
            return body[: limit - 20] + "\n\n审阅单已截断.\n"


def _render_issue(report: Report) -> str:
    lines = [
        "# 仓库卫生审阅",
        "",
        f"扫描时间: {report.scanned_at}",
        f"扫描范围: {_scope(report)}. 运行记录: {one_line(report.run_url) or '本地'}",
        "",
        "勾选要处理的项, 然后执行修复. 只勾选不会执行.",
        _apply_hint(report),
        "执行前会再检查一次. 分支必须仍然是完全合入默认分支, Skill 必须仍然低于权威版本.",
        "",
        "## 权威 Skill",
        "",
        "| Skill | 版本 | 仓库 | 目录 | 提交 |",
        "| --- | --- | --- | --- | --- |",
    ]
    if report.canonical:
        for item in report.canonical:
            version = item.version or "无版本号"
            commit = (item.commit_sha or "")[:7] or "-"
            note = "仅供参考" if item.reference_only else version
            lines.append(
                f"| `{one_line(item.skill_name)}` | {one_line(note)} | `{one_line(item.repo)}` | "
                f"`{one_line(item.path)}` | `{one_line(commit)}` |"
            )
    else:
        lines.append("| 无 | - | - | - | - |")
    lines.extend(["", "## 可删分支", "", "这些分支相对默认分支没有自己的提交, 也没有打开的拉取请求.", ""])
    lines.extend(_branch_lines(report.branches_deletable))
    lines.extend(["", "## 过期 Skill", "", "权威副本是最高版本号. 版本相同时, 优先配置里的仓库, 再比最后提交时间.", ""])
    lines.extend(_skill_lines(report.skills_outdated))
    lines.extend(["", "## 只报告, 不自动处理", "", "### 同版本但内容不同", ""])
    lines.extend(_plain_skills(report.skills_diverged, "同版本分叉, 不会覆盖."))
    lines.extend(["", "### 没有版本号", ""])
    lines.extend(_plain_skills(report.skills_unversioned, "缺少 metadata.version, 不会自动替换."))
    lines.extend(["", "### 内容相同, 只有版本号不同", ""])
    lines.extend(_plain_skills(report.skills_version_only, "目录内容一致."))
    lines.extend(
        [
            "",
            "## 扫描计数",
            "",
            f"- 纳入仓库: {report.repos_considered}",
            f"- 跳过 fork: {report.repos_skipped_fork}",
            f"- 跳过归档: {report.repos_skipped_archived}",
            f"- 跳过忽略名单: {report.repos_skipped_ignored}",
            f"- 跳过私有仓库: {report.repos_skipped_private}",
            f"- 纳入的私有仓库: {report.repos_private}",
            f"- 跳过一年内没有推送的仓库: {report.repos_skipped_stale}",
            f"- 跳过受保护分支: {report.branches_skipped_protected}",
            f"- 跳过仍有拉取请求的分支: {report.branches_skipped_pr}",
            f"- 还有未合入提交的分支: {report.branches_not_merged}",
            f"- 比较失败的分支: {report.branches_compare_failed}",
        ]
    )
    if report.truncated:
        lines.extend(["", "## 审阅单容量", ""])
        lines.extend(f"- {item}" for item in report.truncated)
    if report.errors:
        lines.extend(["", "## 扫描失败", ""])
        lines.extend(f"- {item}" for item in report.errors)
    lines.extend(["", "处理完成后, 成功的项会被勾上并注明已处理. 失败的项保持未勾选."])
    body = "\n".join(lines) + "\n"
    payload = json.dumps(issue_payload(report), ensure_ascii=False, sort_keys=True)
    if "-->" in payload:
        payload = payload.replace("-->", "")
    return body + "\n" + DATA_START + payload + DATA_END + "\n"


def issue_payload(report: Report) -> dict[str, Any]:
    items: dict[str, Any] = {}
    for finding in report.branches_deletable:
        items[finding.item_id] = {"kind": "branch"}
    for finding in report.skills_outdated:
        items[finding.item_id] = {
            "kind": "skill",
            "skill_name": one_line(finding.skill_name),
            "canonical_repo": one_line(finding.canonical_repo),
            "canonical_path": one_line(finding.canonical_path),
            "canonical_version": one_line(finding.canonical_version),
            "canonical_tree": one_line(finding.canonical_tree),
        }
    return {"scanned_at": report.scanned_at, "items": items}


def extract_payload(body: str) -> dict[str, Any]:
    start = body.find(DATA_START)
    if start < 0:
        return {}
    end = body.find(DATA_END, start)
    if end < 0:
        return {}
    try:
        payload = json.loads(body[start + len(DATA_START) : end])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def selected_ids(body: str) -> list[str]:
    found: list[str] = []
    for match in CHECK_LINE.finditer(body):
        if match.group("mark").lower() != "x" or "已处理" in match.group("text"):
            continue
        item = ITEM_ID.search(match.group("text"))
        if item:
            found.append(item.group("id"))
    return found


def pending_selection(body: str) -> bool:
    return bool(selected_ids(body))


def unchecked_ids(body: str) -> list[str]:
    found: list[str] = []
    for match in CHECK_LINE.finditer(body):
        if match.group("mark") != " ":
            continue
        item = ITEM_ID.search(match.group("text"))
        if item:
            found.append(item.group("id"))
    return found


def mark_done(body: str, done_ids: set[str], failed_ids: set[str] | None = None) -> str:
    failed = failed_ids or set()

    def replace(match: re.Match[str]) -> str:
        text = match.group("text")
        item = ITEM_ID.search(text)
        if item is None:
            return match.group(0)
        item_id = item.group("id")
        if item_id in done_ids:
            suffix = "" if "已处理" in text else " （已处理）"
            return f"- [x] {text}{suffix}"
        if item_id in failed:
            note = "" if "上次失败" in text else " （上次失败）"
            return f"- [ ] {text}{note}"
        return match.group(0)

    return CHECK_LINE.sub(replace, body)


def _branch_lines(items: list[BranchFinding]) -> list[str]:
    if not items:
        return ["没有可删除的分支."]
    return [
        (
            f"- [ ] <!--id:{item.item_id}--> `{one_line(item.repo)}` 的分支 `{one_line(item.branch)}` "
            f"已全部合入 `{one_line(item.default_branch)}`, ahead {item.ahead}, behind {item.behind}"
        )
        for item in items
    ]


def _skill_lines(items: list[SkillFinding]) -> list[str]:
    if not items:
        return ["没有可自动更新的 Skill."]
    lines = []
    for item in items:
        warning = " 注意: 目标仓库的提交时间比权威副本新." if item.suspicious else ""
        lines.append(
            f"- [ ] <!--id:{item.item_id}--> `{one_line(item.repo)}` 的 `{one_line(item.skill_name)}` "
            f"{one_line(item.version) or '无版本号'} -> {one_line(item.canonical_version) or '无版本号'}, "
            f"权威副本 `{one_line(item.canonical_repo)}` `{one_line(item.canonical_path)}`"
            f"{warning}"
        )
    return lines


def _plain_skills(items: list[SkillFinding], suffix: str) -> list[str]:
    if not items:
        return ["无."]
    return [
        (
            f"- `{one_line(item.repo)}` 的 `{one_line(item.skill_name)}` "
            f"({one_line(item.version) or '无版本号'}) 对比 `{one_line(item.canonical_repo)}` "
            f"({one_line(item.canonical_version) or '无版本号'}). {suffix}"
        )
        for item in items
    ]


def badge_files(report: Report | None, failed: bool = False) -> dict[str, str]:
    if report is None:
        branches = None
        skills = None
        diverged = None
        scanned_at = None
        considered = None
    else:
        branches = report.branch_total
        skills = report.skill_total
        diverged = report.diverged_total
        scanned_at = report.scanned_at
        considered = report.repos_considered
        failed = failed or bool(report.errors)
    summary = _summary_message(branches, skills, failed, scanned_at)
    if report is None:
        summary_color = "lightgrey"
    else:
        summary_color = _color((branches or 0) + (skills or 0), failed)
    return {
        "summary.svg": _summary_svg(branches, skills, diverged, considered, scanned_at, failed),
        "branches.svg": _pill_svg("可删分支", _count_text(branches), _color(branches, failed)),
        "skills.svg": _pill_svg("过期 Skill", _count_text(skills), _color(skills, failed)),
        "summary.json": _endpoint("仓库卫生", summary, summary_color),
        "branches.json": _endpoint("可删分支", _count_text(branches), _color(branches, failed)),
        "skills.json": _endpoint("过期 Skill", _count_text(skills), _color(skills, failed)),
    }


def _summary_message(branches: int | None, skills: int | None, failed: bool, scanned_at: str | None) -> str:
    if scanned_at is None:
        return "尚未扫描"
    if failed:
        return f"部分失败 · 分支 {_count_text(branches)} · Skill {_count_text(skills)}"
    return f"分支 {_count_text(branches)} · Skill {_count_text(skills)}"


def _count_text(value: int | None) -> str:
    return "尚未扫描" if value is None else str(value)


def _color(value: int | None, failed: bool) -> str:
    if value is None and not failed:
        return "lightgrey"
    if failed and value is None:
        return "red"
    if failed:
        return "orange"
    if value == 0:
        return "2ea44f"
    return "d29922"


def _endpoint(label: str, message: str, color: str) -> str:
    payload = {"schemaVersion": 1, "label": label, "message": message, "color": color}
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _summary_svg(
    branches: int | None,
    skills: int | None,
    diverged: int | None,
    considered: int | None,
    scanned_at: str | None,
    failed: bool,
) -> str:
    rows = [
        ("可删分支", _count_text(branches), _hex(branches, failed)),
        ("过期 Skill", _count_text(skills), _hex(skills, failed)),
        ("同版本分叉", _count_text(diverged), _hex(diverged, False)),
    ]
    row_markup = []
    for index, (label, value, color) in enumerate(rows):
        y = 92 + index * 32
        row_markup.append(
            f'<text x="28" y="{y}" fill="#656d76" font-size="16">{label}</text>'
            f'<text x="220" y="{y}" fill="{color}" font-size="16" font-weight="700">{value}</text>'
        )
    status = "尚未扫描" if scanned_at is None else scanned_at
    if failed and scanned_at is not None:
        status = f"{scanned_at} · 部分失败"
    scope = "等待第一次扫描" if considered is None else f"本次纳入 {considered} 个仓库"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{CARD_WIDTH}" height="{CARD_HEIGHT}" viewBox="0 0 {CARD_WIDTH} {CARD_HEIGHT}" role="img" aria-label="仓库卫生">
  <rect width="{CARD_WIDTH}" height="{CARD_HEIGHT}" rx="16" fill="#f6f8fa"/>
  <rect x="1" y="1" width="{CARD_WIDTH - 2}" height="{CARD_HEIGHT - 2}" rx="15" fill="none" stroke="#d0d7de"/>
  <text x="28" y="42" fill="#1f2328" font-size="22" font-weight="700" font-family="Noto Sans CJK SC, WenQuanYi Zen Hei, PingFang SC, Microsoft YaHei, sans-serif">仓库卫生</text>
  <text x="28" y="68" fill="#656d76" font-size="14" font-family="Noto Sans CJK SC, WenQuanYi Zen Hei, PingFang SC, Microsoft YaHei, sans-serif">{scope}</text>
  <g font-family="Noto Sans CJK SC, WenQuanYi Zen Hei, PingFang SC, Microsoft YaHei, sans-serif">
    {"".join(row_markup)}
  </g>
  <text x="28" y="204" fill="#656d76" font-size="13" font-family="Noto Sans CJK SC, WenQuanYi Zen Hei, PingFang SC, Microsoft YaHei, sans-serif">{status}</text>
</svg>
"""


def _pill_svg(label: str, message: str, color: str) -> str:
    fill = {
        "2ea44f": "#2ea44f",
        "d29922": "#d29922",
        "orange": "#d29922",
        "red": "#cf222e",
    }.get(color, "#6e7781")
    width = 280
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="28" viewBox="0 0 {width} 28" role="img" aria-label="{label}">
  <rect width="{width}" height="28" rx="6" fill="#f6f8fa"/>
  <rect x="0.5" y="0.5" width="{width - 1}" height="27" rx="5.5" fill="none" stroke="#d0d7de"/>
  <text x="12" y="19" fill="#656d76" font-size="13" font-family="Noto Sans CJK SC, WenQuanYi Zen Hei, PingFang SC, Microsoft YaHei, sans-serif">{label}</text>
  <text x="168" y="19" fill="{fill}" font-size="13" font-weight="700" font-family="Noto Sans CJK SC, WenQuanYi Zen Hei, PingFang SC, Microsoft YaHei, sans-serif">{message}</text>
</svg>
"""


def _hex(value: int | None, failed: bool) -> str:
    if value is None:
        return "#656d76"
    if failed:
        return "#9a6700"
    if value == 0:
        return "#1a7f37"
    return "#9a6700"
