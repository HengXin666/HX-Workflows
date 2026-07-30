"""Configuration, filtering, and resource-pool primitives for TG signing."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
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
DEFAULT_RESOURCE_CATALOG_DB = ROOT / "sessions" / "resource_catalog.sqlite3"
DEFAULT_RESOURCE_LINKS_EXPORT = ROOT / "sessions" / "resource_links.json"
DEFAULT_RESOURCE_PREVIEW_DB = ROOT / "sessions" / "resource_previews.sqlite3"
DEFAULT_RESOURCE_PREVIEW_EXPORT = ROOT / "sessions" / "resource_previews.json"
DEFAULT_API_ID = 611335
DEFAULT_API_HASH = "d524b414d21f4d37f08684c1df41ac9c"
TG_LINK_RE = re.compile(r"(?:https?://)?t\.me/[^\s)>\]]+|tg://join\?invite=[A-Za-z0-9_-]+|https?://telegram\.me/[^\s)>\]]+", re.I)
RESOURCE_LINK_RE = re.compile(
    r"(?:(?:https?://|tg://)[^\s<>\"']+|(?<![\w.])(?:t\.me|telegram\.me)/[^\s<>\"']+)",
    re.I,
)

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


def normalize_resource_url(value: str) -> str:
    url = value.strip().rstrip(".,;:!?)]}>'\"，。；：！？）】》")
    if not url:
        return ""
    if url.lower().startswith(("t.me/", "telegram.me/")):
        url = "https://" + url
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https", "tg"}:
        return ""
    netloc = parsed.netloc.lower()
    if netloc == "telegram.me":
        netloc = "t.me"
    return urllib.parse.urlunsplit((scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def extract_message_urls(message: Any) -> list[str]:
    candidates = RESOURCE_LINK_RE.findall(message_text(message))
    for entity in getattr(message, "entities", None) or []:
        url = str(getattr(entity, "url", "") or "").strip()
        if url:
            candidates.append(url)
    for row in getattr(message, "buttons", None) or []:
        buttons = row if isinstance(row, (list, tuple)) else [row]
        for button in buttons:
            raw_url = button.get("url", "") if isinstance(button, dict) else getattr(button, "url", "")
            url = str(raw_url or "").strip()
            if url:
                candidates.append(url)
    normalized = [normalize_resource_url(candidate) for candidate in candidates]
    return list(dict.fromkeys(url for url in normalized if url))


def telegram_message_link(peer: Any, message_id: int | None) -> str:
    if not message_id:
        return ""
    raw_id = getattr(peer, "id", None)
    if raw_id is not None and (
        peer.__class__.__name__ == "User"
        or bool(getattr(peer, "bot", False))
        or (hasattr(peer, "first_name") and not hasattr(peer, "title"))
    ):
        return f"tg://openmessage?user_id={raw_id}&message_id={message_id}"
    username = str(getattr(peer, "username", "") or "").strip().lstrip("@")
    if username:
        return f"https://t.me/{username}/{message_id}"
    if raw_id is not None and (
        peer.__class__.__name__ == "Channel"
        or hasattr(peer, "megagroup")
        or hasattr(peer, "broadcast")
    ):
        return f"https://t.me/c/{str(raw_id).removeprefix('-100')}/{message_id}"
    value = str(peer or "").strip()
    if value.startswith("@"):
        return f"https://t.me/{value[1:]}/{message_id}"
    numeric = str(raw_id if raw_id is not None else value)
    if numeric.startswith("-100") and numeric[4:].isdigit():
        return f"https://t.me/c/{numeric[4:]}/{message_id}"
    return ""


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


def extract_resource_previews(
    messages: list[Any],
    peer: Any,
    account_index: int,
    message_rounds: dict[int, int] | None = None,
    callback_prefix: str = "unlock:",
    account_id: str = "",
) -> list[dict[str, Any]]:
    rounds = message_rounds or {}
    ordered = sorted(messages, key=lambda message: int(getattr(message, "id", 0) or 0))
    media_by_round: dict[int, list[Any]] = {}
    for message in ordered:
        message_id = int(getattr(message, "id", 0) or 0)
        request_round = int(rounds.get(message_id) or 1)
        if media_kind(message):
            media_by_round.setdefault(request_round, []).append(message)

    records: list[dict[str, Any]] = []
    for message in ordered:
        message_id = int(getattr(message, "id", 0) or 0)
        request_round = int(rounds.get(message_id) or 1)
        summary = message_text(message)
        message_peer = getattr(message, "chat", None) or peer
        source_username = str(getattr(message_peer, "username", "") or "").strip().lstrip("@")
        source_peer = f"@{source_username}" if source_username else str(getattr(message_peer, "id", None) or peer)
        preview_messages = media_by_round.get(request_round, [])
        preview_message_ids = [
            int(getattr(item, "id", 0) or 0)
            for item in preview_messages
            if int(getattr(item, "id", 0) or 0)
        ]
        preview_message_links = [
            link
            for item in preview_messages
            if (link := telegram_message_link(getattr(item, "chat", None) or peer, int(getattr(item, "id", 0) or 0)))
        ]
        preview_media = [
            {
                "message_id": int(getattr(item, "id", 0) or 0),
                "kind": str(media_kind(item) or "media"),
                "url": telegram_message_link(
                    getattr(item, "chat", None) or peer,
                    int(getattr(item, "id", 0) or 0),
                ),
            }
            for item in preview_messages
            if int(getattr(item, "id", 0) or 0)
        ]
        description_match = re.search(r"文件描述\s*[:：]\s*(.+)", summary)
        photos_match = re.search(r"图片\s*[:：]\s*(\d+)\s*个", summary)
        videos_match = re.search(r"视频\s*[:：]\s*(\d+)\s*个", summary)
        files_match = re.search(r"文件\s*[:：]\s*(\d+)\s*个", summary)
        cost_match = re.search(r"解锁需要\s*[:：]\s*(\d+)\s*积分", summary)
        for row in getattr(message, "buttons", None) or []:
            buttons = row if isinstance(row, (list, tuple)) else [row]
            for button in buttons:
                raw_data = button.get("data") if isinstance(button, dict) else getattr(button, "data", None)
                if raw_data is None:
                    continue
                if isinstance(raw_data, bytes):
                    callback_data = raw_data.decode("utf-8", errors="replace")
                else:
                    callback_data = str(raw_data)
                if not callback_data.startswith(callback_prefix):
                    continue
                resource_key = callback_data.removeprefix(callback_prefix).strip()
                if not resource_key:
                    continue
                button_text = button.get("text", "") if isinstance(button, dict) else getattr(button, "text", "")
                records.append(
                    {
                        "resource_key": resource_key,
                        "callback_data": callback_data,
                        "button_text": str(button_text or ""),
                        "description": description_match.group(1).strip() if description_match else "",
                        "summary_text": summary,
                        "photos": int(photos_match.group(1)) if photos_match else 0,
                        "videos": int(videos_match.group(1)) if videos_match else 0,
                        "files": int(files_match.group(1)) if files_match else 0,
                        "unlock_cost": int(cost_match.group(1)) if cost_match else 0,
                        "source_peer": source_peer,
                        "source_message_id": message_id or None,
                        "message_link": telegram_message_link(message_peer, message_id or None),
                        "preview_message_ids": preview_message_ids,
                        "preview_message_links": preview_message_links,
                        "preview_media": preview_media,
                        "account_index": account_index,
                        "account_id": account_id,
                        "round": request_round,
                    }
                )
    return records


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


def _sqlite_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _add_sqlite_columns(
    conn: sqlite3.Connection,
    table: str,
    definitions: list[tuple[str, str]],
) -> set[str]:
    existing = _sqlite_columns(conn, table)
    for name, definition in definitions:
        if name not in existing:
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')
    return existing


def _upgrade_resource_preview_schema(conn: sqlite3.Connection) -> None:
    preview_columns = _sqlite_columns(conn, "resource_previews")
    if "resource_key" not in preview_columns:
        raise sqlite3.DatabaseError("resource_previews 缺少 resource_key，无法安全升级")
    original_preview_columns = _add_sqlite_columns(
        conn,
        "resource_previews",
        [
            ("callback_data", "TEXT NOT NULL DEFAULT ''"),
            ("button_text", "TEXT NOT NULL DEFAULT ''"),
            ("description", "TEXT NOT NULL DEFAULT ''"),
            ("summary_text", "TEXT NOT NULL DEFAULT ''"),
            ("photos", "INTEGER NOT NULL DEFAULT 0"),
            ("videos", "INTEGER NOT NULL DEFAULT 0"),
            ("files", "INTEGER NOT NULL DEFAULT 0"),
            ("unlock_cost", "INTEGER NOT NULL DEFAULT 0"),
            ("source_peer", "TEXT NOT NULL DEFAULT ''"),
            ("source_message_id", "INTEGER"),
            ("message_link", "TEXT NOT NULL DEFAULT ''"),
            ("preview_message_ids", "TEXT NOT NULL DEFAULT '[]'"),
            ("preview_message_links", "TEXT NOT NULL DEFAULT '[]'"),
            ("preview_media", "TEXT NOT NULL DEFAULT '[]'"),
            ("first_account_index", "INTEGER NOT NULL DEFAULT 1"),
            ("first_account_id", "TEXT NOT NULL DEFAULT ''"),
            ("first_round", "INTEGER NOT NULL DEFAULT 1"),
            ("seen_count", "INTEGER NOT NULL DEFAULT 1"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
            ("last_seen_at", "TEXT NOT NULL DEFAULT ''"),
        ],
    )
    if "first_account_index" not in original_preview_columns and "account_index" in original_preview_columns:
        conn.execute(
            "UPDATE resource_previews SET first_account_index = "
            "CASE WHEN account_index > 0 THEN account_index ELSE 1 END"
        )
    if "first_round" not in original_preview_columns:
        legacy_round = "request_round" if "request_round" in original_preview_columns else "round"
        if legacy_round in original_preview_columns:
            conn.execute(
                f'UPDATE resource_previews SET first_round = CASE WHEN "{legacy_round}" > 0 '
                f'THEN "{legacy_round}" ELSE 1 END'
            )
    conn.execute(
        """
        UPDATE resource_previews
        SET callback_data = COALESCE(callback_data, ''),
            button_text = COALESCE(button_text, ''),
            description = COALESCE(description, ''),
            summary_text = COALESCE(summary_text, ''),
            photos = COALESCE(photos, 0),
            videos = COALESCE(videos, 0),
            files = COALESCE(files, 0),
            unlock_cost = COALESCE(unlock_cost, 0),
            source_peer = COALESCE(source_peer, ''),
            message_link = COALESCE(message_link, ''),
            preview_message_ids = CASE WHEN preview_message_ids IS NULL OR preview_message_ids = ''
                                       THEN '[]' ELSE preview_message_ids END,
            preview_message_links = CASE WHEN preview_message_links IS NULL OR preview_message_links = ''
                                         THEN '[]' ELSE preview_message_links END,
            preview_media = CASE WHEN preview_media IS NULL OR preview_media = ''
                                 THEN '[]' ELSE preview_media END,
            first_account_index = CASE WHEN first_account_index > 0 THEN first_account_index ELSE 1 END,
            first_account_id = COALESCE(first_account_id, ''),
            first_round = CASE WHEN first_round > 0 THEN first_round ELSE 1 END,
            seen_count = CASE WHEN seen_count > 0 THEN seen_count ELSE 1 END,
            created_at = CASE WHEN created_at IS NULL OR created_at = '' THEN CURRENT_TIMESTAMP ELSE created_at END,
            last_seen_at = CASE WHEN last_seen_at IS NULL OR last_seen_at = '' THEN CURRENT_TIMESTAMP ELSE last_seen_at END
        """
    )

    provenance_columns = _sqlite_columns(conn, "resource_preview_provenance")
    if "resource_key" not in provenance_columns:
        raise sqlite3.DatabaseError("resource_preview_provenance 缺少 resource_key，无法安全升级")
    original_provenance_columns = _add_sqlite_columns(
        conn,
        "resource_preview_provenance",
        [
            ("source_peer", "TEXT NOT NULL DEFAULT ''"),
            ("source_message_id", "INTEGER"),
            ("message_link", "TEXT NOT NULL DEFAULT ''"),
            ("preview_message_ids", "TEXT NOT NULL DEFAULT '[]'"),
            ("preview_message_links", "TEXT NOT NULL DEFAULT '[]'"),
            ("preview_media", "TEXT NOT NULL DEFAULT '[]'"),
            ("account_index", "INTEGER NOT NULL DEFAULT 1"),
            ("account_id", "TEXT NOT NULL DEFAULT ''"),
            ("request_round", "INTEGER NOT NULL DEFAULT 1"),
            ("seen_at", "TEXT NOT NULL DEFAULT ''"),
        ],
    )
    if "account_index" not in original_provenance_columns and "first_account_index" in original_provenance_columns:
        conn.execute(
            "UPDATE resource_preview_provenance SET account_index = "
            "CASE WHEN first_account_index > 0 THEN first_account_index ELSE 1 END"
        )
    if "request_round" not in original_provenance_columns and "round" in original_provenance_columns:
        conn.execute(
            'UPDATE resource_preview_provenance SET request_round = CASE WHEN "round" > 0 '
            'THEN "round" ELSE 1 END'
        )
    conn.execute(
        """
        UPDATE resource_preview_provenance
        SET source_peer = COALESCE(source_peer, ''),
            message_link = COALESCE(message_link, ''),
            preview_message_ids = CASE WHEN preview_message_ids IS NULL OR preview_message_ids = ''
                                       THEN '[]' ELSE preview_message_ids END,
            preview_message_links = CASE WHEN preview_message_links IS NULL OR preview_message_links = ''
                                         THEN '[]' ELSE preview_message_links END,
            preview_media = CASE WHEN preview_media IS NULL OR preview_media = ''
                                 THEN '[]' ELSE preview_media END,
            account_index = CASE WHEN account_index > 0 THEN account_index ELSE 1 END,
            account_id = COALESCE(account_id, ''),
            request_round = CASE WHEN request_round > 0 THEN request_round ELSE 1 END,
            seen_at = CASE WHEN seen_at IS NULL OR seen_at = '' THEN CURRENT_TIMESTAMP ELSE seen_at END
        """
    )


def init_resource_preview_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    os.chmod(path, 0o600)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS resource_previews (
            resource_key TEXT PRIMARY KEY,
            callback_data TEXT NOT NULL,
            button_text TEXT NOT NULL,
            description TEXT NOT NULL,
            summary_text TEXT NOT NULL,
            photos INTEGER NOT NULL,
            videos INTEGER NOT NULL,
            files INTEGER NOT NULL,
            unlock_cost INTEGER NOT NULL,
            source_peer TEXT NOT NULL,
            source_message_id INTEGER,
            message_link TEXT NOT NULL,
            preview_message_ids TEXT NOT NULL,
            preview_message_links TEXT NOT NULL,
            preview_media TEXT NOT NULL DEFAULT '[]',
            first_account_index INTEGER NOT NULL,
            first_account_id TEXT NOT NULL DEFAULT '',
            first_round INTEGER NOT NULL,
            seen_count INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS resource_preview_provenance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resource_key TEXT NOT NULL,
            source_peer TEXT NOT NULL,
            source_message_id INTEGER,
            message_link TEXT NOT NULL,
            preview_message_ids TEXT NOT NULL,
            preview_message_links TEXT NOT NULL,
            preview_media TEXT NOT NULL DEFAULT '[]',
            account_index INTEGER NOT NULL,
            account_id TEXT NOT NULL DEFAULT '',
            request_round INTEGER NOT NULL,
            seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _upgrade_resource_preview_schema(conn)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS resource_preview_provenance_key ON resource_preview_provenance(resource_key)"
    )
    conn.commit()
    return conn


def catalog_resource_preview(conn: sqlite3.Connection, record: dict[str, Any]) -> dict[str, Any]:
    resource_key = str(record.get("resource_key") or "").strip()
    callback_data = str(record.get("callback_data") or "").strip()
    if not resource_key or len(resource_key) > 512:
        raise ValueError("preview record 缺少有效 resource_key")
    if not callback_data or len(callback_data) > 1024:
        raise ValueError("preview record 缺少有效 callback_data")
    account_index = int(record.get("account_index") or 0)
    request_round = int(record.get("round") or 0)
    if account_index < 1 or request_round < 1:
        raise ValueError("preview record 的 account_index 和 round 必须是正整数")
    source_message_id_raw = record.get("source_message_id")
    source_message_id = int(source_message_id_raw) if source_message_id_raw is not None else None
    preview_message_ids = [int(value) for value in record.get("preview_message_ids", [])]
    preview_message_links = [str(value) for value in record.get("preview_message_links", []) if str(value)]
    preview_media: list[dict[str, Any]] = []
    for item in record.get("preview_media", []):
        if not isinstance(item, dict):
            continue
        message_id = int(item.get("message_id") or 0)
        url = str(item.get("url") or "")
        kind = str(item.get("kind") or "media")
        preview_media.append(
            {
                "message_id": message_id,
                "kind": kind if kind in {"photo", "video"} else "media",
                "url": url,
            }
        )
    ids_json = json.dumps(preview_message_ids, ensure_ascii=False, separators=(",", ":"))
    links_json = json.dumps(preview_message_links, ensure_ascii=False, separators=(",", ":"))
    media_json = json.dumps(preview_media, ensure_ascii=False, separators=(",", ":"))
    values = {
        "resource_key": resource_key,
        "callback_data": callback_data,
        "button_text": str(record.get("button_text") or ""),
        "description": str(record.get("description") or ""),
        "summary_text": str(record.get("summary_text") or ""),
        "photos": int(record.get("photos") or 0),
        "videos": int(record.get("videos") or 0),
        "files": int(record.get("files") or 0),
        "unlock_cost": int(record.get("unlock_cost") or 0),
        "source_peer": str(record.get("source_peer") or ""),
        "source_message_id": source_message_id,
        "message_link": str(record.get("message_link") or ""),
        "preview_message_ids": ids_json,
        "preview_message_links": links_json,
        "preview_media": media_json,
        "account_index": account_index,
        "account_id": str(record.get("account_id") or ""),
        "request_round": request_round,
    }
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO resource_previews
                (resource_key, callback_data, button_text, description, summary_text,
                 photos, videos, files, unlock_cost, source_peer, source_message_id,
                 message_link, preview_message_ids, preview_message_links, preview_media, first_account_index,
                 first_account_id, first_round, created_at, last_seen_at)
            VALUES
                (:resource_key, :callback_data, :button_text, :description, :summary_text,
                 :photos, :videos, :files, :unlock_cost, :source_peer, :source_message_id,
                 :message_link, :preview_message_ids, :preview_message_links, :preview_media, :account_index,
                 :account_id, :request_round, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(resource_key) DO UPDATE SET
                callback_data = CASE WHEN resource_previews.callback_data = ''
                                     THEN excluded.callback_data ELSE resource_previews.callback_data END,
                button_text = CASE WHEN resource_previews.button_text = ''
                                   THEN excluded.button_text ELSE resource_previews.button_text END,
                description = CASE WHEN resource_previews.description = ''
                                   THEN excluded.description ELSE resource_previews.description END,
                summary_text = CASE WHEN resource_previews.summary_text = ''
                                    THEN excluded.summary_text ELSE resource_previews.summary_text END,
                photos = CASE WHEN resource_previews.photos = 0
                              THEN excluded.photos ELSE resource_previews.photos END,
                videos = CASE WHEN resource_previews.videos = 0
                              THEN excluded.videos ELSE resource_previews.videos END,
                files = CASE WHEN resource_previews.files = 0
                             THEN excluded.files ELSE resource_previews.files END,
                unlock_cost = CASE WHEN resource_previews.unlock_cost = 0
                                   THEN excluded.unlock_cost ELSE resource_previews.unlock_cost END,
                preview_message_ids = CASE WHEN resource_previews.preview_message_ids IN ('', '[]')
                                           THEN excluded.preview_message_ids
                                           ELSE resource_previews.preview_message_ids END,
                preview_message_links = CASE WHEN resource_previews.preview_message_links IN ('', '[]')
                                             THEN excluded.preview_message_links
                                             ELSE resource_previews.preview_message_links END,
                preview_media = CASE WHEN resource_previews.preview_media IN ('', '[]')
                                     THEN excluded.preview_media
                                     ELSE resource_previews.preview_media END,
                seen_count = resource_previews.seen_count + 1,
                last_seen_at = CURRENT_TIMESTAMP
            """,
            values,
        )
        conn.execute(
            """
            INSERT INTO resource_preview_provenance
                (resource_key, source_peer, source_message_id, message_link, preview_message_ids,
                 preview_message_links, preview_media, account_index, account_id, request_round, seen_at)
            VALUES
                (:resource_key, :source_peer, :source_message_id, :message_link, :preview_message_ids,
                 :preview_message_links, :preview_media, :account_index, :account_id, :request_round, CURRENT_TIMESTAMP)
            """,
            values,
        )
        seen_count = int(
            conn.execute(
                "SELECT seen_count FROM resource_previews WHERE resource_key = ?",
                (resource_key,),
            ).fetchone()[0]
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        **record,
        "resource_key": resource_key,
        "callback_data": callback_data,
        "preview_message_ids": preview_message_ids,
        "preview_message_links": preview_message_links,
        "preview_media": preview_media,
        "account_id": str(record.get("account_id") or ""),
        "seen_count": seen_count,
        "is_new": seen_count == 1,
    }


def list_resource_previews(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT resource_key, callback_data, button_text, description, summary_text,
               photos, videos, files, unlock_cost, source_peer, source_message_id, message_link,
               preview_message_ids, preview_message_links, preview_media, first_account_index, first_account_id,
               first_round, seen_count, created_at, last_seen_at
        FROM resource_previews
        ORDER BY created_at, resource_key
        """
    ).fetchall()
    previews: list[dict[str, Any]] = []
    for row in rows:
        provenance = [
            {
                "source_peer": item["source_peer"],
                "source_message_id": item["source_message_id"],
                "message_link": item["message_link"],
                "preview_message_ids": json.loads(item["preview_message_ids"]),
                "preview_message_links": json.loads(item["preview_message_links"]),
                "preview_media": json.loads(item["preview_media"]),
                "account_index": item["account_index"],
                "account_id": item["account_id"],
                "round": item["request_round"],
                "seen_at": item["seen_at"],
            }
            for item in conn.execute(
                """
                SELECT source_peer, source_message_id, message_link, preview_message_ids,
                       preview_message_links, preview_media, account_index, account_id, request_round, seen_at
                FROM resource_preview_provenance
                WHERE resource_key = ?
                ORDER BY rowid
                """,
                (row["resource_key"],),
            ).fetchall()
        ]
        previews.append(
            {
                "resource_key": row["resource_key"],
                "callback_data": row["callback_data"],
                "button_text": row["button_text"],
                "description": row["description"],
                "summary_text": row["summary_text"],
                "photos": row["photos"],
                "videos": row["videos"],
                "files": row["files"],
                "unlock_cost": row["unlock_cost"],
                "source_peer": row["source_peer"],
                "source_message_id": row["source_message_id"],
                "message_link": row["message_link"],
                "preview_message_ids": json.loads(row["preview_message_ids"]),
                "preview_message_links": json.loads(row["preview_message_links"]),
                "preview_media": json.loads(row["preview_media"]),
                "account_index": row["first_account_index"],
                "account_id": row["first_account_id"],
                "round": row["first_round"],
                "seen_count": row["seen_count"],
                "created_at": row["created_at"],
                "last_seen_at": row["last_seen_at"],
                "provenance": provenance,
            }
        )
    return previews


def export_resource_previews(conn: sqlite3.Connection, path: Path) -> list[dict[str, Any]]:
    previews = list_resource_previews(conn)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(previews, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return previews


def init_resource_catalog_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    os.chmod(path, 0o600)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS resource_catalog (
            url TEXT PRIMARY KEY,
            source_peer TEXT NOT NULL,
            source_message_id INTEGER,
            message_link TEXT NOT NULL,
            first_account_index INTEGER NOT NULL,
            first_round INTEGER NOT NULL,
            seen_count INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS resource_catalog_provenance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            source_peer TEXT NOT NULL,
            source_message_id INTEGER,
            message_link TEXT NOT NULL,
            account_index INTEGER NOT NULL,
            request_round INTEGER NOT NULL,
            seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS resource_catalog_provenance_url ON resource_catalog_provenance(url)"
    )
    conn.commit()
    return conn


def catalog_resource(conn: sqlite3.Connection, record: dict[str, Any]) -> dict[str, Any]:
    url = normalize_resource_url(str(record.get("url") or ""))
    if not url:
        raise ValueError("resource record 缺少有效 url")
    source_peer = str(record.get("source_peer") or "")
    source_message_id_raw = record.get("source_message_id")
    source_message_id = int(source_message_id_raw) if source_message_id_raw is not None else None
    message_link = str(record.get("message_link") or "")
    account_index = int(record.get("account_index") or 0)
    request_round = int(record.get("round") or 0)
    if account_index < 1 or request_round < 1:
        raise ValueError("resource record 的 account_index 和 round 必须是正整数")

    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO resource_catalog
                (url, source_peer, source_message_id, message_link, first_account_index, first_round)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                seen_count = resource_catalog.seen_count + 1,
                last_seen_at = CURRENT_TIMESTAMP
            """,
            (url, source_peer, source_message_id, message_link, account_index, request_round),
        )
        conn.execute(
            """
            INSERT INTO resource_catalog_provenance
                (url, source_peer, source_message_id, message_link, account_index, request_round)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (url, source_peer, source_message_id, message_link, account_index, request_round),
        )
        seen_count = int(
            conn.execute("SELECT seen_count FROM resource_catalog WHERE url = ?", (url,)).fetchone()[0]
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "url": url,
        "source_peer": source_peer,
        "source_message_id": source_message_id,
        "message_link": message_link,
        "account_index": account_index,
        "round": request_round,
        "seen_count": seen_count,
        "is_new": seen_count == 1,
    }


def list_catalog_resources(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    rows = conn.execute(
        """
        SELECT url, source_peer, source_message_id, message_link,
               first_account_index, first_round, seen_count, created_at, last_seen_at
        FROM resource_catalog
        ORDER BY created_at, url
        """
    ).fetchall()
    for row in rows:
        provenance = [
            {
                "source_peer": item["source_peer"],
                "source_message_id": item["source_message_id"],
                "message_link": item["message_link"],
                "account_index": item["account_index"],
                "round": item["request_round"],
                "seen_at": item["seen_at"],
            }
            for item in conn.execute(
                """
                SELECT source_peer, source_message_id, message_link,
                       account_index, request_round, seen_at
                FROM resource_catalog_provenance
                WHERE url = ?
                ORDER BY id
                """,
                (row["url"],),
            ).fetchall()
        ]
        resources.append(
            {
                "url": row["url"],
                "source_peer": row["source_peer"],
                "source_message_id": row["source_message_id"],
                "message_link": row["message_link"],
                "account_index": row["first_account_index"],
                "round": row["first_round"],
                "seen_count": row["seen_count"],
                "created_at": row["created_at"],
                "last_seen_at": row["last_seen_at"],
                "provenance": provenance,
            }
        )
    return resources


def export_resource_catalog(conn: sqlite3.Connection, path: Path) -> list[dict[str, Any]]:
    resources = list_catalog_resources(conn)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(resources, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return resources


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
