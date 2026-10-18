"""Pure decisions for merged branches and drifted skills."""

from __future__ import annotations

import fnmatch

from hygiene.models import (
    BranchFinding,
    CanonicalSkill,
    Config,
    RepoSnapshot,
    Report,
    SkillFinding,
    SkillInstance,
    branch_id,
    skill_id,
    version_equal,
    version_less,
)

SECTION_LIMIT = 80


def _protected(branch: str, config: Config) -> bool:
    if branch in config.protected_branches:
        return True
    return any(fnmatch.fnmatch(branch, pattern) for pattern in config.protected_branch_globs)


def _group_key(skill: SkillInstance) -> str:
    return skill.declared_name or skill.folder


def _preferred_index(repo: str, preferred: tuple[str, ...]) -> int:
    try:
        return preferred.index(repo)
    except ValueError:
        return len(preferred)


def _commit_rank(committed_at: str | None) -> str:
    return committed_at or ""


def classify(snapshots: list[RepoSnapshot], config: Config, scanned_at: str, run_url: str) -> Report:
    report = Report(scanned_at=scanned_at, run_url=run_url)
    considered: list[RepoSnapshot] = []
    for snapshot in snapshots:
        if snapshot.repo in config.ignored_repositories:
            report.repos_skipped_ignored += 1
            continue
        if snapshot.fork and not config.include_forks:
            report.repos_skipped_fork += 1
            continue
        if snapshot.archived and not config.include_archived:
            report.repos_skipped_archived += 1
            continue
        considered.append(snapshot)
        report.errors.extend(f"{snapshot.owner}/{snapshot.repo}: {error}" for error in snapshot.errors)
    report.repos_considered = len(considered)
    _classify_branches(considered, config, report)
    _classify_skills(considered, config, report)
    _truncate(report)
    return report


def _classify_branches(snapshots: list[RepoSnapshot], config: Config, report: Report) -> None:
    findings: list[BranchFinding] = []
    for snapshot in snapshots:
        open_heads = set(snapshot.open_pr_heads)
        for branch in snapshot.branches:
            if branch.name == snapshot.default_branch:
                continue
            if branch.protected or _protected(branch.name, config):
                report.branches_skipped_protected += 1
                continue
            if branch.name in open_heads:
                report.branches_skipped_pr += 1
                continue
            if branch.compare_error or branch.ahead is None:
                report.branches_compare_failed += 1
                report.errors.append(
                    f"{snapshot.owner}/{snapshot.repo}: 分支 `{branch.name}` 比较失败: "
                    f"{branch.compare_error or '没有 ahead 数据'}"
                )
                continue
            if branch.ahead != 0:
                report.branches_not_merged += 1
                continue
            findings.append(
                BranchFinding(
                    item_id=branch_id(snapshot.owner, snapshot.repo, branch.name),
                    owner=snapshot.owner,
                    repo=snapshot.repo,
                    branch=branch.name,
                    default_branch=snapshot.default_branch,
                    ahead=branch.ahead,
                    behind=branch.behind or 0,
                )
            )
    report.branches_deletable = sorted(findings, key=lambda item: (item.repo, item.branch))


def _classify_skills(snapshots: list[RepoSnapshot], config: Config, report: Report) -> None:
    grouped: dict[str, list[SkillInstance]] = {}
    for snapshot in snapshots:
        for skill in snapshot.skills:
            grouped.setdefault(_group_key(skill), []).append(skill)
    for skill_name, copies in sorted(grouped.items()):
        canonical = _pick_canonical(skill_name, copies, config)
        if canonical is None:
            continue
        report.canonical.append(canonical)
        for copy in copies:
            if copy.repo == canonical.repo and copy.path == canonical.path:
                continue
            finding = _finding(skill_name, copy, canonical)
            if copy.tree_sha == canonical.tree_sha:
                if not version_equal(copy.parsed_version, canonical.parsed_version):
                    report.skills_version_only.append(finding)
                continue
            if canonical.reference_only or copy.parsed_version is None or canonical.parsed_version is None:
                report.skills_unversioned.append(finding)
                continue
            if version_equal(copy.parsed_version, canonical.parsed_version):
                report.skills_diverged.append(finding)
                continue
            if version_less(copy.parsed_version, canonical.parsed_version):
                report.skills_outdated.append(finding)


def _pick_canonical(
    skill_name: str,
    copies: list[SkillInstance],
    config: Config,
) -> CanonicalSkill | None:
    versioned = [copy for copy in copies if copy.parsed_version is not None]
    if not versioned:
        if len({copy.tree_sha for copy in copies}) <= 1:
            return None
        chosen = max(copies, key=lambda copy: (_commit_rank(copy.committed_at), copy.repo))
        return _canonical(skill_name, chosen, reference_only=True)
    best = max(copy.parsed_version for copy in versioned if copy.parsed_version is not None)
    winners = [copy for copy in versioned if version_equal(copy.parsed_version, best)]
    chosen = min(
        winners,
        key=lambda copy: (
            _preferred_index(copy.repo, config.preferred_sources),
            0 if copy.committed_at else 1,
            tuple(-ord(char) for char in _commit_rank(copy.committed_at)),
            copy.repo,
            copy.path,
        ),
    )
    return _canonical(skill_name, chosen, reference_only=False)


def _canonical(skill_name: str, copy: SkillInstance, reference_only: bool) -> CanonicalSkill:
    return CanonicalSkill(
        skill_name=skill_name,
        owner=copy.owner,
        repo=copy.repo,
        path=copy.path,
        version=copy.version,
        parsed_version=copy.parsed_version,
        tree_sha=copy.tree_sha,
        commit_sha=copy.commit_sha,
        committed_at=copy.committed_at,
        reference_only=reference_only,
    )


def _finding(skill_name: str, copy: SkillInstance, canonical: CanonicalSkill) -> SkillFinding:
    suspicious = bool(
        copy.committed_at
        and canonical.committed_at
        and copy.committed_at > canonical.committed_at
        and version_less(copy.parsed_version, canonical.parsed_version)
    )
    actionable = (
        not canonical.reference_only
        and copy.parsed_version is not None
        and canonical.parsed_version is not None
        and version_less(copy.parsed_version, canonical.parsed_version)
        and copy.tree_sha != canonical.tree_sha
    )
    return SkillFinding(
        item_id=skill_id(copy.owner, copy.repo, copy.path),
        owner=copy.owner,
        repo=copy.repo,
        path=copy.path,
        skill_name=skill_name,
        version=copy.version,
        tree_sha=copy.tree_sha,
        committed_at=copy.committed_at,
        canonical_repo=canonical.repo,
        canonical_path=canonical.path,
        canonical_version=canonical.version,
        canonical_commit=(canonical.commit_sha or "")[:7] or None,
        canonical_tree=canonical.tree_sha,
        suspicious=suspicious,
        actionable=actionable,
    )


def _truncate(report: Report) -> None:
    report.branches_deletable = _cap(report.branches_deletable, "可删分支", report)
    report.skills_outdated = _cap(report.skills_outdated, "过期 Skill", report)
    report.skills_diverged = _cap(report.skills_diverged, "同版本分叉", report)
    report.skills_unversioned = _cap(report.skills_unversioned, "没有版本号", report)
    report.skills_version_only = _cap(report.skills_version_only, "仅版本号不同", report)


def _cap(items: list, label: str, report: Report) -> list:
    if len(items) <= SECTION_LIMIT:
        return items
    report.truncated.append(f"{label}还有 {len(items) - SECTION_LIMIT} 项这次没有写入审阅单")
    return items[:SECTION_LIMIT]


def is_protected_branch(branch: str, config: Config) -> bool:
    return _protected(branch, config)
