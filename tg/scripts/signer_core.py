"""Configuration, filtering, and resource-pool primitives for TG signing."""

from __future__ import annotations

import os
import re
import sqlite3
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

try:
    import socks  # type: ignore
except Exception:  # pragma: no cover - only needed when proxy is used
    socks = None

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_ROOT = ROOT.parent
CONFIG_DIR = ROOT / "config"
DEFAULT_CONFIG = CONFIG_DIR / "signins.yml"
LEGACY_DEFAULT_CONFIG = ROOT / "signins.yml"
DEFAULT_REPORT = ROOT / "logs" / "reports" / "tg_signins.json"
DEFAULT_RESOURCE_POOL_DB = ROOT / "sessions" / "resource_pool_seen.sqlite3"
DEFAULT_API_ID = 611335
DEFAULT_API_HASH = "d524b414d21f4d37f08684c1df41ac9c"
TG_LINK_RE = re.compile(r"(?:https?://)?t\.me/[^\s)>\]]+|tg://join\?invite=[A-Za-z0-9_-]+|https?://telegram\.me/[^\s)>\]]+", re.I)

def normalize_join_target(target: str) -> str:
    value = target.strip()
    if value.startswith("tg://join?invite="):
        return "+" + urllib.parse.parse_qs(urllib.parse.urlparse(value).query).get("invite", [""])[0]
    parsed = urllib.parse.urlparse(value if "://" in value else "https://" + value if value.startswith(("t.me/", "telegram.me/")) else value)
    if parsed.netloc in {"t.me", "telegram.me"}:
        path = parsed.path.strip("/")
        if path.startswith("+"):
            return path
        if path.startswith("joinchat/"):
            return "+" + path.split("/", 1)[1]
        if path:
            return "@" + path.split("/", 1)[0]
    return value


def entity_label(entity: Any) -> str:
    return (
        getattr(entity, "title", None)
        or getattr(entity, "username", None)
        or " ".join(
            x
            for x in [getattr(entity, "first_name", ""), getattr(entity, "last_name", "")]
            if x
        ).strip()
        or str(getattr(entity, "id", "unknown"))
    )


@dataclass
class RunResult:
    job_id: str
    account_index: int
    status: str
    matched: bool
    summary: str
    messages: list[Any]
    node_logs: dict[str, list[str]]


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"配置文件不存在: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise SystemExit("配置根节点必须是 YAML mapping")
    data.setdefault("defaults", {})
    data.setdefault("jobs", [])
    if not isinstance(data["defaults"], dict):
        raise SystemExit("defaults 必须是 YAML mapping")
    if not isinstance(data["jobs"], list) or not all(isinstance(job, dict) for job in data["jobs"]):
        raise SystemExit("jobs 必须是 YAML mapping 数组")
    return data


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少 GitHub Secret / 环境变量: {name}")
    return value


def telegram_api_config(api_id_secret: str, api_hash_secret: str) -> tuple[int, str]:
    api_id_raw = os.environ.get(api_id_secret, "").strip()
    api_hash = os.environ.get(api_hash_secret, "").strip()
    if api_id_raw and api_hash:
        try:
            api_id = int(api_id_raw)
        except ValueError as exc:
            raise RuntimeError(f"{api_id_secret} 必须是正整数") from exc
        if api_id <= 0:
            raise RuntimeError(f"{api_id_secret} 必须是正整数")
        return api_id, api_hash
    if not api_id_raw and not api_hash:
        return DEFAULT_API_ID, DEFAULT_API_HASH
    missing = api_hash_secret if api_id_raw else api_id_secret
    raise RuntimeError(f"Telegram API 参数不完整，缺少环境变量: {missing}")


def optional_env(name: str | None) -> str | None:
    if not name:
        return None
    value = os.environ.get(name, "").strip()
    return value or None


def read_secret_lines(secret_name: str) -> list[str]:
    raw = required_env(secret_name)
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"Secret {secret_name} 为空")
    return lines


def parse_proxy(proxy_url: str | None):
    if not proxy_url:
        return None
    if socks is None:
        raise RuntimeError("配置了 TG_PROXY，但没有安装 PySocks")

    parsed = urllib.parse.urlparse(proxy_url)
    scheme = parsed.scheme.lower()
    if scheme not in {"socks5", "socks4", "http"}:
        raise RuntimeError(f"不支持的代理协议: {scheme}")

    proxy_type = {
        "socks5": socks.SOCKS5,
        "socks4": socks.SOCKS4,
        "http": socks.HTTP,
    }[scheme]

    if not parsed.hostname or not parsed.port:
        raise RuntimeError("TG_PROXY 必须包含 host 和 port")

    username = urllib.parse.unquote(parsed.username) if parsed.username else None
    password = urllib.parse.unquote(parsed.password) if parsed.password else None
    return (proxy_type, parsed.hostname, parsed.port, True, username, password)


def message_text(message: Any) -> str:
    return (getattr(message, "message", None) or getattr(message, "text", None) or "").strip()


def resolve_local_path(value: str | None, default: Path) -> Path:
    raw = os.path.expandvars(str(value or "").strip()) if value else ""
    return Path(raw).expanduser().resolve() if raw else default.resolve()


def media_kind(message: Any) -> str | None:
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "video", None) or getattr(message, "video_note", None):
        return "video"
    file = getattr(message, "file", None)
    mime_type = str(getattr(file, "mime_type", "") or "").lower()
    if mime_type.startswith("image/"):
        return "photo"
    if mime_type.startswith("video/"):
        return "video"
    return None


def media_fingerprint(message: Any) -> str | None:
    document = getattr(message, "document", None)
    if document is not None:
        doc_id = getattr(document, "id", None)
        if doc_id is not None:
            return ":".join(
                [
                    "document",
                    str(doc_id),
                    str(getattr(document, "size", "") or ""),
                    str(getattr(document, "mime_type", "") or ""),
                ]
            )

    photo = getattr(message, "photo", None)
    if photo is not None:
        photo_id = getattr(photo, "id", None)
        if photo_id is not None:
            return f"photo:{photo_id}"

    file = getattr(message, "file", None)
    file_id = getattr(file, "id", None)
    if file_id:
        return f"file:{file_id}"
    return None


def init_resource_pool_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS resource_pool_seen (
            fingerprint TEXT PRIMARY KEY,
            media_type TEXT NOT NULL,
            pool_peer TEXT NOT NULL,
            source_peer TEXT,
            source_message_id INTEGER,
            account_index INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    return conn


def resource_seen(conn: sqlite3.Connection, fingerprint: str) -> bool:
    row = conn.execute("SELECT 1 FROM resource_pool_seen WHERE fingerprint = ? LIMIT 1", (fingerprint,)).fetchone()
    return row is not None


def mark_resource_seen(
    conn: sqlite3.Connection,
    fingerprint: str,
    media_type: str,
    pool_peer: str,
    source_peer: str,
    source_message_id: int | None,
    account_index: int,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO resource_pool_seen
            (fingerprint, media_type, pool_peer, source_peer, source_message_id, account_index)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (fingerprint, media_type, pool_peer, source_peer, source_message_id, account_index),
    )
    conn.commit()


def listify(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value]
    return [str(value)]


def text_matches(text: str, filters: dict[str, Any]) -> bool:
    include = filters.get("include", {}) or {}
    exclude = filters.get("exclude", {}) or {}

    include_contains = listify(include.get("contains"))
    include_regex = listify(include.get("regex"))
    exclude_contains = listify(exclude.get("contains"))
    exclude_regex = listify(exclude.get("regex"))

    for token in exclude_contains:
        if token and token in text:
            return False
    for pattern in exclude_regex:
        if pattern and re.search(pattern, text, flags=re.I | re.M):
            return False

    has_include_rule = bool(include_contains or include_regex)
    if not has_include_rule:
        return False

    for token in include_contains:
        if token and token in text:
            return True
    for pattern in include_regex:
        if pattern and re.search(pattern, text, flags=re.I | re.M):
            return True
    return False


def should_forward(forward: dict[str, Any], status: str, matched: bool) -> bool:
    if not forward.get("enabled", False):
        return False
    when = set(listify(forward.get("when")))
    if "always" in when:
        return True
    if status in when:
        return True
    if matched and "matched" in when:
        return True
    if (not matched) and "not_matched" in when:
        return True
    return False
