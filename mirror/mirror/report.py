"""Render a run as Markdown (Actions job summary) and JSON (artifact)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, replace
from pathlib import Path

from mirror.run import Outcome, RunReport


def masked_name(name: str) -> str:
    return "私有仓库-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:6]


def mask_report(report: RunReport) -> RunReport:
    """The workflow runs in a public repository, so private repository names never reach logs or artifacts."""
    names = sorted(report.private_repos, key=len, reverse=True)
    if not names:
        return report
    pattern = re.compile("|".join(re.escape(name) for name in names))

    def hide(text: str) -> str:
        return pattern.sub(lambda match: masked_name(match.group(0)), text)

    def hide_outcome(item: Outcome) -> Outcome:
        return replace(
            item,
            repo=hide(item.repo) if item.private else item.repo,
            target_repo=hide(item.target_repo) if item.private else item.target_repo,
            error=hide(item.error),
            actions=[hide(text) for text in item.actions],
            warnings=[hide(text) for text in item.warnings],
        )

    return replace(
        report,
        outcomes=[hide_outcome(item) for item in report.outcomes],
        repos=[replace(repo, name=masked_name(repo.name), description="") if repo.private else repo for repo in report.repos],
        skipped_repos={(masked_name(k) if k in report.private_repos else k): v for k, v in report.skipped_repos.items()},
        target_problems={k: hide(v) for k, v in report.target_problems.items()},
        source_error=hide(report.source_error),
        private_repos=set(),
    )


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _status(item: Outcome) -> str:
    if item.error:
        return "❌ 失败"
    if item.skipped:
        return "⏭️ 跳过"
    if item.warnings:
        return "⚠️ 完成"
    return "✅ 完成"


def render_markdown(report: RunReport) -> str:
    title = "## 多平台镜像同步" + (" (试跑, 未写入)" if report.dry_run else "")
    lines = [title, ""]
    if report.source_error:
        lines += [f"**{report.source_error}**", ""]
    failed = sum(1 for item in report.outcomes if item.error)
    done = sum(1 for item in report.outcomes if not item.error and not item.skipped)
    repos = len({item.repo for item in report.outcomes})
    lines += [f"{repos} 个仓库, {done} 项完成, {failed} 项失败.", ""]
    if report.target_problems:
        lines += ["### 目标平台", ""]
        lines += [f"- `{name}`: {problem}" for name, problem in sorted(report.target_problems.items())]
        lines.append("")
    if report.outcomes:
        lines += ["### 明细", "", "| 仓库 | 平台 | 目标 | 状态 | 说明 |", "| --- | --- | --- | --- | --- |"]
        ordered = sorted(report.outcomes, key=lambda item: (not item.error, item.repo.lower(), item.target))
        for item in ordered:
            note = item.error or item.skipped or ", ".join(item.actions + item.warnings) or "已是最新"
            lines.append(
                f"| {_cell(item.repo)} | {_cell(item.target)} | {_cell(item.target_repo)} | {_status(item)} | {_cell(note)} |"
            )
        lines.append("")
    if report.skipped_repos:
        lines += ["<details><summary>未同步的源仓库</summary>", ""]
        lines += [f"- `{name}`: {reason}" for name, reason in sorted(report.skipped_repos.items())]
        lines += ["", "</details>", ""]
    return "\n".join(lines)


def write_reports(report: RunReport, output: Path, summary_file: str | None, reveal_private: bool = False) -> None:
    if not reveal_private:
        report = mask_report(report)
    output.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(report)
    (output / "report.md").write_text(markdown, encoding="utf-8")
    payload = {
        "dry_run": report.dry_run,
        "failed": report.failed,
        "source_error": report.source_error,
        "target_problems": report.target_problems,
        "skipped_repos": report.skipped_repos,
        "outcomes": [asdict(item) for item in report.outcomes],
    }
    (output / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")
