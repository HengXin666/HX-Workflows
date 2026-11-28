"""Pure decisions: which repositories to mirror, under which name and with which visibility."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase

from mirror.config import SourceConfig, TargetConfig


@dataclass(frozen=True)
class SourceRepo:
    name: str
    private: bool
    fork: bool = False
    archived: bool = False
    empty: bool = False
    description: str = ""
    default_branch: str = ""


@dataclass(frozen=True)
class RemoteRepo:
    """A repository as a target platform reports it. private is None when the platform hides it."""

    name: str
    private: bool | None
    default_branch: str = ""


@dataclass(frozen=True)
class RepoPlan:
    create: bool
    private: bool
    change_visibility: bool
    skip_reason: str = ""


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(fnmatchcase(lowered, pattern.lower()) for pattern in patterns)


def selected(name: str, include: tuple[str, ...], exclude: tuple[str, ...]) -> bool:
    if include and not _matches(name, include):
        return False
    return not _matches(name, exclude)


def source_skip_reason(repo: SourceRepo, source: SourceConfig, repo_limit: tuple[str, ...]) -> str:
    if repo_limit and repo.name.lower() not in {item.lower() for item in repo_limit}:
        return "不在 repo_limit 里"
    if repo.private and not source.include_private:
        return "私有仓库已关闭 (include_private: false)"
    if repo.fork and not source.include_forks:
        return "fork 仓库已关闭 (include_forks: false)"
    if repo.archived and not source.include_archived:
        return "归档仓库已关闭 (include_archived: false)"
    if not selected(repo.name, source.include, source.exclude):
        return "被 source.include / exclude 排除"
    return ""


def target_name(repo: SourceRepo, target: TargetConfig) -> str:
    return target.rename.get(repo.name, repo.name)


def desired_private(repo: SourceRepo, visibility: str) -> bool:
    if visibility == "private":
        return True
    if visibility == "public":
        return False
    return repo.private


def plan_repo(
    repo: SourceRepo,
    existing: RemoteRepo | None,
    visibility: str,
    create_missing: bool,
    sync_visibility: bool,
) -> RepoPlan:
    """
    Decide whether a target repository is created or has its visibility aligned with the source
    .agents/notes/implemented/feature/2026-11-28-multi-platform-mirror.md
    """
    private = desired_private(repo, visibility)
    if existing is None:
        if not create_missing:
            return RepoPlan(create=False, private=private, change_visibility=False, skip_reason="目标不存在且 create_missing: false")
        return RepoPlan(create=True, private=private, change_visibility=False)
    change = sync_visibility and existing.private is not None and existing.private != private
    return RepoPlan(create=False, private=private, change_visibility=change)


def parse_limit(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())
