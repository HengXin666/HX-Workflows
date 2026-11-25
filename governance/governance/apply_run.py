"""Apply the checked repositories after a fresh read of each one."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from governance.config import Config, load_config
from governance.decide import RepoPlan, plan_repo
from governance.github_api import GitHub, GitHubError
from governance.render import (
    extract_payload,
    mark_done,
    selected_repos,
    unchecked_repos,
)
from governance.templates import setup_body, setup_title

WORKFLOW_HINT = "写入 .github/workflows 需要 HYGIENE_TOKEN 勾选 workflow 权限"


def ruleset_body(config: Config) -> dict[str, Any]:
    return {
        "name": config.ruleset_name,
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": 0,
                    "dismiss_stale_reviews_on_push": False,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": False,
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": False,
                    "required_status_checks": [{"context": config.required_check}],
                },
            },
        ],
    }


def _error_text(error: GitHubError) -> str:
    text = str(error)
    if "workflow" in text.lower():
        text += f" ({WORKFLOW_HINT})"
    return text


def apply_settings(worker: GitHub, plan: RepoPlan) -> tuple[list[str], bool]:
    if not plan.settings_patch:
        return [], True
    snap = plan.snapshot
    try:
        worker.patch_repo(snap.owner, snap.repo, plan.settings_patch)
        return ["设置已更新: " + ", ".join(f"{key}={value}" for key, value in plan.settings_patch.items())], True
    except GitHubError as error:
        return [f"设置失败: {error}"], False


def apply_labels(worker: GitHub, plan: RepoPlan) -> tuple[list[str], bool]:
    snap = plan.snapshot
    done: list[str] = []
    problems: list[str] = []
    for op in plan.label_ops:
        try:
            if op.kind == "create":
                worker.create_label(snap.owner, snap.repo, op.new_name, op.color, op.description)
            else:
                worker.update_label(snap.owner, snap.repo, op.name, op.new_name, op.color, op.description)
            done.append(op.describe() if op.kind != "rename" else f"改名 {op.describe()}")
        except GitHubError as error:
            if op.kind == "create" and error.status == 422:
                continue
            problems.append(f"标签 {op.describe()} 失败: {error}")
    lines = [f"标签已同步 {len(done)} 个"] if done else []
    return lines + problems, not problems


def apply_files(worker: GitHub, config: Config, plan: RepoPlan) -> tuple[list[str], bool]:
    if not plan.file_changes:
        return [], True
    snap = plan.snapshot
    owner, repo, branch = snap.owner, snap.repo, config.setup_branch
    entries = [
        {"path": spec.path, "mode": spec.mode, "type": "blob", "content": spec.content}
        for spec in plan.file_changes.values()
    ]
    title = setup_title(plan.style)
    try:
        pull = worker.open_pull(owner, repo, branch)
        if pull:
            head = str((pull.get("head") or {}).get("sha") or "")
            base_tree = worker.commit_tree(owner, repo, head)
            tree = worker.create_tree(owner, repo, base_tree, entries)
            if tree == base_tree:
                return [f"治理 PR 已是最新: {pull.get('html_url')}"], True
            commit = worker.create_commit(owner, repo, title, tree, head)
            worker.move_ref(owner, repo, branch, commit, force=False)
            return [f"已更新治理 PR: {pull.get('html_url')}"], True
        default_sha = worker.ref_sha(owner, repo, snap.default_branch)
        if not default_sha:
            return ["默认分支不存在, 没有写文件"], False
        base_tree = worker.commit_tree(owner, repo, default_sha)
        tree = worker.create_tree(owner, repo, base_tree, entries)
        if tree == base_tree:
            return ["文件已经一致"], True
        commit = worker.create_commit(owner, repo, title, tree, default_sha)
        if worker.ref_sha(owner, repo, branch):
            worker.move_ref(owner, repo, branch, commit, force=True)
        else:
            worker.create_ref(owner, repo, branch, commit)
        body = setup_body(plan.style, sorted(plan.file_changes), plan.files_skipped, plan.baseline)
        try:
            created = worker.create_pull(owner, repo, title, body, branch, snap.default_branch)
        except GitHubError as error:
            existing = worker.open_pull(owner, repo, branch) if error.status == 422 else None
            if existing is None:
                raise
            created = existing
        return [f"已开治理 PR: {created.get('html_url')}"], True
    except GitHubError as error:
        return [f"写文件失败: {_error_text(error)}"], False


def apply_ruleset(worker: GitHub, config: Config, plan: RepoPlan) -> tuple[list[str], bool]:
    snap = plan.snapshot
    if plan.ruleset not in {"create", "update"}:
        return ([f"规则集: {plan.ruleset_reason}"] if plan.ruleset not in {"ok"} else []), plan.ruleset != "unknown"
    body = ruleset_body(config)
    try:
        if plan.ruleset == "update":
            current = next(item for item in snap.rulesets or [] if item.get("name") == config.ruleset_name)
            worker.update_ruleset(snap.owner, snap.repo, int(current["id"]), body)
            return ["规则集已更新"], True
        worker.create_ruleset(snap.owner, snap.repo, body)
        return ["规则集已创建: 默认分支必须走 PR 且 commit-lint 通过"], True
    except GitHubError as error:
        if error.status in {403, 404, 422}:
            return [f"规则集不可用 ({error.status}), 可能是计划限制或权限不足, 已跳过: {error}"], False
        return [f"规则集失败: {error}"], False


def apply_plan(worker: GitHub, config: Config, plan: RepoPlan) -> tuple[list[str], bool]:
    """
    Run each step for one repository; a failed step never stops the later steps
    """
    lines: list[str] = []
    ok = True
    for step in (
        lambda: apply_settings(worker, plan),
        lambda: apply_labels(worker, plan),
        lambda: apply_files(worker, config, plan),
        lambda: apply_ruleset(worker, config, plan),
    ):
        step_lines, step_ok = step()
        lines += step_lines
        ok = ok and step_ok
    if not lines:
        lines.append("已经合规, 没有改动")
    return lines, ok


def apply_issue(config_path: Path, issue_number: int) -> int:
    config = load_config(config_path)
    actor = os.environ.get("GITHUB_ACTOR", "")
    if actor != config.actor:
        print(f"拒绝执行: 操作者 {actor or '未知'} 不是 {config.actor}.")
        return 1
    token = os.environ.get("HYGIENE_TOKEN", "")
    github_token = os.environ.get("GITHUB_TOKEN", "")
    if not token or not github_token:
        print("缺少 HYGIENE_TOKEN 或 GITHUB_TOKEN.")
        return 1
    writer = GitHub(github_token)
    worker = GitHub(token)
    owner, report_repo = config.owner, config.report_repository
    issue = writer.get_issue(owner, report_repo, issue_number)
    body = str(issue.get("body") or "")
    writer.remove_label(owner, report_repo, issue_number, "apply")
    chosen = selected_repos(body)
    if not chosen:
        writer.comment(owner, report_repo, issue_number, "没有勾选任何仓库, 未做修改.")
        print("没有勾选任何仓库.")
        return 0
    reviewed = extract_payload(body).get("repos") or {}
    notes: list[str] = []
    done: set[str] = set()
    for repo_id in chosen:
        repo_owner, _slash, name = repo_id.partition("/")
        record = reviewed.get(repo_id) if isinstance(reviewed, dict) else None
        if repo_owner != config.owner or not name or not isinstance(record, dict) or name in config.ignored_repositories:
            notes.append(f"### `{repo_id}`\n\n- 拒绝: 不在审阅数据里, 或不属于 {config.owner}.")
            continue
        try:
            payload = worker.request("GET", f"/repos/{config.owner}/{name}")
            snapshot = worker.snapshot(payload, config.setup_branch, config.commit_sample_size)
        except GitHubError as error:
            notes.append(f"### `{name}`\n\n- 读取失败: {error}")
            continue
        if snapshot.errors:
            notes.append(f"### `{name}`\n\n- 读取不完整, 这次不改: {'; '.join(snapshot.errors)}")
            continue
        plan = plan_repo(snapshot, config, str(record.get("style") or "") or None)
        lines, ok = apply_plan(worker, config, plan)
        notes.append(f"### `{name}`\n\n" + "\n".join(f"- {line}" for line in lines))
        if ok:
            done.add(repo_id)
        print(f"{name}: {'完成' if ok else '有失败'}")
    updated = mark_done(body, done, set(chosen) - done)
    state = "closed" if not unchecked_repos(updated) and not (set(chosen) - done) else None
    writer.update_issue(owner, report_repo, issue_number, body=updated, state=state)
    writer.comment(owner, report_repo, issue_number, "## 执行结果\n\n" + "\n\n".join(notes))
    return 0 if done == set(chosen) else 1
