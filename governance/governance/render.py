"""Chinese review issue: one checkbox per repository that still needs governance."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from governance.decide import RepoPlan
from governance.labels import summarize
from governance.templates import RenderInput, render_files, setup_title

FULL_SET = render_files(RenderInput("conventional", ("feat",), 72, "", "main", "warn", True, "commit-lint"))
CHECK_LINE = re.compile(r"^(?P<indent>[ \t]*)- \[(?P<mark>[ xX])\] (?P<text>.*)$")
REPO_ID = re.compile(r"<!--repo:(?P<id>[^>]+?)-->")
DATA_START = "<!-- governance-data\n"
DATA_END = "\n-->"
ISSUE_LIMIT = 60000
DONE_NOTE = " （已处理）"
FAILED_NOTE = " （上次失败）"
STYLE_NAMES = {"conventional": "约定式 `type(scope): 描述`", "bracket": "方括号 `[type]: 描述`"}
SOURCE_NAMES = {
    "override": "配置强制",
    "existing": "已接入",
    "reviewed": "审阅单",
    "empty": "空仓库, 用默认",
    "history": "沿用历史",
    "default": "历史无统一风格, 用默认",
}
SETTING_NAMES = {
    "delete_branch_on_merge": "合并后自动删分支",
    "allow_squash_merge": "允许 squash",
    "squash_merge_commit_title": "squash 标题",
    "squash_merge_commit_message": "squash 正文",
    "allow_merge_commit": "关闭 merge commit",
    "allow_rebase_merge": "关闭 rebase 合并",
}
SKIP_NAMES = {
    "ignored": "配置忽略",
    "private": "私有仓库 (未开启)",
    "fork": "fork",
    "archived": "已归档",
    "stale": "超过活跃天数",
}


@dataclass
class Report:
    scanned_at: str
    run_url: str = ""
    apply_url: str = ""
    active_within_days: int = 365
    plans: list[RepoPlan] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    issue_number: int | None = None

    @property
    def pending(self) -> list[RepoPlan]:
        return [plan for plan in self.plans if not plan.compliant and not plan.snapshot.errors]

    @property
    def compliant(self) -> list[RepoPlan]:
        return [plan for plan in self.plans if plan.compliant]

    @property
    def failed(self) -> list[RepoPlan]:
        return [plan for plan in self.plans if plan.snapshot.errors]


def one_line(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ").replace("-->", "").replace("`", "")
    return " ".join(text.split())


def clean(value: object) -> str:
    text = "" if value is None else str(value)
    return " ".join(text.replace("-->", "").split())


def repo_id(plan: RepoPlan) -> str:
    return f"{plan.snapshot.owner}/{plan.snapshot.repo}"


def style_distribution(plans: list[RepoPlan]) -> dict[str, int]:
    counts: Counter = Counter()
    for plan in plans:
        if plan.snapshot.empty or plan.stats.total == 0:
            counts["empty"] += 1
        else:
            counts[plan.stats.dominant] += 1
    return {key: counts.get(key, 0) for key in ("conventional", "bracket", "none", "empty")}


def describe_plan(plan: RepoPlan) -> list[str]:
    lines: list[str] = []
    stats = plan.stats
    sample = (
        f"抽样 {stats.total} 个: 约定式 {stats.conventional}, 方括号 {stats.bracket}, 其他 {stats.other}"
        if stats.total
        else "没有可抽样的提交"
    )
    lines.append(f"提交规范: {STYLE_NAMES[plan.style]} ({SOURCE_NAMES.get(plan.style_source, plan.style_source)}; {sample})")
    if plan.settings_patch:
        names = sorted({SETTING_NAMES.get(key, key) for key in plan.settings_patch})
        lines.append("设置: " + ", ".join(names))
    if plan.label_ops:
        lines.append("标签: " + summarize(plan.label_ops))
    if plan.file_changes:
        pr = plan.snapshot.setup_pr
        where = f"等待合入 PR #{pr.get('number')}, 执行时同步最新内容" if pr else f"新开 PR `{setup_title(plan.style)}`"
        if len(plan.file_changes) == len(FULL_SET):
            lines.append(f"文件 ({where}): 全套 {len(FULL_SET)} 个 (Issue 表单, PR 模板, 提交规范, commit-lint 工作流, 本地钩子)")
        else:
            lines.append(f"文件 ({where}): " + ", ".join(f"`{path.removeprefix('.github/')}`" for path in sorted(plan.file_changes)))
    for item in plan.files_skipped:
        lines.append("跳过 " + item)
    if plan.ruleset not in {"ok"}:
        lines.append("规则集: " + plan.ruleset_reason)
    lines.extend(plan.notes)
    return lines


def _visibility(plan: RepoPlan) -> str:
    return "私有" if plan.snapshot.private else "公开"


def _render(report: Report) -> str:
    plans = report.plans
    dist = style_distribution(plans)
    pending = report.pending
    lines = [
        "## 仓库治理审阅",
        "",
        f"扫描时间: {report.scanned_at}" + (f" · [运行记录]({report.run_url})" if report.run_url else ""),
        "",
        (
            f"范围: 最近 {report.active_within_days} 天有推送的仓库. 共检查 {len(plans)} 个, "
            f"待治理 {len(pending)}, 已合规 {len(report.compliant)}, 失败 {len(report.failed)}."
        ),
        "",
        f"提交风格分布: 约定式 {dist['conventional']}, 方括号 {dist['bracket']}, 无统一风格 {dist['none']}, 空仓库 {dist['empty']}.",
        "",
    ]
    if report.skipped:
        lines += ["跳过: " + ", ".join(f"{SKIP_NAMES.get(key, key)} {count}" for key, count in sorted(report.skipped.items())), ""]
    lines += [
        "### 怎么执行",
        "",
        (
            "勾选要治理的仓库, 然后给这张 Issue 加上 `apply` 标签, 或者在 "
            + (f"[执行治理]({report.apply_url})" if report.apply_url else "「仓库治理修复」工作流")
            + (f" 里填编号 {report.issue_number}." if report.issue_number else " 里运行.")
        ),
        "执行时会重新读取仓库再改. 设置和标签直接改, 文件以 PR 提交, 不直接推默认分支, 不删除任何标签.",
        "",
        "### 待治理",
        "",
    ]
    if not pending:
        lines += ["没有需要治理的仓库.", ""]
    for plan in pending:
        name = one_line(plan.snapshot.repo)
        lines.append(f"- [ ] <!--repo:{one_line(repo_id(plan))}--> `{name}` ({_visibility(plan)})")
        lines += [f"  - {clean(item)}" for item in describe_plan(plan)]
    lines.append("")
    if report.compliant:
        lines += ["<details><summary>已合规 (" + str(len(report.compliant)) + ")</summary>", ""]
        for plan in report.compliant:
            extra = f", {plan.ruleset_reason}" if plan.ruleset != "ok" else ""
            lines.append(f"- `{one_line(plan.snapshot.repo)}` ({_visibility(plan)}, {STYLE_NAMES[plan.style]}{one_line(extra)})")
        lines += ["", "</details>", ""]
    if report.failed or report.errors:
        lines += ["### 扫描失败", ""]
        for plan in report.failed:
            lines.append(f"- `{one_line(plan.snapshot.repo)}`: {one_line('; '.join(plan.snapshot.errors))}")
        lines += [f"- {one_line(item)}" for item in report.errors]
        lines.append("")
    lines += [DATA_START.rstrip("\n"), json.dumps(issue_payload(report), ensure_ascii=False), DATA_END.lstrip("\n"), ""]
    return "\n".join(lines)


def render_issue(report: Report, limit: int = ISSUE_LIMIT) -> str:
    body = _render(report)
    if len(body) <= limit:
        return body
    keep = list(report.plans)
    while len(body) > limit and keep:
        compliant = [plan for plan in keep if plan.compliant]
        keep.remove(compliant[-1] if compliant else keep[-1])
        trimmed = Report(**{**report.__dict__, "plans": keep, "errors": [*report.errors, "审阅单过长, 部分仓库没有列出"]})
        body = _render(trimmed)
    return body


def issue_payload(report: Report) -> dict[str, Any]:
    return {
        "scanned_at": report.scanned_at,
        "repos": {repo_id(plan): {"style": plan.style} for plan in report.pending},
    }


def extract_payload(body: str) -> dict[str, Any]:
    start = body.find(DATA_START)
    if start < 0:
        return {}
    end = body.find(DATA_END, start + len(DATA_START))
    if end < 0:
        return {}
    try:
        payload = json.loads(body[start + len(DATA_START) : end])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _parts(line: str) -> tuple[str, str, str] | None:
    match = CHECK_LINE.match(line)
    if match is None:
        return None
    return match.group("indent"), match.group("mark"), match.group("text")


def selected_repos(body: str) -> list[str]:
    found: list[str] = []
    for line in body.splitlines():
        parts = _parts(line)
        if parts is None or parts[1].lower() != "x" or DONE_NOTE in parts[2]:
            continue
        match = REPO_ID.search(parts[2])
        if match and match.group("id") not in found:
            found.append(match.group("id"))
    return found


def unchecked_repos(body: str) -> list[str]:
    found: list[str] = []
    for line in body.splitlines():
        parts = _parts(line)
        if parts is None or parts[1] != " " or DONE_NOTE in parts[2]:
            continue
        match = REPO_ID.search(parts[2])
        if match:
            found.append(match.group("id"))
    return found


def mark_done(body: str, done: set[str], failed: set[str]) -> str:
    lines = body.split("\n")
    for index, line in enumerate(lines):
        parts = _parts(line)
        if parts is None:
            continue
        match = REPO_ID.search(parts[2])
        if match is None:
            continue
        text = parts[2]
        for note in (DONE_NOTE, FAILED_NOTE):
            text = text.replace(note, "")
        if match.group("id") in done:
            lines[index] = f"{parts[0]}- [x] {text}{DONE_NOTE}"
        elif match.group("id") in failed:
            lines[index] = f"{parts[0]}- [ ] {text}{FAILED_NOTE}"
    return "\n".join(lines)
