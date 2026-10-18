"""Apply the checked review items after a fresh safety check."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hygiene.config import load_config
from hygiene.decide import is_protected_branch
from hygiene.github_api import GitHub, GitHubError
from hygiene.models import Config, SkillInstance, parse_item_id, parse_version, version_less
from hygiene.render import extract_payload, mark_done, selected_ids, unchecked_ids
from hygiene.trees import GitFile, plan_skill_replacement, safe_branch_name, safe_skill_dir

CST = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class SkillJob:
    item_id: str
    repo: str
    path: str
    source_repo: str
    source_path: str
    source_commit_sha: str
    target_commit_sha: str
    summary: str


def apply_issue(config_path: Path, issue_number: int) -> int:
    config = load_config(config_path)
    actor = os.environ.get("GITHUB_ACTOR", "")
    if actor != config.actor:
        print(f"拒绝执行: 操作者 {actor or '未知'} 不是 {config.actor}.")
        return 1
    hygiene_token = os.environ.get("HYGIENE_TOKEN", "")
    github_token = os.environ.get("GITHUB_TOKEN", "")
    if not hygiene_token or not github_token:
        print("缺少 HYGIENE_TOKEN 或 GITHUB_TOKEN.")
        return 1
    writer = GitHub(github_token)
    worker = GitHub(hygiene_token)
    owner = config.owner
    repo = config.report_repository
    issue = writer.get_issue(owner, repo, issue_number)
    body = str(issue.get("body") or "")
    writer.remove_label(owner, repo, issue_number, "apply")
    chosen = selected_ids(body)
    if not chosen:
        writer.comment(owner, repo, issue_number, "没有勾选任何待处理项, 未做修改.")
        print("没有勾选任何待处理项.")
        return 0
    payload = extract_payload(body)
    known = payload.get("items") if isinstance(payload.get("items"), dict) else {}
    notes: list[str] = []
    done: set[str] = set()
    jobs: list[SkillJob] = []
    for item_id in chosen:
        ref = parse_item_id(item_id)
        record = known.get(item_id) if isinstance(known, dict) else None
        if ref is None or ref.owner != config.owner or not isinstance(record, dict):
            notes.append(f"- 拒绝 `{item_id}`: 编号无效, 或不属于 {config.owner}.")
            continue
        if ref.kind == "branch":
            note, finished = _apply_branch(worker, config, ref.repo, ref.value)
            notes.append(f"- {note}")
            if finished:
                done.add(item_id)
            continue
        if ref.kind != "skill" or not safe_skill_dir(ref.value, config.skill_roots):
            notes.append(f"- 拒绝 `{item_id}`: Skill 路径不在允许的根目录内.")
            continue
        source_repo = str(record.get("canonical_repo") or "")
        source_path = str(record.get("canonical_path") or "")
        if not source_repo or not safe_skill_dir(source_path, config.skill_roots):
            notes.append(f"- 拒绝 `{ref.repo}` `{ref.value}`: 审阅数据里没有合法的权威副本.")
            continue
        note, job = _recheck_skill(
            worker,
            config,
            item_id,
            ref.repo,
            ref.value,
            source_repo,
            source_path,
            record,
        )
        if job is None:
            notes.append(f"- {note}")
            if note.startswith("已经一致"):
                done.add(item_id)
            continue
        jobs.append(job)
    for repository, group in _by_repo(jobs).items():
        group_notes, group_done = _apply_skill_group(worker, config, issue_number, repository, group)
        notes.extend(group_notes)
        done.update(group_done)
    updated = mark_done(body, done, set(chosen))
    state = "closed" if done == set(chosen) and not unchecked_ids(updated) else None
    writer.update_issue(owner, repo, issue_number, body=updated, state=state)
    writer.comment(owner, repo, issue_number, _comment(notes))
    print("\n".join(notes))
    return 1 if len(done) != len(chosen) else 0


def _apply_branch(worker: GitHub, config: Config, repo: str, branch: str) -> tuple[str, bool]:
    try:
        repository = worker.get_repo(config.owner, repo)
        default = str(repository.get("default_branch") or "")
        if not safe_branch_name(branch, default) or is_protected_branch(branch, config):
            return (f"拒绝删除 `{repo}` `{branch}`: 分支受保护或名称不安全.", False)
        try:
            live = worker.get_branch(config.owner, repo, branch)
        except GitHubError as error:
            if error.status == 404:
                return (f"`{repo}` 的分支 `{branch}` 已经不存在.", True)
            raise
        if live.get("protected"):
            return (f"拒绝删除 `{repo}` `{branch}`: 分支受保护.", False)
        if branch in set(worker.open_pr_heads(config.owner, repo)):
            return (f"拒绝删除 `{repo}` `{branch}`: 仍有打开的拉取请求.", False)
        compared = worker.compare_to_base(config.owner, repo, default, [branch])
        info = compared.get(branch) or {}
        if info.get("error") or info.get("ahead") != 0:
            return (f"拒绝删除 `{repo}` `{branch}`: 已不再是完全合入 {default}.", False)
        worker.delete_branch(config.owner, repo, branch)
        return (f"已删除 `{repo}` 的分支 `{branch}`.", True)
    except GitHubError as error:
        if error.status == 404:
            return (f"`{repo}` 的分支 `{branch}` 已经不存在.", True)
        return (f"删除 `{repo}` `{branch}` 失败: {error}", False)


def _recheck_skill(
    worker: GitHub,
    config: Config,
    item_id: str,
    repo: str,
    path: str,
    source_repo: str,
    source_path: str,
    record: dict,
) -> tuple[str, SkillJob | None]:
    try:
        source_default = str(worker.get_repo(config.owner, source_repo).get("default_branch") or "")
        target_default = str(worker.get_repo(config.owner, repo).get("default_branch") or "")
        source = _one_skill(worker, config, source_repo, source_default, source_path)
        target = _one_skill(worker, config, repo, target_default, path)
    except GitHubError as error:
        return (f"复查 `{repo}` `{path}` 失败: {error}", None)
    if source is None or target is None:
        return (f"拒绝 `{repo}` `{path}`: 权威副本或目标 Skill 已经不存在.", None)
    recorded = parse_version(str(record.get("canonical_version") or ""))
    if recorded is not None and source.parsed_version is not None and version_less(source.parsed_version, recorded):
        return (f"拒绝 `{repo}` `{path}`: 权威副本版本回退, 没有覆盖.", None)
    if target.tree_sha == source.tree_sha:
        return (f"已经一致: `{repo}` 的 `{path}` 与权威副本内容相同.", None)
    if target.parsed_version is None or source.parsed_version is None:
        return (f"拒绝 `{repo}` `{path}`: 没有可比较的版本号.", None)
    if not version_less(target.parsed_version, source.parsed_version):
        return (f"拒绝 `{repo}` `{path}`: 目标版本不低于权威副本, 或已经变成同版本分叉.", None)
    summary = (
        f"`{path}` {target.version or '无版本号'} -> {source.version or '无版本号'} "
        f"(来自 `{source_repo}` `{source_path}`)"
    )
    return (
        "",
        SkillJob(
            item_id=item_id,
            repo=repo,
            path=path,
            source_repo=source_repo,
            source_path=source_path,
            source_commit_sha=source.commit_sha or "",
            target_commit_sha=target.commit_sha or "",
            summary=summary,
        ),
    )


def _one_skill(
    worker: GitHub,
    config: Config,
    repo: str,
    default_branch: str,
    path: str,
) -> SkillInstance | None:
    skills, errors = worker.read_skills(config.owner, repo, default_branch, config.skill_roots)
    if errors:
        raise GitHubError(0, "; ".join(errors))
    for skill in skills:
        if skill.path == path:
            return skill
    return None


def _apply_skill_group(
    worker: GitHub,
    config: Config,
    issue_number: int,
    repo: str,
    jobs: list[SkillJob],
) -> tuple[list[str], set[str]]:
    try:
        repository = worker.get_repo(config.owner, repo)
        default = str(repository.get("default_branch") or "")
        entries: list[dict] = []
        upload: dict[str, tuple[str, str]] = {}
        for job in jobs:
            source_default = str(worker.get_repo(config.owner, job.source_repo).get("default_branch") or "")
            source_files = worker.list_skill_files(
                config.owner, job.source_repo, job.source_commit_sha or source_default, job.source_path
            )
            target_files = worker.list_skill_files(
                config.owner, repo, job.target_commit_sha or default, job.path
            )
            remapped = [_remap(job.source_path, job.path, item) for item in source_files]
            for change in plan_skill_replacement(job.path, target_files, remapped):
                if change.sha is None:
                    entries.append({"path": change.path, "mode": change.mode, "type": "blob", "sha": None})
                    continue
                if change.upload_sha:
                    upload[change.upload_sha] = (job.source_repo, change.upload_sha)
                entries.append({"path": change.path, "mode": change.mode, "type": "blob", "sha": change.sha})
        if not entries:
            return ([f"- 已经一致: `{repo}` 没有文件差异."], {job.item_id for job in jobs})
        copied = _upload(worker, config.owner, repo, upload)
        for entry in entries:
            sha = entry.get("sha")
            if isinstance(sha, str) and sha in copied:
                entry["sha"] = copied[sha]
        parent, base_tree = worker.default_commit_tree(config.owner, repo, default)
        if any(job.target_commit_sha and job.target_commit_sha != parent for job in jobs):
            return ([f"- `{repo}` 在复查后发生变化, 已停止更新, 请重新扫描."], set())
        tree_sha = worker.create_tree(config.owner, repo, base_tree, entries)
        stamp = datetime.now(CST).strftime("%Y%m%d%H%M%S")
        branch_prefix = f"hygiene/skills-{issue_number}-"
        existing = [head for head in worker.open_pr_heads(config.owner, repo) if head.startswith(branch_prefix)]
        if existing:
            return ([f"- `{repo}` 已有打开的 Skill 更新拉取请求 `{existing[0]}`, 跳过重复创建."], set())
        branch = f"{branch_prefix}{stamp}"
        message = "更新过期 Skill"
        commit = worker.create_commit(config.owner, repo, message, tree_sha, parent)
        worker.create_branch(config.owner, repo, branch, commit)
        body = "由仓库卫生审阅单执行.\n\n" + "\n".join(f"- {job.summary}" for job in jobs)
        pull = worker.create_pull(config.owner, repo, message, body, branch, default)
        number = int(pull.get("number") or 0)
        url = str(pull.get("html_url") or "")
        try:
            worker.merge_pull(config.owner, repo, number, message)
        except GitHubError as error:
            return (
                [f"- `{repo}` 已打开拉取请求 {url}, 但未能自动合入: {error}"],
                set(),
            )
        try:
            worker.delete_branch(config.owner, repo, branch)
        except GitHubError as error:
            if error.status != 404:
                return ([f"- `{repo}` 已合入, 但删除临时分支失败: {error}"], {job.item_id for job in jobs})
        return ([f"- 已更新并合入 `{repo}`: {url}"], {job.item_id for job in jobs})
    except GitHubError as error:
        return ([f"- 更新 `{repo}` 失败: {error}"], set())


def _remap(source_prefix: str, target_prefix: str, item: GitFile) -> GitFile:
    relative = item.path[len(source_prefix) :]
    return GitFile(path=target_prefix + relative, mode=item.mode, sha=item.sha)


def _upload(worker: GitHub, owner: str, repo: str, blobs: dict[str, tuple[str, str]]) -> dict[str, str]:
    copied: dict[str, str] = {}
    for sha, (source_repo, source_sha) in blobs.items():
        content = worker.get_blob_bytes(owner, source_repo, source_sha)
        copied[sha] = worker.create_blob(owner, repo, content)
    return copied


def _by_repo(jobs: list[SkillJob]) -> dict[str, list[SkillJob]]:
    grouped: dict[str, list[SkillJob]] = {}
    for job in jobs:
        grouped.setdefault(job.repo, []).append(job)
    return grouped


def _comment(notes: list[str]) -> str:
    text = "仓库卫生修复结果:\n\n" + "\n".join(notes)
    if len(text) > 60000:
        return text[:60000] + "\n\n其余结果已截断."
    return text
