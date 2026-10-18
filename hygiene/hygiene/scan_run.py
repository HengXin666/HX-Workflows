"""Scan repositories and publish one Chinese review issue plus status cards."""

from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hygiene.config import load_config
from hygiene.decide import classify
from hygiene.github_api import GitHub, GitHubError
from hygiene.models import BranchInfo, Config, RepoSnapshot, Report
from hygiene.render import badge_files, pending_selection, render_issue

CST = timezone(timedelta(hours=8))


def scan(config_path: Path, badges_path: Path, repo_limit: str, force_new: bool) -> int:
    config = load_config(config_path)
    hygiene_token = os.environ.get("HYGIENE_TOKEN", "")
    github_token = os.environ.get("GITHUB_TOKEN", "")
    if not hygiene_token or not github_token:
        print("缺少 HYGIENE_TOKEN 或 GITHUB_TOKEN.")
        return 1
    reader = GitHub(hygiene_token)
    writer = GitHub(github_token)
    _print_quota(reader)
    wanted = {part.strip() for part in repo_limit.split(",") if part.strip()}
    payloads = reader.list_owner_repos(config.owner)
    names = {str(item.get("name")) for item in payloads}
    snapshots = _collect(reader, config, payloads, wanted)
    now = datetime.now(CST)
    scanned_at = now.strftime("%Y-%m-%d %H:%M +0800")
    report = classify(snapshots, config, scanned_at, _run_url())
    for name in sorted(wanted - names):
        report.errors.append(f"未找到仓库 {name}")
    _write_outputs(report, badges_path)
    _publish_issue(writer, config, report, force_new, now)
    print(
        f"扫描完成: 可删分支 {len(report.branches_deletable)}, "
        f"过期 Skill {len(report.skills_outdated)}, 失败 {len(report.errors)}"
    )
    return 1 if report.errors else 0


def _collect(reader: GitHub, config: Config, payloads: list[dict], wanted: set[str]) -> list[RepoSnapshot]:
    selected = []
    for payload in payloads:
        name = str(payload.get("name") or "")
        if wanted and name not in wanted:
            continue
        selected.append(payload)
    snapshots: list[RepoSnapshot] = []
    to_fetch: list[dict] = []
    for payload in selected:
        snapshot = _bare_snapshot(payload)
        if _skip_fetch(snapshot, config):
            snapshots.append(snapshot)
        else:
            to_fetch.append(payload)
    with ThreadPoolExecutor(max_workers=4) as pool:
        fetched = list(pool.map(lambda item: _fetch(reader, config, item), to_fetch))
    snapshots.extend(fetched)
    return sorted(snapshots, key=lambda item: item.repo)


def _skip_fetch(snapshot: RepoSnapshot, config: Config) -> bool:
    if snapshot.repo in config.ignored_repositories:
        return True
    if snapshot.fork and not config.include_forks:
        return True
    if snapshot.archived and not config.include_archived:
        return True
    return False


def _bare_snapshot(payload: dict) -> RepoSnapshot:
    owner = str((payload.get("owner") or {}).get("login") or "")
    return RepoSnapshot(
        owner=owner,
        repo=str(payload.get("name") or ""),
        default_branch=str(payload.get("default_branch") or "main"),
        archived=bool(payload.get("archived")),
        fork=bool(payload.get("fork")),
    )


def _fetch(reader: GitHub, config: Config, payload: dict) -> RepoSnapshot:
    snapshot = _bare_snapshot(payload)
    print(f"正在扫描 {snapshot.owner}/{snapshot.repo}")
    errors: list[str] = []
    branches: list[BranchInfo] = []
    pull_heads: list[str] = []
    skills = []
    try:
        raw_branches = reader.list_branches(snapshot.owner, snapshot.repo)
        pull_heads = reader.open_pr_heads(snapshot.owner, snapshot.repo)
        heads = [str(item.get("name")) for item in raw_branches if item.get("name") != snapshot.default_branch]
        compared = reader.compare_to_base(snapshot.owner, snapshot.repo, snapshot.default_branch, heads) if heads else {}
        for raw in raw_branches:
            name = str(raw.get("name") or "")
            info = compared.get(name, {})
            failed = "error" in info
            branches.append(
                BranchInfo(
                    name=name,
                    protected=bool(raw.get("protected")),
                    ahead=None if failed else info.get("ahead"),
                    behind=None if failed else info.get("behind"),
                    status=None if failed else info.get("status"),
                    compare_error=str(info.get("error")) if failed else None,
                )
            )
    except GitHubError as error:
        errors.append(f"分支扫描失败: {error}")
    try:
        skills, skill_errors = reader.read_skills(
            snapshot.owner,
            snapshot.repo,
            snapshot.default_branch,
            config.skill_roots,
        )
        errors.extend(skill_errors)
    except GitHubError as error:
        errors.append(f"Skill 扫描失败: {error}")
    return RepoSnapshot(
        owner=snapshot.owner,
        repo=snapshot.repo,
        default_branch=snapshot.default_branch,
        archived=snapshot.archived,
        fork=snapshot.fork,
        branches=tuple(branches),
        open_pr_heads=tuple(pull_heads),
        skills=tuple(skills),
        errors=tuple(errors),
    )


def _write_outputs(report: Report, badges_path: Path) -> None:
    badges_path.mkdir(parents=True, exist_ok=True)
    for name, content in badge_files(report).items():
        (badges_path / name).write_text(content, encoding="utf-8")
    _export_png(badges_path / "summary.svg", badges_path / "summary.png")
    output = badges_path.parent / "output" / "report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _export_png(svg_path: Path, png_path: Path) -> None:
    try:
        subprocess.run(
            ["rsvg-convert", "-w", "920", str(svg_path), "-o", str(png_path)],
            check=True,
            capture_output=True,
        )
    except FileNotFoundError:
        print("未安装 rsvg-convert, 跳过 PNG 卡片.")
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"PNG 卡片渲染失败: {detail}") from error


def _publish_issue(writer: GitHub, config: Config, report: Report, force_new: bool, now: datetime) -> None:
    owner = config.owner
    repo = config.report_repository
    writer.ensure_label(owner, repo, "hygiene-report", "1f6feb", "仓库卫生审阅单")
    writer.ensure_label(owner, repo, "apply", "d29922", "执行已勾选的仓库卫生修复")
    body = render_issue(report)
    title = f"仓库卫生审阅 {now.strftime('%Y-%m-%d %H:%M')}"
    existing = writer.list_open_report_issues(owner, repo)
    if force_new:
        for issue in existing:
            number = int(issue["number"])
            writer.comment(owner, repo, number, "已由新的审阅单取代.")
            writer.update_issue(owner, repo, number, state="closed")
        created = writer.create_issue(owner, repo, title, body)
        print(f"已新建审阅单: {created.get('html_url')}")
        return
    if not existing:
        created = writer.create_issue(owner, repo, title, body)
        print(f"已新建审阅单: {created.get('html_url')}")
        return
    current = max(existing, key=lambda item: int(item["number"]))
    number = int(current["number"])
    if pending_selection(str(current.get("body") or "")):
        writer.comment(
            owner,
            repo,
            number,
            "已有未执行的勾选, 没有覆盖审阅单. 状态卡片已按这次扫描更新. "
            "如果要丢弃勾选并重开, 重新运行扫描并打开 force_new.",
        )
        print(f"保留现有审阅单: {current.get('html_url')}")
        return
    writer.update_issue(owner, repo, number, body=body)
    print(f"已更新审阅单: {current.get('html_url')}")


def _print_quota(reader: GitHub) -> None:
    try:
        rate = reader.request("GET", "/rate_limit")
        resources = rate.get("resources") or {}
        core = (resources.get("core") or {}).get("remaining")
        graphql = (resources.get("graphql") or {}).get("remaining")
        print(f"API 剩余额度: REST {core}, GraphQL {graphql}")
    except GitHubError as error:
        print(f"无法读取 API 额度: {error}")


def _run_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if repository and run_id:
        return f"{server}/{repository}/actions/runs/{run_id}"
    return ""
