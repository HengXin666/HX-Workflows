"""Orchestrate one mirror run: list GitHub repositories, then make every enabled target match them."""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypeVar

from mirror.config import Config
from mirror.git_ops import Git, GitError
from mirror.http_client import HttpError
from mirror.plan import SourceRepo, plan_repo, selected, source_skip_reason, target_name
from mirror.targets import Target, build_target

T = TypeVar("T")
CHANGE_ACTIONS = ("新建", "可见性改为", "默认分支改为")


class Source(Protocol):
    def list_repos(self) -> list[SourceRepo]: ...
    def clone_url(self, name: str) -> str: ...
    def credentials(self) -> tuple[str, str]: ...


@dataclass
class Outcome:
    repo: str
    target: str
    target_repo: str
    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    skipped: str = ""
    private: bool = False
    updated_refs: int = 0

    @property
    def changed(self) -> bool:
        """Something reached the target this run: new commits or tags, a new repository, or a visibility change."""
        return bool(self.updated_refs) or any(action.startswith(CHANGE_ACTIONS) for action in self.actions)


@dataclass
class RunReport:
    dry_run: bool
    outcomes: list[Outcome] = field(default_factory=list)
    repos: list[SourceRepo] = field(default_factory=list)
    skipped_repos: dict[str, str] = field(default_factory=dict)
    private_repos: set[str] = field(default_factory=set)
    target_problems: dict[str, str] = field(default_factory=dict)
    source_error: str = ""

    @property
    def failed(self) -> bool:
        return bool(self.source_error or any(item.error for item in self.outcomes) or self.target_problems_failed)

    @property
    def target_problems_failed(self) -> bool:
        return any(not problem.startswith("跳过") for problem in self.target_problems.values())


def retry(action: Callable[[], T], attempts: int, delay: float = 2.0) -> T:
    """Retry git and network failures; other errors are configuration problems and fail at once."""
    for attempt in range(attempts + 1):
        try:
            return action()
        except (GitError, HttpError) as error:
            transient = isinstance(error, GitError) or error.status in {0, 429, 500, 502, 503, 504}
            if not transient or attempt == attempts:
                raise
            time.sleep(delay * 2**attempt)
    raise RuntimeError("unreachable")


def prepare_targets(config: Config, env: Mapping[str, str], report: RunReport) -> list[Target]:
    ready: list[Target] = []
    for target_config in config.targets:
        if not target_config.enabled:
            report.target_problems[target_config.name] = "跳过: enabled: false"
            continue
        token = env.get(target_config.token_env, "")
        if not token:
            report.target_problems[target_config.name] = f"跳过: 没有配置 secret {target_config.token_env}"
            continue
        target = build_target(target_config, token)
        try:
            target.prepare()
        except HttpError as error:
            report.target_problems[target_config.name] = f"令牌或 owner 校验失败: {error}"
            continue
        ready.append(target)
    return ready


def ensure_target_repo(repo: SourceRepo, target: Target, config: Config, outcome: Outcome, dry_run: bool) -> bool:
    """Create the target repository or align its visibility. Returns False when nothing should be pushed."""
    existing = target.get(outcome.target_repo)
    plan = plan_repo(
        repo,
        existing,
        config.visibility_for(target.config),
        config.policy.create_missing,
        config.policy.sync_visibility,
    )
    label = "私有" if plan.private else "公开"
    if plan.skip_reason:
        outcome.skipped = plan.skip_reason
        return False
    if plan.create:
        outcome.actions.append(f"新建 ({label})")
        if not dry_run:
            target.create(outcome.target_repo, plan.private, repo.description)
    elif plan.change_visibility:
        if dry_run:
            outcome.actions.append(f"可见性改为{label}")
        elif target.set_private(outcome.target_repo, plan.private):
            outcome.actions.append(f"可见性改为{label}")
        else:
            outcome.warnings.append(f"{target.config.provider} 不支持用 API 改可见性, 请到网页上手动改为{label}")
    elif existing is not None and existing.private is None:
        outcome.warnings.append("平台没有返回可见性, 无法核对")
    return True


def sync_target(repo: SourceRepo, mirror_dir: Path | None, target: Target, config: Config, git: Git, dry_run: bool) -> Outcome:
    name = target_name(repo, target.config)
    outcome = Outcome(repo=repo.name, target=target.config.name, target_repo=name, private=repo.private)
    if not selected(repo.name, target.config.include, target.config.exclude):
        outcome.skipped = "被该平台的 include / exclude 排除"
        return outcome
    attempts = config.policy.max_retries
    try:
        if not ensure_target_repo(repo, target, config, outcome, dry_run):
            return outcome
        if dry_run:
            outcome.actions.append("推送分支和标签")
            return outcome
        if mirror_dir is None:
            outcome.actions.append("源仓库为空, 只建仓不推送")
            return outcome
        retry(lambda: target.before_push(name), attempts)
        outcome.updated_refs = retry(
            lambda: git.push_mirror(mirror_dir, target.push_url(name), target.credentials(), config.policy.prune), attempts
        )
        outcome.actions.append(f"推送 {outcome.updated_refs} 个分支/标签" if outcome.updated_refs else "推送: 已是最新")
        if repo.default_branch:
            current = target.get(name)
            if current is not None and current.default_branch and current.default_branch != repo.default_branch:
                if target.set_default_branch(name, repo.default_branch):
                    outcome.actions.append(f"默认分支改为 {repo.default_branch}")
    except (GitError, HttpError) as error:
        outcome.error = str(error)
    return outcome


def sync_repo(repo: SourceRepo, source: Source, targets: list[Target], config: Config, git: Git, cache: Path, dry_run: bool) -> list[Outcome]:
    mirror_dir: Path | None = None
    if not dry_run:
        directory = cache / f"{repo.name}.git"
        try:
            retry(lambda: git.fetch_mirror(directory, source.clone_url(repo.name), source.credentials()), config.policy.max_retries)
        except GitError as error:
            return [
                Outcome(
                    repo=repo.name,
                    target=t.config.name,
                    target_repo=target_name(repo, t.config),
                    error=f"拉取源仓库失败: {error}",
                    private=repo.private,
                )
                for t in targets
            ]
        mirror_dir = directory if git.has_refs(directory) else None
    try:
        # One local copy, pushed to every platform at the same time.
        with ThreadPoolExecutor(max_workers=max(1, len(targets))) as pool:
            return list(pool.map(lambda target: sync_target(repo, mirror_dir, target, config, git, dry_run), targets))
    finally:
        if not dry_run:
            # Free the disk right away: a hosted runner cannot hold every large repository at once.
            shutil.rmtree(cache / f"{repo.name}.git", ignore_errors=True)


def run(
    config: Config,
    source: Source,
    *,
    env: Mapping[str, str] | None = None,
    repo_limit: tuple[str, ...] = (),
    dry_run: bool = False,
    cache: Path = Path(".mirror-cache"),
    targets: list[Target] | None = None,
) -> RunReport:
    report = RunReport(dry_run=dry_run)
    ready = targets if targets is not None else prepare_targets(config, env if env is not None else os.environ, report)
    if not ready:
        report.source_error = report.source_error or "没有可用的目标平台, 检查 targets 配置和 secrets"
        return report
    try:
        repos = source.list_repos()
    except HttpError as error:
        report.source_error = f"读取 GitHub 仓库列表失败: {error}"
        return report
    chosen: list[SourceRepo] = []
    for repo in repos:
        if repo.private:
            report.private_repos.update({repo.name, *(target_name(repo, t.config) for t in ready)})
        reason = source_skip_reason(repo, config.source, repo_limit)
        if reason:
            report.skipped_repos[repo.name] = reason
        else:
            chosen.append(repo)
    report.repos = chosen
    cache.mkdir(parents=True, exist_ok=True)
    git = Git(config.policy.git_timeout)
    try:
        with ThreadPoolExecutor(max_workers=config.policy.concurrency) as pool:
            results = pool.map(lambda repo: sync_repo(repo, source, ready, config, git, cache, dry_run), chosen)
            for outcomes in results:
                report.outcomes.extend(outcomes)
    finally:
        git.close()
    return report
