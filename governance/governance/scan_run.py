"""Scan repositories read-only and publish one Chinese review issue."""

from __future__ import annotations

import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from governance.config import Config, load_config
from governance.decide import RepoPlan, RepoSnapshot, plan_repo, should_skip
from governance.github_api import REPORT_LABEL, GitHub, GitHubError
from governance.render import Report, render_issue, selected_repos, style_distribution

CST = timezone(timedelta(hours=8))


def collect(reader: GitHub, config: Config, wanted: set[str]) -> tuple[list[RepoPlan], Counter, list[str]]:
    payloads = reader.list_owner_repos(config.owner)
    names = {str(item.get("name")) for item in payloads}
    skipped: Counter = Counter()
    chosen: list[dict[str, Any]] = []
    for payload in payloads:
        name = str(payload.get("name") or "")
        if wanted and name not in wanted:
            continue
        bare = RepoSnapshot(
            owner=config.owner,
            repo=name,
            private=bool(payload.get("private")),
            fork=bool(payload.get("fork")),
            archived=bool(payload.get("archived")),
            pushed_at=str(payload.get("pushed_at") or "") or None,
        )
        reason = should_skip(bare, config)
        if reason:
            skipped[reason] += 1
            continue
        chosen.append(payload)

    def one(payload: dict[str, Any]) -> RepoPlan:
        try:
            snapshot = reader.snapshot(payload, config.setup_branch, config.commit_sample_size)
        except GitHubError as error:
            snapshot = RepoSnapshot(owner=config.owner, repo=str(payload.get("name") or ""), private=bool(payload.get("private")))
            snapshot.errors.append(str(error))
        label = snapshot.repo if not snapshot.private else "私有仓库"
        print(f"已读取 {label}")
        return plan_repo(snapshot, config)

    with ThreadPoolExecutor(max_workers=4) as pool:
        plans = list(pool.map(one, chosen))
    errors = [f"未找到仓库 {name}" for name in sorted(wanted - names)]
    return sorted(plans, key=lambda plan: plan.snapshot.repo.lower()), skipped, errors


def plan_summary(plan: RepoPlan) -> dict[str, Any]:
    return {
        "repo": plan.snapshot.repo,
        "private": plan.snapshot.private,
        "empty": plan.snapshot.empty,
        "compliant": plan.compliant,
        "style": plan.style,
        "style_source": plan.style_source,
        "stats": {
            "total": plan.stats.total,
            "conventional": plan.stats.conventional,
            "bracket": plan.stats.bracket,
            "other": plan.stats.other,
            "dominant": plan.stats.dominant,
        },
        "baseline": plan.baseline,
        "settings_patch": plan.settings_patch,
        "labels": [f"{op.kind}:{op.name}->{op.new_name}" for op in plan.label_ops],
        "files": sorted(plan.file_changes),
        "files_skipped": plan.files_skipped,
        "ruleset": plan.ruleset,
        "ruleset_reason": plan.ruleset_reason,
        "setup_pr": plan.snapshot.setup_pr,
        "errors": plan.snapshot.errors,
    }


def write_output(report: Report, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.md").write_text(render_issue(report), encoding="utf-8")
    data = {
        "scanned_at": report.scanned_at,
        "skipped": dict(report.skipped),
        "errors": report.errors,
        "distribution": style_distribution(report.plans),
        "repos": [plan_summary(plan) for plan in report.plans],
    }
    (output / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def scan(config_path: Path, repo_limit: str, force_new: bool, dry_run: bool, output: Path) -> int:
    config = load_config(config_path)
    token = os.environ.get("HYGIENE_TOKEN", "")
    github_token = os.environ.get("GITHUB_TOKEN", "")
    if not token or (not dry_run and not github_token):
        print("缺少 HYGIENE_TOKEN 或 GITHUB_TOKEN.")
        return 1
    reader = GitHub(token)
    wanted = {part.strip() for part in repo_limit.split(",") if part.strip()}
    plans, skipped, errors = collect(reader, config, wanted)
    now = datetime.now(CST)
    report = Report(
        scanned_at=now.strftime("%Y-%m-%d %H:%M +0800"),
        run_url=_run_url(),
        apply_url=f"https://github.com/{config.owner}/{config.report_repository}/actions/workflows/governance-apply.yml",
        active_within_days=config.active_within_days,
        plans=plans,
        skipped=skipped,
        errors=errors,
    )
    write_output(report, output)
    dist = style_distribution(plans)
    print(
        f"扫描完成: 检查 {len(plans)} 个, 待治理 {len(report.pending)}, 已合规 {len(report.compliant)}, "
        f"失败 {len(report.failed) + len(errors)}. 风格: 约定式 {dist['conventional']}, 方括号 {dist['bracket']}, "
        f"无统一 {dist['none']}, 空仓库 {dist['empty']}. 跳过: {dict(skipped)}"
    )
    if dry_run or wanted:
        print(f"只读模式, 没有写审阅单. 结果在 {output}/report.md 和 report.json")
        return 1 if errors or report.failed else 0
    publish(GitHub(github_token), config, report, force_new, now)
    return 1 if errors or report.failed else 0


def publish(writer: GitHub, config: Config, report: Report, force_new: bool, now: datetime) -> None:
    owner, repo = config.owner, config.report_repository
    writer.ensure_label(owner, repo, REPORT_LABEL, "0e8a16", "仓库治理审阅单")
    writer.ensure_label(owner, repo, "apply", "d29922", "执行审阅单里勾选的修复")
    title = f"仓库治理审阅 {now.strftime('%Y-%m-%d %H:%M')}"
    existing = writer.list_open_report_issues(owner, repo)
    if force_new:
        for issue in existing:
            number = int(issue["number"])
            writer.comment(owner, repo, number, "已由新的审阅单取代.")
            writer.update_issue(owner, repo, number, state="closed")
        existing = []
    if existing:
        current = max(existing, key=lambda item: int(item["number"]))
        number = int(current["number"])
        report.issue_number = number
        if selected_repos(str(current.get("body") or "")):
            writer.comment(
                owner,
                repo,
                number,
                "已有未执行的勾选, 没有覆盖审阅单. 要丢弃勾选并重开, 重新运行扫描并打开 force_new.",
            )
            print(f"保留现有审阅单: {current.get('html_url')}")
            return
        writer.update_issue(owner, repo, number, body=render_issue(report))
        print(f"已更新审阅单: {current.get('html_url')}")
        return
    if not report.pending:
        print("没有待治理的仓库, 不新建审阅单.")
        return
    created = writer.request(
        "POST",
        f"/repos/{owner}/{repo}/issues",
        json_body={"title": title, "body": render_issue(report), "labels": [REPORT_LABEL]},
    )
    number = int(created.get("number") or 0)
    if number:
        report.issue_number = number
        try:
            writer.update_issue(owner, repo, number, body=render_issue(report))
        except GitHubError as error:
            writer.comment(owner, repo, number, f"正文没能写入编号 ({error}). 执行时编号填 {number}.")
    print(f"已新建审阅单: {created.get('html_url')}")


def _run_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    return f"{server}/{repository}/actions/runs/{run_id}" if repository and run_id else ""
