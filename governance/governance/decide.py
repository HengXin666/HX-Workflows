"""Decide what each repository still needs: settings, labels, files and the ruleset."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from governance.commit_lint import StyleStats, detect_style, parse_convention
from governance.config import Config
from governance.labels import Label, LabelOp, plan_labels
from governance.templates import (
    CONVENTION,
    ISSUE_DIR,
    LINT_WORKFLOW,
    PR_TEMPLATE,
    FileSpec,
    RenderInput,
    is_managed,
    render_files,
)

PR_TEMPLATE_PATHS = (
    ".github/pull_request_template.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    "pull_request_template.md",
    "PULL_REQUEST_TEMPLATE.md",
    "docs/pull_request_template.md",
    "docs/PULL_REQUEST_TEMPLATE.md",
)
PUSHING_WORKFLOW = re.compile(
    r"git\s+push|git-auto-commit-action|add-and-commit|github-push-action"
)
RULESET_DONE = {"ok", "private", "disabled", "pushes"}


@dataclass
class RepoSnapshot:
    owner: str
    repo: str
    default_branch: str = "main"
    private: bool = False
    fork: bool = False
    archived: bool = False
    pushed_at: str | None = None
    empty: bool = False
    head_sha: str = ""
    settings: dict[str, object] = field(default_factory=dict)
    labels: list[Label] = field(default_factory=list)
    files: dict[str, str | None] = field(default_factory=dict)  # path -> text (None = binary or directory)
    modes: dict[str, str] = field(default_factory=dict)
    headers: list[str] = field(default_factory=list)
    rulesets: list[dict] | None = None  # None means it could not be read
    setup_pr: dict | None = None
    errors: list[str] = field(default_factory=list)


@dataclass
class RepoPlan:
    snapshot: RepoSnapshot
    style: str
    style_source: str
    stats: StyleStats
    baseline: str
    settings_patch: dict[str, object] = field(default_factory=dict)
    label_ops: list[LabelOp] = field(default_factory=list)
    file_changes: dict[str, FileSpec] = field(default_factory=dict)
    files_skipped: list[str] = field(default_factory=list)
    ruleset: str = "disabled"
    ruleset_reason: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def compliant(self) -> bool:
        return (
            not self.settings_patch
            and not self.label_ops
            and not self.file_changes
            and self.ruleset in RULESET_DONE
            and not self.snapshot.errors
        )


def recently_pushed(pushed_at: str | None, days: int, now: datetime | None = None) -> bool:
    if days == 0:
        return True
    if not pushed_at:
        return False
    try:
        moment = datetime.fromisoformat(pushed_at)
    except ValueError:
        return False
    current = now or datetime.now(UTC)
    return current - moment <= timedelta(days=days)


def should_skip(snapshot: RepoSnapshot, config: Config, now: datetime | None = None) -> str | None:
    if snapshot.repo in config.ignored_repositories:
        return "ignored"
    if snapshot.private and not config.include_private:
        return "private"
    if snapshot.fork and not config.include_forks:
        return "fork"
    if snapshot.archived and not config.include_archived:
        return "archived"
    if not recently_pushed(snapshot.pushed_at, config.active_within_days, now):
        return "stale"
    return None


def existing_convention(snapshot: RepoSnapshot) -> tuple[str | None, str]:
    text = snapshot.files.get(CONVENTION)
    if not text or not is_managed(text):
        return None, ""
    try:
        parsed = parse_convention(text)
    except ValueError:
        return None, ""
    return parsed.style, parsed.baseline


def choose_style(
    stats: StyleStats,
    config: Config,
    snapshot: RepoSnapshot,
    reviewed: str | None = None,
) -> tuple[str, str]:
    """Pick the commit style: override, then the onboarded file, then the reviewed or detected style."""
    forced = config.override(snapshot.repo).style
    if forced:
        return forced, "override"
    onboarded, _baseline = existing_convention(snapshot)
    if onboarded:
        return onboarded, "existing"
    if reviewed in ("conventional", "bracket"):
        return reviewed, "reviewed"
    if snapshot.empty or stats.total == 0:
        return config.default_style, "empty"
    if stats.dominant in ("conventional", "bracket"):
        return stats.dominant, "history"
    return config.default_style, "default"


def desired_settings(config: Config) -> dict[str, object]:
    wanted: dict[str, object] = {
        "delete_branch_on_merge": config.settings.delete_branch_on_merge,
        "allow_squash_merge": config.settings.allow_squash_merge,
    }
    if config.settings.allow_squash_merge:
        wanted["squash_merge_commit_title"] = config.settings.squash_merge_commit_title
        wanted["squash_merge_commit_message"] = config.settings.squash_merge_commit_message
    if config.settings.disable_other_merge_methods:
        wanted["allow_merge_commit"] = False
        wanted["allow_rebase_merge"] = False
    return wanted


def plan_settings(snapshot: RepoSnapshot, config: Config) -> dict[str, object]:
    patch: dict[str, object] = {}
    for key, value in desired_settings(config).items():
        if snapshot.settings.get(key) != value:
            patch[key] = value
    if "squash_merge_commit_title" in patch or "squash_merge_commit_message" in patch:
        # GitHub validates the squash title and message together, so always send them as a pair.
        patch["squash_merge_commit_title"] = config.settings.squash_merge_commit_title
        patch["squash_merge_commit_message"] = config.settings.squash_merge_commit_message
    return patch


def _custom(snapshot: RepoSnapshot, path: str) -> bool:
    return path in snapshot.files and not is_managed(snapshot.files.get(path))


def plan_files(
    snapshot: RepoSnapshot,
    desired: dict[str, FileSpec],
    overwrite_custom: bool,
) -> tuple[dict[str, FileSpec], list[str]]:
    changes: dict[str, FileSpec] = {}
    skipped: list[str] = []
    blocked: set[str] = set()
    if not overwrite_custom:
        issue_custom = sorted(
            path
            for path in snapshot.files
            if path.startswith(ISSUE_DIR + "/") and not is_managed(snapshot.files.get(path))
        )
        if issue_custom:
            blocked.update(path for path in desired if path.startswith(ISSUE_DIR + "/"))
            skipped.append(f"Issue 模板: 已有自定义 `{issue_custom[0]}`" + (f" 等 {len(issue_custom)} 个" if len(issue_custom) > 1 else ""))
        pr_custom = [
            path
            for path in snapshot.files
            if (path in PR_TEMPLATE_PATHS or path.startswith(".github/PULL_REQUEST_TEMPLATE/"))
            and not is_managed(snapshot.files.get(path))
        ]
        if pr_custom:
            blocked.add(PR_TEMPLATE)
            skipped.append(f"PR 模板: 已有自定义 `{min(pr_custom)}`")
        for path in desired:
            if path in blocked or not _custom(snapshot, path):
                continue
            blocked.add(path)
            skipped.append(f"`{path}`: 已有同名的自定义文件")
    for path, spec in desired.items():
        if path in blocked:
            continue
        if snapshot.files.get(path) == spec.content and snapshot.modes.get(path, spec.mode) == spec.mode:
            continue
        changes[path] = spec
    return changes, skipped


def plan_ruleset(snapshot: RepoSnapshot, config: Config, files_pending: bool) -> tuple[str, str]:
    forced = config.override(snapshot.repo).ruleset
    if snapshot.private:
        return "private", "私有仓库在免费计划下不能用规则集, 只靠 commit-lint 失败提醒和本地钩子"
    if forced is False or (forced is None and not config.ruleset_enabled_for_public):
        return "disabled", "配置关闭了规则集"
    if snapshot.rulesets is None:
        return "unknown", "读不到规则集, 这次不处理"
    existing = next((item for item in snapshot.rulesets if item.get("name") == config.ruleset_name), None)
    if existing is not None:
        rules = set(existing.get("rule_types") or [])
        if existing.get("enforcement") == "active" and {"pull_request", "required_status_checks"} <= rules:
            return "ok", "规则集已生效"
        return "update", "规则集存在但规则不完整, 将更新"
    if snapshot.empty:
        return "defer", "空仓库, 有提交后再建规则集"
    if files_pending or not is_managed(snapshot.files.get(LINT_WORKFLOW)):
        return "defer", f"默认分支上还没有 {config.required_check} 检查, 治理 PR 合入后再执行一次才会创建"
    if forced is not True:
        pusher = min(
            (
                path
                for path, text in snapshot.files.items()
                if path.startswith(".github/workflows/")
                and path != LINT_WORKFLOW
                and text
                and PUSHING_WORKFLOW.search(text)
            ),
            default=None,
        )
        if pusher:
            return "pushes", f"`{pusher}` 会直接写默认分支, 要求 PR 会让它失败. 确认无碍后在 overrides 里设 ruleset: true"
    return "create", "将创建: 默认分支必须走 PR 且 commit-lint 通过, 不设绕过名单"


def plan_repo(snapshot: RepoSnapshot, config: Config, reviewed_style: str | None = None) -> RepoPlan:
    """
    Build one repository's governance plan from a live snapshot
    .agents/notes/implemented/feature/2026-11-25-repository-governance.md
    """
    stats = detect_style(snapshot.headers[: config.commit_sample_size], config.style_min_share)
    style, source = choose_style(stats, config, snapshot, reviewed_style)
    _style, onboarded_baseline = existing_convention(snapshot)
    baseline = onboarded_baseline or snapshot.head_sha
    plan = RepoPlan(snapshot=snapshot, style=style, style_source=source, stats=stats, baseline=baseline)
    override = config.override(snapshot.repo)
    if override.settings:
        plan.settings_patch = plan_settings(snapshot, config)
    if override.labels:
        plan.label_ops = plan_labels(snapshot.labels, config.labels)
    if override.files and not snapshot.empty:
        desired = render_files(
            RenderInput(
                style=style,
                types=config.commit_types,
                max_header_length=config.max_header_length,
                baseline=baseline,
                default_branch=snapshot.default_branch,
                pr_commits=config.pr_commits,
                skip_bot_commits=config.skip_bot_commits,
                check_name=config.required_check,
            )
        )
        plan.file_changes, plan.files_skipped = plan_files(snapshot, desired, override.overwrite_custom_files)
    elif snapshot.empty:
        plan.notes.append("空仓库没有默认分支, 首次推送后再扫描才会开 PR 写文件")
    plan.ruleset, plan.ruleset_reason = plan_ruleset(snapshot, config, bool(plan.file_changes))
    if not override.files and plan.ruleset == "defer":
        plan.ruleset, plan.ruleset_reason = "disabled", "这个仓库关闭了写文件, 没有检查可要求"
    return plan
