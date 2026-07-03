#!/usr/bin/env python3
"""Run Telegram sign-in jobs from a plaintext YAML config.

Sensitive values are read from GitHub Actions Secrets via environment variables.
The YAML config should only contain editable non-secret workflow configuration.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import subprocess
import sys
import traceback
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.types import Channel, Chat, User

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
        return int(api_id_raw), api_hash
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


def bot_api_notify(bot_token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text[:3900],
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()


def build_summary(result: RunResult, selected_texts: list[str]) -> str:
    lines = [
        f"TG 签到任务: {result.job_id}",
        f"账号序号: {result.account_index}",
        f"状态: {result.status}",
        f"命中过滤: {'是' if result.matched else '否'}",
        "",
        result.summary,
    ]
    if selected_texts:
        lines.append("")
        lines.append("匹配消息:")
        for idx, text in enumerate(selected_texts, start=1):
            lines.append(f"--- #{idx} ---")
            lines.append(text[:1200])
    return "\n".join(lines)


def result_texts(result: RunResult) -> list[str]:
    return [message_text(msg) for msg in result.messages if message_text(msg)]


def result_status_label(status: str) -> str:
    return {
        "success": "成功",
        "failure": "失败",
    }.get(status, status)


def result_to_report_item(result: RunResult) -> dict[str, Any]:
    return {
        "account_index": result.account_index,
        "status": result.status,
        "matched": result.matched,
        "summary": result.summary,
        "messages": result_texts(result),
        "node_logs": result.node_logs,
    }


def job_to_report_workflow(job: dict[str, Any]) -> dict[str, Any]:
    nodes: list[dict[str, str]] = []
    edges: list[dict[str, str]] = []
    previous = str(job.get("node_id") or "")
    if previous:
        nodes.append({"id": previous, "kind": "task", "label": str(job.get("id") or previous)})
    seen = {previous} if previous else set()
    for action in job.get("actions", []) or []:
        node_id = str(action.get("node_id") or "")
        if not node_id:
            continue
        if node_id not in seen:
            nodes.append({"id": node_id, "kind": str(action.get("type") or "action"), "label": str(action.get("type") or node_id)})
            seen.add(node_id)
        if previous and previous != node_id:
            edges.append({"source": previous, "target": node_id})
        previous = node_id
    return {"nodes": nodes, "edges": edges}


def write_report(grouped: dict[str, list[RunResult]], path: Path, jobs: list[dict[str, Any]] | None = None) -> None:
    jobs_by_id = {str(job.get("id")): job for job in jobs or [] if isinstance(job, dict)}
    report = {
        "tasks": [
            {
                "id": job_id,
                "workflow": job_to_report_workflow(jobs_by_id.get(job_id, {})),
                "accounts": [result_to_report_item(result) for result in results],
            }
            for job_id, results in grouped.items()
        ]
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 TG 签到报告: {path}")


def send_report_with_emall(report_path: Path) -> None:
    command = [
        "uv",
        "run",
        "python",
        "send_tg_report.py",
        str(report_path),
    ]
    subprocess.run(command, cwd=WORKFLOWS_ROOT / "emall", check=True)


async def join_target(client: TelegramClient, target: str) -> Any | None:
    normalized = normalize_join_target(target)
    try:
        if normalized.startswith("+"):
            updates = await client(ImportChatInviteRequest(normalized[1:]))
            chats = getattr(updates, "chats", None) or []
            if chats:
                print(f"已通过邀请链接加入: {entity_label(chats[0])}", flush=True)
                return chats[0]
            return None

        entity = await client.get_entity(normalized)
        if isinstance(entity, Channel):
            await client(JoinChannelRequest(entity))
            print(f"已加入频道/群组: {entity_label(entity)}", flush=True)
            return entity
        if isinstance(entity, Chat):
            print(f"普通群组已可访问，无需加入: {entity_label(entity)}", flush=True)
            return entity
        if isinstance(entity, User):
            if getattr(entity, "bot", False):
                await client.send_message(entity, "/start")
                print(f"已向 bot 发起对话: {entity_label(entity)}", flush=True)
                return entity
            print(f"目标是用户账号，已确认可访问: {entity_label(entity)}", flush=True)
            return entity
        raise RuntimeError(f"不支持的 join 目标类型: {entity.__class__.__name__}")
    except errors.UserAlreadyParticipantError:
        print(f"账号已在目标中: {target}", flush=True)
        if not normalized.startswith("+"):
            return await client.get_entity(normalized)
        checked = await client(CheckChatInviteRequest(normalized[1:]))
        chat = getattr(checked, "chat", None)
        if chat:
            return chat
        return None


async def run_actions(client: TelegramClient, peer: str, actions: list[dict[str, Any]], account_index: int) -> dict[str, Any]:
    current_peer = peer
    last_messages: list[Any] = []
    context: dict[str, Any] = {"node_logs": {}}

    def log_node(node_id: str, message: str) -> None:
        if node_id:
            context.setdefault("node_logs", {}).setdefault(node_id, []).append(message)

    for action in actions:
        action_type = str(action.get("type", "")).strip().lower()
        node_id = str(action.get("node_id") or "")
        if node_id:
            print(f"::node::{node_id}::start::{action_type}", flush=True)
            log_node(node_id, f"开始 {action_type}")
        try:
            if action_type == "open":
                current_peer = str(action.get("peer") or current_peer).strip()
                if not current_peer:
                    raise RuntimeError("open action 缺少 peer")
                print(f"打开对话: {current_peer}", flush=True)
                log_node(node_id, f"打开对话: {current_peer}")
                await client.get_entity(current_peer)
                if node_id:
                    print(f"::node::{node_id}::done::{action_type}", flush=True)
                    log_node(node_id, f"完成 {action_type}")
                continue
        except Exception:
            if node_id:
                print(f"::node::{node_id}::error::{action_type}", flush=True)
                log_node(node_id, f"失败 {action_type}")
            raise

        if action_type == "open":
            current_peer = str(action.get("peer") or current_peer).strip()
            if not current_peer:
                raise RuntimeError("open action 缺少 peer")
            print(f"打开对话: {current_peer}", flush=True)
            log_node(node_id, f"打开对话: {current_peer}")
            await client.get_entity(current_peer)

        elif action_type == "open_link":
            source = str(action.get("source", "")).strip()
            url = str(action.get("url", "")).strip()
            if source:
                source_value = context.get(source)
                if isinstance(source_value, list):
                    url = str(source_value[0] if source_value else "").strip()
                elif source_value:
                    url = str(source_value).strip()
            if not url:
                raise RuntimeError("open_link action 缺少 url")
            print(f"打开链接: {url}", flush=True)
            log_node(node_id, f"打开链接: {url}")
            await client.send_message("me", url)

        elif action_type == "join":
            source = str(action.get("source", "")).strip()
            target = str(action.get("target", "")).strip()
            if source:
                source_value = context.get(source)
                if isinstance(source_value, list):
                    target = str(source_value[0] if source_value else "").strip()
                elif source_value:
                    target = str(source_value).strip()
            if not target:
                raise RuntimeError("join action 缺少 target")
            print(f"加入账号/群组: {target}", flush=True)
            log_node(node_id, f"加入账号/群组: {target}")
            next_peer = await join_target(client, target)
            if next_peer:
                current_peer = next_peer

        elif action_type == "send":
            text = str(action.get("text") if action.get("text") is not None else action.get("cmd", ""))
            if not text:
                raise RuntimeError("send action 缺少 text/cmd")
            print(f"发送消息到 {current_peer}: {text}", flush=True)
            log_node(node_id, f"发送消息到 {current_peer}: {text}")
            sent = await client.send_message(current_peer, text)
            context["last_send_peer"] = current_peer
            context["last_send_id"] = getattr(sent, "id", None)
            print(f"已发送消息 id={context['last_send_id']}", flush=True)
            log_node(node_id, f"已发送消息 id={context['last_send_id']}")

        elif action_type == "click":
            text = str(action.get("text") if action.get("text") is not None else action.get("cmd", ""))
            if not text:
                raise RuntimeError("click action 缺少 text/cmd")
            search_limit = int(action.get("search_limit", 5))
            print(f"查找并点击按钮: {text}", flush=True)
            log_node(node_id, f"查找并点击按钮: {text}")
            messages = await client.get_messages(current_peer, limit=search_limit)
            clicked = False
            for msg in messages:
                try:
                    await msg.click(text=text)
                    clicked = True
                    print(f"已点击按钮: {text}", flush=True)
                    log_node(node_id, f"已点击按钮: {text}")
                    break
                except Exception:
                    continue
            if not clicked:
                raise RuntimeError(f"没有找到可点击按钮: {text}")

        elif action_type == "wait":
            seconds = float(action.get("seconds", 1))
            print(f"等待 {seconds} 秒", flush=True)
            log_node(node_id, f"等待 {seconds} 秒")
            await asyncio.sleep(seconds)

        elif action_type == "parse":
            pattern = str(action.get("pattern", ""))
            limit = int(action.get("limit", 5) or 5)
            use_regex = bool(action.get("regex", True))
            save_as = str(action.get("save_as", "last_parse"))
            extract = str(action.get("extract", "messages")).strip().lower()
            after_send = bool(action.get("after_send") or str(action.get("mode", "")).strip().lower() in {"after_send", "after-send"})
            if after_send:
                sent_id = context.get("last_send_id")
                source_peer = str(context.get("last_send_peer") or current_peer)
                if not sent_id:
                    raise RuntimeError("parse.after_send=true 需要前面先执行 send action")
                wait_seconds = float(action.get("wait_seconds", 3) or 0)
                if wait_seconds > 0:
                    print(f"等待回执 {wait_seconds} 秒", flush=True)
                    log_node(node_id, f"等待回执 {wait_seconds} 秒")
                    await asyncio.sleep(wait_seconds)
                print(f"无条件解析发送消息之后的回执: peer={source_peer}, sent_id={sent_id}, limit={limit}", flush=True)
                log_node(node_id, f"解析发送后回执: peer={source_peer}, limit={limit}")
                scanned = list(await client.get_messages(source_peer, limit=limit))
                last_messages = [
                    msg
                    for msg in scanned
                    if getattr(msg, "id", 0) > int(sent_id)
                    and not bool(getattr(msg, "out", False))
                ]
            else:
                print(f"解析最近 {limit} 条消息: {pattern}", flush=True)
                log_node(node_id, f"解析最近 {limit} 条消息: {pattern}")
                last_messages = list(await client.get_messages(current_peer, limit=limit))
            matched = []
            extracted: list[str] = []
            for msg in last_messages:
                text = message_text(msg)
                if after_send or not pattern or (re.search(pattern, text, flags=re.I | re.M) if use_regex else pattern in text):
                    matched.append(msg)
                if extract in {"links", "join_links"}:
                    extracted.extend(TG_LINK_RE.findall(text))
                    for row in getattr(msg, "buttons", None) or []:
                        buttons = row if isinstance(row, list) else [row]
                        for button in buttons:
                            url = str(getattr(button, "url", "") or "")
                            button_text = str(getattr(button, "text", "") or "")
                            if url and (extract == "links" or "t.me/" in url or "telegram.me/" in url or "tg://join" in url):
                                extracted.append(url)
                            elif extract == "join_links":
                                extracted.extend(TG_LINK_RE.findall(button_text))
            context[save_as] = matched
            if extract in {"links", "join_links"}:
                unique = list(dict.fromkeys(extracted))
                context[save_as] = unique
                context["last_links"] = unique
            context["last_parse"] = matched
            if after_send:
                context["last_parse_unconditional"] = True
            print(f"解析命中 {len(matched)} 条消息，提取 {len(extracted)} 个目标", flush=True)
            log_node(node_id, f"解析命中 {len(matched)} 条消息，提取 {len(extracted)} 个目标")
            if matched:
                print("解析结果:", flush=True)
                for idx, msg in enumerate(matched, start=1):
                    print(f"--- parse #{idx} ---", flush=True)
                    print(message_text(msg) or "[空消息]", flush=True)
            else:
                print("解析结果: 未命中", flush=True)

        elif action_type == "forward":
            to_peer = str(action.get("to_peer", "")).strip()
            if not to_peer:
                raise RuntimeError("forward action 缺少 to_peer")
            source = str(action.get("source", "last_parse"))
            selected = context.get(source) or context.get("last_parse") or last_messages
            print(f"转发 {len(selected)} 条消息到 {to_peer}", flush=True)
            log_node(node_id, f"转发 {len(selected)} 条消息到 {to_peer}")
            if selected:
                await client.forward_messages(to_peer, selected)

        elif action_type == "resource_pool":
            pool_peer = str(action.get("pool_peer") or action.get("to_peer") or "").strip()
            if not pool_peer:
                raise RuntimeError("resource_pool action 缺少 pool_peer")
            pool_target: Any = pool_peer
            if bool(action.get("join_pool", True)):
                print(f"确认账号已加入资源池: {pool_peer}", flush=True)
                log_node(node_id, f"确认账号已加入资源池: {pool_peer}")
                joined = await join_target(client, pool_peer)
                if joined is not None:
                    pool_target = joined

            source = str(action.get("source", "last_parse"))
            selected = context.get(source) or context.get("last_parse") or last_messages
            if not isinstance(selected, list):
                selected = [selected]
            allowed_types = {item.lower() for item in listify(action.get("media_types") or ["photo", "video"])}
            db_path = resolve_local_path(
                os.environ.get("TG_RESOURCE_POOL_DB") or str(action.get("dedupe_store") or ""),
                DEFAULT_RESOURCE_POOL_DB,
            )
            dedupe = bool(action.get("dedupe", True))
            forwarded = 0
            skipped_duplicate = 0
            skipped_non_media = 0
            skipped_unknown = 0
            conn = init_resource_pool_db(db_path) if dedupe else None
            try:
                for msg in selected:
                    kind = media_kind(msg)
                    if not kind or kind not in allowed_types:
                        skipped_non_media += 1
                        continue
                    fingerprint = media_fingerprint(msg)
                    if dedupe and not fingerprint:
                        skipped_unknown += 1
                        print(f"跳过无法生成去重指纹的媒体消息 id={getattr(msg, 'id', '')}", flush=True)
                        continue
                    if conn is not None and fingerprint and resource_seen(conn, fingerprint):
                        skipped_duplicate += 1
                        continue
                    await client.forward_messages(pool_target, msg)
                    forwarded += 1
                    if conn is not None and fingerprint:
                        mark_resource_seen(
                            conn,
                            fingerprint,
                            kind,
                            pool_peer,
                            str(current_peer),
                            getattr(msg, "id", None),
                            account_index,
                        )
                print(
                    f"资源池转存完成: 转发 {forwarded}，重复 {skipped_duplicate}，非图片/视频 {skipped_non_media}，无指纹 {skipped_unknown}，db={db_path}",
                    flush=True,
                )
                log_node(node_id, f"资源池转存完成: 转发 {forwarded}，重复 {skipped_duplicate}，非图片/视频 {skipped_non_media}，无指纹 {skipped_unknown}")
            finally:
                if conn is not None:
                    conn.close()

        else:
            raise RuntimeError(f"不支持的 action type: {action_type}")

        if action_type not in {"wait", "open"} and action.get("wait_seconds"):
            seconds = float(action.get("wait_seconds", 1))
            print(f"动作后等待 {seconds} 秒", flush=True)
            await asyncio.sleep(seconds)

        if node_id:
            print(f"::node::{node_id}::done::{action_type}", flush=True)
            log_node(node_id, f"完成 {action_type}")

    return context


async def collect_messages(client: TelegramClient, peer: str, collect: dict[str, Any], forward: dict[str, Any]) -> list[Any]:
    wait_seconds = float(collect.get("wait_seconds", 0) or 0)
    if wait_seconds > 0:
        print(f"收集消息前等待 {wait_seconds} 秒")
        await asyncio.sleep(wait_seconds)
    limit = int(collect.get("last_messages", forward.get("max_messages", 3)) or 3)
    return list(await client.get_messages(peer, limit=limit))


async def handle_forward(client: TelegramClient, forward: dict[str, Any], result: RunResult) -> None:
    if not should_forward(forward, result.status, result.matched):
        print("转发规则未命中，跳过转发/通知")
        return

    selected = [msg for msg in result.messages if text_matches(message_text(msg), forward)]
    selected_texts = [message_text(msg) for msg in selected if message_text(msg)]
    summary = build_summary(result, selected_texts)
    mode = str(forward.get("mode", "notify")).strip().lower()

    if mode == "notify":
        bot_token_secret = str(forward.get("bot_token_secret", "TG_FORWARD_BOT_TOKEN"))
        chat_id_secret = str(forward.get("chat_id_secret", "TG_FORWARD_CHAT_ID"))
        bot_token = required_env(bot_token_secret)
        chat_id = str(forward.get("chat_id") or required_env(chat_id_secret))
        print(f"发送通知到 chat_id={chat_id}")
        bot_api_notify(bot_token, chat_id, summary)
        return

    if mode == "user_forward":
        to_peer = forward.get("to_peer")
        if not to_peer:
            raise RuntimeError("user_forward 模式需要配置 forward.to_peer")
        if selected:
            print(f"转发 {len(selected)} 条匹配消息到 {to_peer}")
            await client.forward_messages(to_peer, selected)
        else:
            print(f"没有匹配消息，发送摘要到 {to_peer}")
            await client.send_message(to_peer, summary)
        return

    raise RuntimeError(f"不支持的 forward.mode: {mode}")


async def run_one_account(job: dict[str, Any], defaults: dict[str, Any], session: str, account_index: int) -> RunResult:
    job_id = str(job["id"])
    job_node_id = str(job.get("node_id") or "")
    api_id_secret = str(job.get("api_id_secret") or defaults.get("api_id_secret") or "TG_API_ID")
    api_hash_secret = str(job.get("api_hash_secret") or defaults.get("api_hash_secret") or "TG_API_HASH")
    proxy_secret = str(job.get("proxy_secret") or defaults.get("proxy_secret") or "TG_PROXY")

    api_id, api_hash = telegram_api_config(api_id_secret, api_hash_secret)
    proxy = parse_proxy(optional_env(proxy_secret))
    peer = str(job.get("peer", "")).strip()
    if not peer:
        raise RuntimeError(f"任务 {job_id} 缺少 peer")

    default_forward = defaults.get("forward", {}) or {}
    job_forward = deep_merge(default_forward, job.get("forward", {}) or {})
    collect = job.get("collect", {}) or {}
    actions = job.get("actions", []) or []
    if not actions:
        raise RuntimeError(f"任务 {job_id} 没有 actions")

    if job_node_id:
        print(f"::node::{job_node_id}::start::task", flush=True)
    print(f"开始任务 {job_id} / 账号 #{account_index} / peer={peer}", flush=True)
    client = TelegramClient(StringSession(session), api_id, api_hash, proxy=proxy)

    messages: list[Any] = []
    context: dict[str, Any] = {"node_logs": {}}
    try:
        async with client:
            context = await run_actions(client, peer, actions, account_index)
            messages = await collect_messages(client, peer, collect, job_forward)
            parsed_messages = context.get("last_parse")
            if isinstance(parsed_messages, list) and parsed_messages:
                by_id = {getattr(msg, "id", idx): msg for idx, msg in enumerate(messages)}
                for msg in parsed_messages:
                    by_id[getattr(msg, "id", len(by_id))] = msg
                messages = list(by_id.values())
            matched = any(text_matches(message_text(msg), job_forward) for msg in messages)
            if context.get("last_parse_unconditional") and isinstance(parsed_messages, list) and parsed_messages:
                matched = True
            if not matched:
                print("没有命中过滤条件。最近收集到的消息:")
                for idx, msg in enumerate(messages, start=1):
                    print(f"--- message #{idx} ---")
                    print(message_text(msg) or "[空消息]")
            result = RunResult(
                job_id=job_id,
                account_index=account_index,
                status="success",
                matched=matched,
                summary="任务执行成功。",
                messages=messages,
                node_logs=dict(context.get("node_logs", {})) if isinstance(context.get("node_logs"), dict) else {},
            )
            await handle_forward(client, job_forward, result)
            if job_node_id:
                print(f"::node::{job_node_id}::done::task", flush=True)
            return result
    except Exception as exc:
        if job_node_id:
            print(f"::node::{job_node_id}::error::task", flush=True)
        summary = "任务执行失败。\n" + "".join(traceback.format_exception_only(type(exc), exc)).strip()
        result = RunResult(
            job_id=job_id,
            account_index=account_index,
            status="failure",
            matched=False,
            summary=summary,
            messages=messages,
            node_logs=dict(context.get("node_logs", {})) if isinstance(context.get("node_logs"), dict) else {},
        )
        try:
            if client.is_connected():
                await handle_forward(client, job_forward, result)
            else:
                # notify 模式不需要用户 client。失败时尽量仍发出通知。
                mode = str(job_forward.get("mode", "notify")).strip().lower()
                if mode == "notify" and should_forward(job_forward, result.status, result.matched):
                    bot_token = required_env(str(job_forward.get("bot_token_secret", "TG_FORWARD_BOT_TOKEN")))
                    chat_id = str(job_forward.get("chat_id") or required_env(str(job_forward.get("chat_id_secret", "TG_FORWARD_CHAT_ID"))))
                    bot_api_notify(bot_token, chat_id, build_summary(result, []))
        finally:
            raise


async def run_job(config: dict[str, Any], job_id: str) -> int:
    defaults = config.get("defaults", {}) or {}
    jobs = config.get("jobs", []) or []
    selected = next((job for job in jobs if str(job.get("id")) == job_id), None)
    if not selected:
        raise SystemExit(f"没有找到 signins job: {job_id}")
    if not selected.get("enabled", False):
        print(f"任务 {job_id} 当前 enabled=false，跳过。")
        return 0

    accounts_secret = str(selected.get("accounts_secret") or defaults.get("accounts_secret") or "TG_SESSION_STRINGS")
    sessions = read_secret_lines(accounts_secret)

    failures = 0
    for index, session in enumerate(sessions, start=1):
        try:
            await run_one_account(selected, defaults, session, index)
        except Exception as exc:
            failures += 1
            print(f"账号 #{index} 执行失败: {exc}", file=sys.stderr)

    if failures:
        print(f"任务 {job_id} 完成，但有 {failures}/{len(sessions)} 个账号失败。")
        return 1
    print(f"任务 {job_id} 全部账号执行成功。")
    return 0


async def run_job_collect(config: dict[str, Any], job_id: str) -> tuple[list[RunResult], int]:
    defaults = config.get("defaults", {}) or {}
    jobs = config.get("jobs", []) or []
    selected = next((job for job in jobs if str(job.get("id")) == job_id), None)
    if not selected:
        raise SystemExit(f"没有找到 signins job: {job_id}")
    if not selected.get("enabled", False):
        print(f"任务 {job_id} 当前 enabled=false，跳过。")
        return [], 0

    accounts_secret = str(selected.get("accounts_secret") or defaults.get("accounts_secret") or "TG_SESSION_STRINGS")
    sessions = read_secret_lines(accounts_secret)

    results: list[RunResult] = []
    failures = 0
    for index, session in enumerate(sessions, start=1):
        try:
            results.append(await run_one_account(selected, defaults, session, index))
        except Exception as exc:
            failures += 1
            print(f"账号 #{index} 执行失败: {exc}", file=sys.stderr)
            results.append(
                RunResult(
                    job_id=job_id,
                    account_index=index,
                    status="failure",
                    matched=False,
                    summary=str(exc),
                    messages=[],
                    node_logs={},
                )
            )

    if failures:
        print(f"任务 {job_id} 完成，但有 {failures}/{len(sessions)} 个账号失败。")
        return results, 1
    print(f"任务 {job_id} 全部账号执行成功。")
    return results, 0


async def run_enabled(config: dict[str, Any], mail: bool, report_path: Path) -> int:
    jobs = [
        str(job.get("id"))
        for job in config.get("jobs", []) or []
        if isinstance(job, dict) and job.get("enabled", False)
    ]
    if not jobs:
        print("没有 enabled=true 的 signins job。")
        return 0

    grouped: dict[str, list[RunResult]] = {}
    exit_code = 0
    for job_id in jobs:
        results, code = await run_job_collect(config, job_id)
        grouped[job_id] = results
        exit_code = max(exit_code, code)

    write_report(grouped, report_path, config.get("jobs", []) or [])
    if mail:
        send_report_with_emall(report_path)
    return exit_code


async def list_jobs(config: dict[str, Any]) -> int:
    for job in config.get("jobs", []) or []:
        status = "enabled" if job.get("enabled", False) else "disabled"
        print(f"- {job.get('id')} [{status}] peer={job.get('peer')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run TG sign-in jobs from signins.yml")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to signins.yml")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    run = sub.add_parser("run")
    run.add_argument("job_id")
    run_enabled_parser = sub.add_parser("run-enabled")
    run_enabled_parser.add_argument("--mail", action="store_true", help="运行后发送 HTML 邮件报告")
    run_enabled_parser.add_argument("--report", default=str(DEFAULT_REPORT), help="输出 JSON 报告路径")

    args = parser.parse_args()
    config_path = Path(args.config)
    if config_path == DEFAULT_CONFIG and not config_path.exists() and LEGACY_DEFAULT_CONFIG.exists():
        config_path = LEGACY_DEFAULT_CONFIG
    config = load_yaml(config_path.resolve())
    if args.cmd == "list":
        return asyncio.run(list_jobs(config))
    if args.cmd == "run":
        return asyncio.run(run_job(config, args.job_id))
    if args.cmd == "run-enabled":
        return asyncio.run(run_enabled(config, args.mail, Path(args.report).resolve()))
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    raise SystemExit(main())
