"""Load mirror/config.yml. Tokens are never read from this file, only the names of their environment variables."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROVIDERS = ("gitlab", "gitee", "codeberg", "forgejo", "gitea", "cnb")
VISIBILITIES = ("follow", "private", "public")
EMAIL_MODES = ("always", "failure", "never")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
DEFAULT_URLS: dict[str, tuple[str, str]] = {
    "gitlab": ("https://gitlab.com", "https://gitlab.com/api/v4"),
    "gitee": ("https://gitee.com", "https://gitee.com/api/v5"),
    "codeberg": ("https://codeberg.org", "https://codeberg.org/api/v1"),
    "cnb": ("https://cnb.cool", "https://api.cnb.cool"),
}


@dataclass(frozen=True)
class SourceConfig:
    owner: str
    token_env: str = "MIRROR_GITHUB_TOKEN"
    api_url: str = "https://api.github.com"
    base_url: str = "https://github.com"
    include_private: bool = True
    include_forks: bool = False
    include_archived: bool = True
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class TargetConfig:
    name: str
    provider: str
    owner: str
    token_env: str
    base_url: str
    api_url: str
    enabled: bool = True
    username: str = ""
    visibility: str | None = None
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    rename: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Policy:
    visibility: str = "follow"
    sync_visibility: bool = True
    create_missing: bool = True
    prune: bool = False
    concurrency: int = 2
    max_retries: int = 3
    git_timeout: int = 1800


@dataclass(frozen=True)
class Notify:
    email: str = "always"
    mail_to_env: str = "MIRROR_MAIL_TO"


@dataclass(frozen=True)
class Config:
    source: SourceConfig
    targets: tuple[TargetConfig, ...]
    policy: Policy = field(default_factory=Policy)
    notify: Notify = field(default_factory=Notify)

    def visibility_for(self, target: TargetConfig) -> str:
        return target.visibility or self.policy.visibility


def _table(data: object, key: str) -> dict[str, Any]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"配置 {key} 必须是映射")
    return data


def _bool(data: dict[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"配置 {key} 必须是 true 或 false")
    return value


def _int(data: dict[str, Any], key: str, default: int, minimum: int) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"配置 {key} 必须是不小于 {minimum} 的整数")
    return value


def _text(data: dict[str, Any], key: str, default: str | None = None) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"配置缺少 {key}")
    return value.strip()


def _words(data: dict[str, Any], key: str) -> tuple[str, ...]:
    value = data.get(key) or []
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"配置 {key} 必须是字符串列表")
    return tuple(value)


def _env(data: dict[str, Any], key: str, default: str | None = None) -> str:
    value = _text(data, key, default)
    if not ENV_NAME.match(value):
        raise ValueError(f"配置 {key} 必须是环境变量名 (大写字母、数字和下划线), 不要把令牌本身写进配置")
    return value


def _url(data: dict[str, Any], key: str, default: str) -> str:
    value = _text(data, key, default).rstrip("/")
    if not value.startswith(("https://", "http://")):
        raise ValueError(f"配置 {key} 必须是 http(s) 地址")
    return value


def _visibility(data: dict[str, Any], key: str, default: str | None) -> str | None:
    value = data.get(key, default)
    if value is None:
        return None
    if value not in VISIBILITIES:
        raise ValueError(f"配置 {key} 只能是 {' / '.join(VISIBILITIES)}")
    return str(value)


def _rename(data: dict[str, Any]) -> dict[str, str]:
    value = data.get("rename") or {}
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in value.items()):
        raise ValueError("配置 rename 必须是 源仓库名: 目标仓库名 的映射")
    return dict(value)


def _target(raw: object, index: int) -> TargetConfig:
    if not isinstance(raw, dict):
        raise ValueError(f"targets 第 {index + 1} 项必须是映射")
    provider = _text(raw, "provider")
    if provider not in PROVIDERS:
        raise ValueError(f"targets 第 {index + 1} 项 provider 只能是 {' / '.join(PROVIDERS)}")
    family = "codeberg" if provider in {"forgejo", "gitea"} else provider
    default_base, default_api = DEFAULT_URLS[family]
    if provider in {"forgejo", "gitea"} and ("base_url" not in raw or "api_url" not in raw):
        raise ValueError(f"自建 {provider} 必须同时写 base_url 和 api_url")
    base_url = _url(raw, "base_url", default_base)
    if provider == "gitlab" and "api_url" not in raw:
        default_api = base_url + "/api/v4"
    return TargetConfig(
        name=_text(raw, "name", provider),
        provider=provider,
        owner=_text(raw, "owner").strip("/"),
        token_env=_env(raw, "token_env"),
        base_url=base_url,
        api_url=_url(raw, "api_url", default_api),
        enabled=_bool(raw, "enabled", True),
        username=str(raw.get("username") or "").strip(),
        visibility=_visibility(raw, "visibility", None),
        include=_words(raw, "include"),
        exclude=_words(raw, "exclude"),
        rename=_rename(raw),
    )


def parse_config(data: object) -> Config:
    root = _table(data, "根节点")
    source_raw = _table(root.get("source"), "source")
    provider = source_raw.get("provider", "github")
    if provider != "github":
        raise ValueError("source.provider 目前只支持 github")
    source = SourceConfig(
        owner=_text(source_raw, "owner"),
        token_env=_env(source_raw, "token_env", "MIRROR_GITHUB_TOKEN"),
        api_url=_url(source_raw, "api_url", "https://api.github.com"),
        base_url=_url(source_raw, "base_url", "https://github.com"),
        include_private=_bool(source_raw, "include_private", True),
        include_forks=_bool(source_raw, "include_forks", False),
        include_archived=_bool(source_raw, "include_archived", True),
        include=_words(source_raw, "include"),
        exclude=_words(source_raw, "exclude"),
    )
    policy_raw = _table(root.get("policy"), "policy")
    policy = Policy(
        visibility=_visibility(policy_raw, "visibility", "follow") or "follow",
        sync_visibility=_bool(policy_raw, "sync_visibility", True),
        create_missing=_bool(policy_raw, "create_missing", True),
        prune=_bool(policy_raw, "prune", False),
        concurrency=_int(policy_raw, "concurrency", 2, 1),
        max_retries=_int(policy_raw, "max_retries", 3, 0),
        git_timeout=_int(policy_raw, "git_timeout", 1800, 60),
    )
    targets_raw = root.get("targets") or []
    if not isinstance(targets_raw, list) or not targets_raw:
        raise ValueError("配置 targets 必须是非空列表")
    targets = tuple(_target(item, index) for index, item in enumerate(targets_raw))
    names = [target.name for target in targets]
    duplicated = sorted({name for name in names if names.count(name) > 1})
    if duplicated:
        raise ValueError(f"targets 的 name 重复: {', '.join(duplicated)}")
    notify_raw = _table(root.get("notify"), "notify")
    email = notify_raw.get("email", "always")
    if email not in EMAIL_MODES:
        raise ValueError(f"配置 notify.email 只能是 {' / '.join(EMAIL_MODES)}")
    notify = Notify(email=str(email), mail_to_env=_env(notify_raw, "mail_to_env", "MIRROR_MAIL_TO"))
    return Config(source=source, targets=targets, policy=policy, notify=notify)


def load_config(path: Path) -> Config:
    return parse_config(yaml.safe_load(path.read_text(encoding="utf-8")))
