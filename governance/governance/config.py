"""Load governance/config.yml."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

STYLES = ("conventional", "bracket")
SQUASH_TITLES = ("PR_TITLE", "COMMIT_OR_PR_TITLE")
SQUASH_MESSAGES = ("PR_BODY", "COMMIT_MESSAGES", "BLANK")
COLOR = re.compile(r"^[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class LabelSpec:
    name: str
    color: str
    description: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Override:
    style: str | None = None
    settings: bool = True
    labels: bool = True
    files: bool = True
    ruleset: bool | None = None
    overwrite_custom_files: bool = False


@dataclass(frozen=True)
class Settings:
    delete_branch_on_merge: bool = True
    allow_squash_merge: bool = True
    squash_merge_commit_title: str = "PR_TITLE"
    squash_merge_commit_message: str = "COMMIT_MESSAGES"
    disable_other_merge_methods: bool = False


@dataclass(frozen=True)
class Config:
    owner: str
    report_repository: str
    actor: str
    include_forks: bool = False
    include_archived: bool = False
    include_private: bool = True
    active_within_days: int = 365
    ignored_repositories: tuple[str, ...] = ()
    commit_sample_size: int = 200
    default_style: str = "conventional"
    style_min_share: float = 0.3
    commit_types: tuple[str, ...] = ("feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore", "revert")
    max_header_length: int = 72
    pr_commits: str = "warn"
    skip_bot_commits: bool = True
    setup_branch: str = "governance/setup"
    settings: Settings = field(default_factory=Settings)
    ruleset_enabled_for_public: bool = True
    ruleset_name: str = "governance-default-branch"
    required_check: str = "commit-lint"
    labels: tuple[LabelSpec, ...] = ()
    overrides: dict[str, Override] = field(default_factory=dict)

    def override(self, repo: str) -> Override:
        return self.overrides.get(repo, Override())


def _bool(data: dict[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"配置 {key} 必须是 true 或 false")
    return value


def _int(data: dict[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"配置 {key} 必须是非负整数")
    return value


def _words(data: dict[str, Any], key: str) -> tuple[str, ...]:
    value = data.get(key) or []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"配置 {key} 必须是字符串列表")
    return tuple(value)


def _text(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"配置缺少 {key}")
    return value


def _labels(raw: object) -> tuple[LabelSpec, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("配置 labels 必须是非空列表")
    found: list[LabelSpec] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("labels 的每一项必须有 name / color / description")
        name = _text(item, "name")
        color = str(item.get("color") or "").lstrip("#")
        if not COLOR.match(color):
            raise ValueError(f"标签 {name} 的颜色必须是 6 位十六进制")
        if name.lower() in seen:
            raise ValueError(f"标签重复: {name}")
        seen.add(name.lower())
        found.append(
            LabelSpec(
                name=name,
                color=color.lower(),
                description=str(item.get("description") or ""),
                aliases=_words(item, "aliases"),
            )
        )
    return tuple(found)


def _overrides(raw: object) -> dict[str, Override]:
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise ValueError("配置 overrides 必须是 仓库名 -> 设置 的映射")
    found: dict[str, Override] = {}
    for repo, item in raw.items():
        item = item or {}
        if not isinstance(item, dict):
            raise ValueError(f"overrides.{repo} 必须是映射")
        style = item.get("style")
        if style is not None and style not in STYLES:
            raise ValueError(f"overrides.{repo}.style 只能是 conventional 或 bracket")
        ruleset = item.get("ruleset")
        if ruleset is not None and not isinstance(ruleset, bool):
            raise ValueError(f"overrides.{repo}.ruleset 必须是 true 或 false")
        found[str(repo)] = Override(
            style=style,
            settings=_bool(item, "settings", True),
            labels=_bool(item, "labels", True),
            files=_bool(item, "files", True),
            ruleset=ruleset,
            overwrite_custom_files=_bool(item, "overwrite_custom_files", False),
        )
    return found


def parse_config(text: str) -> Config:
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("配置文件必须是映射")
    settings_raw = data.get("settings") or {}
    ruleset_raw = data.get("ruleset") or {}
    if not isinstance(settings_raw, dict) or not isinstance(ruleset_raw, dict):
        raise ValueError("settings 和 ruleset 必须是映射")
    settings = Settings(
        delete_branch_on_merge=_bool(settings_raw, "delete_branch_on_merge", True),
        allow_squash_merge=_bool(settings_raw, "allow_squash_merge", True),
        squash_merge_commit_title=str(settings_raw.get("squash_merge_commit_title", "PR_TITLE")),
        squash_merge_commit_message=str(settings_raw.get("squash_merge_commit_message", "COMMIT_MESSAGES")),
        disable_other_merge_methods=_bool(settings_raw, "disable_other_merge_methods", False),
    )
    if settings.squash_merge_commit_title not in SQUASH_TITLES:
        raise ValueError("squash_merge_commit_title 只能是 PR_TITLE 或 COMMIT_OR_PR_TITLE")
    if settings.squash_merge_commit_message not in SQUASH_MESSAGES:
        raise ValueError("squash_merge_commit_message 只能是 PR_BODY / COMMIT_MESSAGES / BLANK")
    default_style = str(data.get("default_style", "conventional"))
    if default_style not in STYLES:
        raise ValueError("default_style 只能是 conventional 或 bracket")
    share = data.get("style_min_share", 0.3)
    if not isinstance(share, (int, float)) or not 0 < float(share) <= 1:
        raise ValueError("style_min_share 必须在 0 到 1 之间")
    pr_commits = str(data.get("pr_commits", "warn"))
    if pr_commits not in {"warn", "error", "off"}:
        raise ValueError("pr_commits 只能是 warn / error / off")
    types = _words(data, "commit_types") or Config.__dataclass_fields__["commit_types"].default
    for kind in types:
        if not re.fullmatch(r"[a-z]+", kind):
            raise ValueError(f"提交类型只能是小写字母: {kind}")
    return Config(
        owner=_text(data, "owner"),
        report_repository=_text(data, "report_repository"),
        actor=_text(data, "actor"),
        include_forks=_bool(data, "include_forks", False),
        include_archived=_bool(data, "include_archived", False),
        include_private=_bool(data, "include_private", True),
        active_within_days=_int(data, "active_within_days", 365),
        ignored_repositories=_words(data, "ignored_repositories"),
        commit_sample_size=max(1, _int(data, "commit_sample_size", 200)),
        default_style=default_style,
        style_min_share=float(share),
        commit_types=types,
        max_header_length=max(20, _int(data, "max_header_length", 72)),
        pr_commits=pr_commits,
        skip_bot_commits=_bool(data, "skip_bot_commits", True),
        setup_branch=str(data.get("setup_branch") or "governance/setup"),
        settings=settings,
        ruleset_enabled_for_public=_bool(ruleset_raw, "enabled_for_public", True),
        ruleset_name=str(ruleset_raw.get("name") or "governance-default-branch"),
        required_check=str(ruleset_raw.get("required_check") or "commit-lint"),
        labels=_labels(data.get("labels")),
        overrides=_overrides(data.get("overrides")),
    )


def load_config(path: Path) -> Config:
    return parse_config(path.read_text(encoding="utf-8"))
