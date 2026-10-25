"""Shared records for a hygiene scan."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote, unquote


def parse_version(value: str | None) -> tuple[int, ...] | None:
    if value is None:
        return None
    text = value.strip()
    if text.startswith(("v", "V")):
        text = text[1:]
    if text == "" or "-" in text or "+" in text:
        return None
    parts = text.split(".")
    numbers: list[int] = []
    for part in parts:
        if not part.isdecimal():
            return None
        numbers.append(int(part))
    return tuple(numbers)


def version_sort_key(parsed: tuple[int, ...] | None) -> tuple[int, ...]:
    if parsed is None:
        return ()
    return parsed


def version_less(left: tuple[int, ...] | None, right: tuple[int, ...] | None) -> bool:
    if left is None or right is None:
        return False
    width = max(len(left), len(right))
    padded_left = left + (0,) * (width - len(left))
    padded_right = right + (0,) * (width - len(right))
    return padded_left < padded_right


def version_equal(left: tuple[int, ...] | None, right: tuple[int, ...] | None) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) == right + (0,) * (width - len(right))


def branch_id(owner: str, repo: str, branch: str) -> str:
    return f"branch:{owner}/{repo}:{quote(branch, safe='')}"


def skill_id(owner: str, repo: str, path: str) -> str:
    return f"skill:{owner}/{repo}:{quote(path, safe='')}"


@dataclass(frozen=True)
class ItemRef:
    kind: str
    owner: str
    repo: str
    value: str


def parse_item_id(item_id: str) -> ItemRef | None:
    kind, separator, rest = item_id.partition(":")
    if separator != ":" or kind not in {"branch", "skill"} or "/" not in rest:
        return None
    owner_repo, separator, encoded = rest.partition(":")
    if separator != ":":
        return None
    owner, slash, repo = owner_repo.partition("/")
    if slash != "/" or owner == "" or repo == "" or encoded == "":
        return None
    return ItemRef(kind=kind, owner=owner, repo=repo, value=unquote(encoded))


@dataclass(frozen=True)
class Config:
    owner: str
    report_repository: str
    actor: str
    include_forks: bool
    include_archived: bool
    include_private: bool
    active_within_days: int
    skill_roots: tuple[str, ...]
    preferred_sources: tuple[str, ...]
    protected_branches: tuple[str, ...]
    protected_branch_globs: tuple[str, ...]
    ignored_repositories: tuple[str, ...]


@dataclass(frozen=True)
class BranchInfo:
    name: str
    protected: bool
    ahead: int | None = None
    behind: int | None = None
    status: str | None = None
    compare_error: str | None = None


@dataclass(frozen=True)
class SkillInstance:
    owner: str
    repo: str
    path: str
    folder: str
    declared_name: str | None
    version: str | None
    parsed_version: tuple[int, ...] | None
    tree_sha: str
    commit_sha: str | None
    committed_at: str | None


@dataclass(frozen=True)
class RepoSnapshot:
    owner: str
    repo: str
    default_branch: str
    archived: bool
    fork: bool
    branches: tuple[BranchInfo, ...] = ()
    open_pr_heads: tuple[str, ...] = ()
    skills: tuple[SkillInstance, ...] = ()
    errors: tuple[str, ...] = ()
    private: bool = False
    pushed_at: str | None = None


@dataclass(frozen=True)
class BranchFinding:
    item_id: str
    owner: str
    repo: str
    branch: str
    default_branch: str
    ahead: int
    behind: int


@dataclass(frozen=True)
class CanonicalSkill:
    skill_name: str
    owner: str
    repo: str
    path: str
    version: str | None
    parsed_version: tuple[int, ...] | None
    tree_sha: str
    commit_sha: str | None
    committed_at: str | None
    reference_only: bool


@dataclass(frozen=True)
class SkillFinding:
    item_id: str
    owner: str
    repo: str
    path: str
    skill_name: str
    version: str | None
    tree_sha: str
    committed_at: str | None
    canonical_repo: str
    canonical_path: str
    canonical_version: str | None
    canonical_commit: str | None
    canonical_tree: str
    suspicious: bool
    actionable: bool


@dataclass
class Report:
    scanned_at: str
    run_url: str
    repos_considered: int = 0
    repos_skipped_fork: int = 0
    repos_skipped_archived: int = 0
    repos_skipped_ignored: int = 0
    repos_skipped_private: int = 0
    repos_skipped_stale: int = 0
    repos_private: int = 0
    active_within_days: int = 0
    branch_total: int = 0
    skill_total: int = 0
    diverged_total: int = 0
    branches_skipped_protected: int = 0
    branches_skipped_pr: int = 0
    branches_not_merged: int = 0
    branches_compare_failed: int = 0
    branches_deletable: list[BranchFinding] = field(default_factory=list)
    skills_outdated: list[SkillFinding] = field(default_factory=list)
    skills_diverged: list[SkillFinding] = field(default_factory=list)
    skills_unversioned: list[SkillFinding] = field(default_factory=list)
    skills_reference_only: list[SkillFinding] = field(default_factory=list)
    skills_version_only: list[SkillFinding] = field(default_factory=list)
    canonical: list[CanonicalSkill] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)
    apply_url: str = ""
    issue_number: int = 0
