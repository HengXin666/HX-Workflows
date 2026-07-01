#!/usr/bin/env python3
"""Local-only HTTP API for the TG workflow web app."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import qrcode
import socks
import yaml
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telethon.tl.functions.users import GetFullUserRequest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ALT_OUT = ROOT / "sessions" / "tg_session_strings.txt"
DEFAULT_MAIN_OUT = ROOT / "sessions" / "tg_main_session_string.txt"
DEFAULT_WORKFLOWS = ROOT / "sessions" / "web_workflows.json"
DEFAULT_API_ID = 611335
DEFAULT_API_HASH = "d524b414d21f4d37f08684c1df41ac9c"
COMMAND_RE = re.compile(r"(?<!\S)/[A-Za-z0-9_]{1,32}")


@dataclass
class LoginJob:
    id: str
    role: str
    status: str = "pending"
    url: str | None = None
    qr_image: str | None = None
    account: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)


@dataclass
class RunJob:
    id: str
    status: str = "running"
    output: str = ""
    active_node: str | None = None
    completed_nodes: list[str] = field(default_factory=list)
    failed_node: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)


login_jobs: dict[str, LoginJob] = {}
run_jobs: dict[str, RunJob] = {}
run_events: dict[str, threading.Condition] = {}
jobs_lock = threading.Lock()


def resolve_api_id(value: str | None) -> int:
    return int(value or os.environ.get("TG_API_ID") or DEFAULT_API_ID)


def resolve_api_hash(value: str | None) -> str:
    return value or os.environ.get("TG_API_HASH") or DEFAULT_API_HASH


def parse_proxy(proxy_url: str | None):
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    proxy_types = {
        "http": socks.HTTP,
        "socks4": socks.SOCKS4,
        "socks5": socks.SOCKS5,
    }
    if parsed.scheme not in proxy_types:
        raise RuntimeError(f"不支持的代理协议: {parsed.scheme}")
    if not parsed.hostname or not parsed.port:
        raise RuntimeError("代理地址必须包含 host 和 port")
    return (
        proxy_types[parsed.scheme],
        parsed.hostname,
        parsed.port,
        True,
        parsed.username,
        parsed.password,
    )


def make_qr_data_url(url: str) -> str:
    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="#0b1220", back_color="#ffffff")
    buf = BytesIO()
    image.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def append_session(path: Path, session: str) -> None:
    sessions = read_lines(path)
    if session not in sessions:
        sessions.append(session)
        write_lines(path, sessions)


def account_cache_path(path: Path) -> Path:
    return path.with_suffix(".accounts.json")


def load_account_cache(*paths: Path) -> dict[str, dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = {}
    for path in paths:
        meta = account_cache_path(path)
        if meta.exists():
            for item in json.loads(meta.read_text(encoding="utf-8")):
                session = str(item.get("session") or "")
                if session:
                    cache[session] = item
    return cache


def save_account_cache(path: Path, accounts: list[dict[str, Any]]) -> None:
    meta = account_cache_path(path)
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")


async def profile_from_session(session: str, api_id: int, api_hash: str, proxy=None) -> dict[str, Any]:
    client = TelegramClient(StringSession(session), api_id, api_hash, proxy=proxy)
    await client.connect()
    try:
        me = await client.get_me()
        if not me:
            raise RuntimeError("无法读取账号信息")
        avatar_data = ""
        photo = await client.download_profile_photo(me, file=bytes)
        if photo:
            avatar_data = "data:image/jpeg;base64," + base64.b64encode(photo).decode("ascii")
        username = f"@{me.username}" if getattr(me, "username", None) else ""
        full_name = " ".join(x for x in [getattr(me, "first_name", ""), getattr(me, "last_name", "")] if x).strip()
        return {
            "telegram_id": str(me.id),
            "username": username,
            "name": full_name or username or str(me.id),
            "avatar": avatar_data,
        }
    finally:
        await client.disconnect()


def masked_session(session: str) -> str:
    return f"{session[:8]}...{session[-6:]}" if len(session) > 18 else "stored"


def accounts_from_file(path: Path, role: str, cache: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    accounts = []
    for index, session in enumerate(read_lines(path), start=1):
        cached = cache.get(session, {})
        accounts.append(
            {
                "id": f"{role}-{index}",
                "role": role,
                "name": cached.get("name") or ("本地主账号" if role == "main" else f"本地小号 {index}"),
                "username": cached.get("username") or "",
                "telegram_id": cached.get("telegram_id") or "",
                "avatar": cached.get("avatar") or "",
                "source": str(path),
                "masked_session": masked_session(session),
            }
        )
    return accounts


def sessions_with_accounts(alt_out: Path, main_out: Path) -> list[tuple[dict[str, Any], str]]:
    cache = load_account_cache(alt_out, main_out)
    rows: list[tuple[dict[str, Any], str]] = []
    for account in accounts_from_file(main_out, "main", cache):
        sessions = read_lines(Path(account["source"]))
        index = int(account["id"].split("-")[-1]) - 1
        if 0 <= index < len(sessions):
            rows.append((account, sessions[index]))
    for account in accounts_from_file(alt_out, "alt", cache):
        sessions = read_lines(Path(account["source"]))
        index = int(account["id"].split("-")[-1]) - 1
        if 0 <= index < len(sessions):
            rows.append((account, sessions[index]))
    return rows


def session_for_account(account_id: str, alt_out: Path, main_out: Path) -> str:
    for account, session in sessions_with_accounts(alt_out, main_out):
        if account.get("id") == account_id:
            return session
    raise RuntimeError(f"账号不存在或未导入 session: {account_id}")


def iso_date(value: Any) -> str:
    if not value:
        return ""
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def resolve_peer_value(peer: str) -> str | int:
    value = peer.strip()
    if value and value.lstrip("-").isdigit():
        return int(value)
    return value


async def resolve_dialog_entity(client: TelegramClient, peer: str):
    value = peer.strip()
    if not value:
        raise RuntimeError("缺少 peer")
    if not value.lstrip("-").isdigit() or value == "me":
        return await client.get_entity(value)

    dialogs = await client.get_dialogs(limit=500)
    for dialog in dialogs:
        entity = dialog.entity
        username = getattr(entity, "username", "") or ""
        candidates = {
            str(dialog.id),
            str(getattr(entity, "id", "")),
            f"@{username}" if username else "",
            username,
        }
        if value in candidates:
            return entity

    return await client.get_input_entity(resolve_peer_value(value))


async def list_dialogs(
    session: str,
    api_id: int,
    api_hash: str,
    proxy=None,
    limit: int = 40,
    query: str = "",
) -> list[dict[str, Any]]:
    client = TelegramClient(StringSession(session), api_id, api_hash, proxy=proxy)
    await client.connect()
    try:
        dialogs = await client.get_dialogs(limit=limit)
        items: list[dict[str, Any]] = []
        needle = query.casefold().strip()
        for dialog in dialogs:
            entity = dialog.entity
            username = getattr(entity, "username", "") or ""
            title = dialog.name or username or str(dialog.id)
            if needle and needle not in title.casefold() and needle not in username.casefold():
                continue
            message = dialog.message
            items.append(
                {
                    "id": str(dialog.id),
                    "title": title,
                    "username": f"@{username}" if username else "",
                    "type": entity.__class__.__name__,
                    "unread_count": int(getattr(dialog, "unread_count", 0) or 0),
                    "date": iso_date(getattr(message, "date", None)),
                    "last_message": getattr(message, "message", "") if message else "",
                }
            )
        return items
    finally:
        await client.disconnect()


async def list_messages(
    session: str,
    api_id: int,
    api_hash: str,
    peer: str,
    proxy=None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    client = TelegramClient(StringSession(session), api_id, api_hash, proxy=proxy)
    await client.connect()
    try:
        entity = await resolve_dialog_entity(client, peer)
        messages = await client.get_messages(entity, limit=limit)
        rows: list[dict[str, Any]] = []
        for message in reversed(messages):
            sender_name = ""
            try:
                sender = await message.get_sender()
                sender_name = (
                    " ".join(
                        x
                        for x in [getattr(sender, "first_name", ""), getattr(sender, "last_name", "")]
                        if x
                    ).strip()
                    or getattr(sender, "title", "")
                    or getattr(sender, "username", "")
                    or ""
                )
            except Exception:
                sender_name = ""
            text = getattr(message, "message", "") or ""
            if not text and getattr(message, "media", None):
                text = f"[{message.media.__class__.__name__}]"
            rows.append(
                {
                    "id": str(message.id),
                    "date": iso_date(getattr(message, "date", None)),
                    "out": bool(getattr(message, "out", False)),
                    "sender": sender_name,
                    "text": text,
                }
            )
        return rows
    finally:
        await client.disconnect()


async def list_bot_commands(
    session: str,
    api_id: int,
    api_hash: str,
    peer: str,
    proxy=None,
) -> dict[str, Any]:
    client = TelegramClient(StringSession(session), api_id, api_hash, proxy=proxy)
    await client.connect()
    try:
        def add_suggestion(items: list[dict[str, str]], seen: set[str], text: str, description: str) -> bool:
            value = text.strip()
            if not value or value in seen:
                return False
            seen.add(value)
            items.append({"command": value, "description": description})
            return True

        entity = await resolve_dialog_entity(client, peer)
        if not bool(getattr(entity, "bot", False)):
            return {"commands": [], "is_bot": False, "source": "not_bot"}
        full = await client(GetFullUserRequest(entity))
        full_user = getattr(full, "full_user", None)
        bot_info = getattr(full_user, "bot_info", None) or getattr(full, "bot_info", None)
        commands = getattr(bot_info, "commands", None) or []
        official = [
            {
                "command": f"/{str(getattr(command, 'command', '')).lstrip('/')}",
                "description": str(getattr(command, "description", "") or ""),
            }
            for command in commands
            if str(getattr(command, "command", "") or "").strip()
        ]
        seen = {item["command"] for item in official}
        source = "official" if official else "none"
        suggestions = official[:]
        messages = await client.get_messages(entity, limit=80)

        if len(suggestions) < 16:
            for message in messages:
                rows = getattr(message, "buttons", None) or []
                for row in rows:
                    buttons = row if isinstance(row, list) else [row]
                    for button in buttons:
                        text = str(getattr(button, "text", "") or "")
                        if add_suggestion(suggestions, seen, text, "键盘按钮") and source == "none":
                            source = "keyboard"
                        if len(suggestions) >= 16:
                            break
                    if len(suggestions) >= 16:
                        break
                if len(suggestions) >= 16:
                    break

        if len(suggestions) < 8:
            for message in messages:
                for command in COMMAND_RE.findall(getattr(message, "message", "") or ""):
                    if add_suggestion(suggestions, seen, command, "历史消息") and source == "none":
                        source = "history"
                    if len(suggestions) >= 16:
                        break
                if len(suggestions) >= 16:
                    break
        return {"commands": suggestions, "is_bot": True, "source": source}
    finally:
        await client.disconnect()


async def send_chat_message(
    session: str,
    api_id: int,
    api_hash: str,
    peer: str,
    text: str,
    proxy=None,
) -> dict[str, Any]:
    client = TelegramClient(StringSession(session), api_id, api_hash, proxy=proxy)
    await client.connect()
    try:
        entity = await resolve_dialog_entity(client, peer)
        message = await client.send_message(entity, text)
        return {
            "id": str(message.id),
            "date": iso_date(getattr(message, "date", None)),
            "out": True,
            "sender": "me",
            "text": getattr(message, "message", "") or text,
        }
    finally:
        await client.disconnect()


async def refresh_profiles(api_id: int, api_hash: str, alt_out: Path, main_out: Path, proxy=None) -> list[dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = {}
    for path, role in [(main_out, "main"), (alt_out, "alt")]:
        rows = []
        for session in read_lines(path):
            try:
                profile = await profile_from_session(session, api_id, api_hash, proxy)
                rows.append({"session": session, "role": role, **profile})
            except Exception as exc:
                rows.append({"session": session, "role": role, "name": f"{role} 账号", "error": str(exc)})
        save_account_cache(path, rows)
        cache.update({row["session"]: row for row in rows})
    return accounts_from_file(main_out, "main", cache) + accounts_from_file(alt_out, "alt", cache)


async def qr_login(job_id: str, api_id: int, api_hash: str, out: Path, role: str, timeout: int, proxy_url: str | None) -> None:
    proxy = parse_proxy(proxy_url)
    client = TelegramClient(StringSession(), api_id, api_hash, proxy=proxy)
    await client.connect()
    try:
        qr = await client.qr_login()
        with jobs_lock:
            login_jobs[job_id].status = "waiting"
            login_jobs[job_id].url = qr.url
            login_jobs[job_id].qr_image = make_qr_data_url(qr.url)
        try:
            await qr.wait(timeout=timeout)
        except errors.SessionPasswordNeededError:
            with jobs_lock:
                login_jobs[job_id].status = "needs_password"
                login_jobs[job_id].error = "该账号开启了二步验证，请使用命令行导入。"
            return

        session = client.session.save()
        if not session:
            raise RuntimeError("未能生成 session string")
        append_session(out, session)
        fallback_profile = {
            "telegram_id": "",
            "username": "",
            "name": "本地主账号" if role == "main" else "本地小号",
            "avatar": "",
        }
        with jobs_lock:
            login_jobs[job_id].status = "done"
            login_jobs[job_id].account = {"role": role, **fallback_profile, "masked_session": masked_session(session)}
        try:
            profile = await profile_from_session(session, api_id, api_hash, proxy)
        except Exception:
            profile = fallback_profile
        cache = load_account_cache(out)
        cache[session] = {"session": session, "role": role, **profile}
        save_account_cache(out, list(cache.values()))
        with jobs_lock:
            login_jobs[job_id].status = "done"
            login_jobs[job_id].account = {"role": role, **profile, "masked_session": masked_session(session)}
    except Exception as exc:
        with jobs_lock:
            login_jobs[job_id].status = "error"
            login_jobs[job_id].error = f"{type(exc).__name__}: {exc}"
    finally:
        await client.disconnect()


def flow_to_signins(flow: dict[str, Any]) -> dict[str, Any]:
    nodes = flow.get("nodes", [])
    edges = flow.get("edges", [])
    by_id = {str(node.get("id")): node for node in nodes}
    next_map: dict[str, str] = {}
    for edge in edges:
        source = str(edge.get("source"))
        target = str(edge.get("target"))
        if source and target:
            next_map[source] = target

    start = next((node for node in nodes if (node.get("data") or {}).get("kind") == "task"), nodes[0] if nodes else None)
    job_id = str(start.get("data", {}).get("taskId") or flow.get("id") or "web-flow") if start else "web-flow"
    actions: list[dict[str, Any]] = []
    peer = ""
    collect_limit = 5
    include_contains: list[str] = []
    forward: dict[str, Any] = {"enabled": False}

    current = str(start.get("id")) if start else ""
    visited: set[str] = set()
    while current and current not in visited:
        visited.add(current)
        node = by_id.get(current, {})
        data = node.get("data", {}) or {}
        node_type = data.get("kind") or node.get("type")
        if node_type == "open":
            peer = str(data.get("peer") or peer)
            actions.append({"type": "open", "peer": peer, "regex": bool(data.get("regex"))})
        elif node_type == "send":
            text = str(data.get("command") or "") if str(data.get("sendMode") or "") == "command" else str(data.get("text") or "")
            actions.append({"type": "send", "text": text})
        elif node_type == "parse":
            collect_limit = int(data.get("limit") or collect_limit)
            pattern = str(data.get("pattern") or "")
            if pattern:
                include_contains.append(pattern)
            actions.append({"type": "parse", "pattern": pattern, "regex": bool(data.get("regex")), "save_as": str(data.get("saveAs") or "last_parse")})
        elif node_type == "forward":
            forward = {
                "enabled": True,
                "mode": "user_forward",
                "to_peer": str(data.get("toPeer") or ""),
                "when": ["matched", "failure"],
                "include": {"contains": include_contains or ["签到"], "regex": []},
                "exclude": {"contains": [], "regex": []},
            }
            actions.append({"type": "forward", "to_peer": str(data.get("toPeer") or ""), "source": str(data.get("source") or "last_parse")})
        elif node_type == "link":
            actions.append({"type": "open_link", "url": str(data.get("url") or "")})
        current = next_map.get(current, "")

    return {
        "defaults": {
            "api_id_secret": "TG_API_ID",
            "api_hash_secret": "TG_API_HASH",
            "accounts_secret": "TG_SESSION_STRINGS",
            "proxy_secret": "TG_PROXY",
            "forward": {
                "enabled": True,
                "mode": "notify",
                "when": ["failure", "matched"],
                "bot_token_secret": "TG_FORWARD_BOT_TOKEN",
                "chat_id_secret": "TG_FORWARD_CHAT_ID",
                "include": {"contains": include_contains or ["签到成功", "积分"], "regex": []},
                "exclude": {"contains": ["广告"], "regex": []},
                "max_messages": collect_limit,
            },
        },
        "jobs": [
            {
                "id": job_id,
                "enabled": True,
                "peer": peer or "@example_bot",
                "accounts_secret": "TG_SESSION_STRINGS",
                "actions": actions or [{"type": "send", "text": "签到"}, {"type": "wait", "seconds": 5}],
                "collect": {"last_messages": collect_limit},
                "forward": forward,
            }
        ],
    }


def flow_to_tasks(flow: dict[str, Any]) -> dict[str, Any]:
    job_id = str(flow.get("id") or "web-flow")
    return {
        "timezone": "Asia/Shanghai",
        "tasks": [
            {
                "id": f"tg-sign-{job_id}",
                "enabled": True,
                "schedule": ["daily:00:15"],
                "command": "uv run python scripts/sign_from_config.py run-enabled --mail",
                "timeout_minutes": 30,
            }
        ],
    }


def read_workflows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def write_workflows(path: Path, workflows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(workflows, ensure_ascii=False, indent=2), encoding="utf-8")


def save_workflow(path: Path, flow: dict[str, Any]) -> dict[str, Any]:
    workflows = read_workflows(path)
    flow_id = str(flow.get("id") or uuid.uuid4().hex[:10])
    flow["id"] = flow_id
    flow["updated_at"] = int(time.time())
    workflows = [item for item in workflows if str(item.get("id")) != flow_id]
    workflows.append(flow)
    write_workflows(path, workflows)
    return flow


def ordered_node_ids(flow: dict[str, Any]) -> list[str]:
    nodes = flow.get("nodes", [])
    edges = flow.get("edges", [])
    by_id = {str(node.get("id")): node for node in nodes}
    next_map = {str(edge.get("source")): str(edge.get("target")) for edge in edges if edge.get("source") and edge.get("target")}
    start = next((node for node in nodes if (node.get("data") or {}).get("kind") == "task"), nodes[0] if nodes else None)
    current = str(start.get("id")) if start else ""
    ordered: list[str] = []
    visited: set[str] = set()
    while current and current not in visited and current in by_id:
        visited.add(current)
        ordered.append(current)
        current = next_map.get(current, "")
    return ordered


def run_flow(job_id: str, flow: dict[str, Any], alt_out: Path, account_limit: int | None = None, proxy: str | None = None) -> None:
    ordered = ordered_node_ids(flow)
    with jobs_lock:
        run_jobs[job_id].active_node = ordered[0] if ordered else None
    notify_run(job_id)
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "signins.yml"
        config_path.write_text(yaml.safe_dump(flow_to_signins(flow), allow_unicode=True, sort_keys=False), encoding="utf-8")
        env = os.environ.copy()
        sessions = read_lines(alt_out)
        if account_limit and account_limit > 0:
            sessions = sessions[:account_limit]
        env["TG_SESSION_STRINGS"] = "\n".join(sessions)
        if proxy:
            env["TG_PROXY"] = proxy
        command = ["uv", "run", "python", "scripts/sign_from_config.py", "--config", str(config_path), "run-enabled"]
        try:
            with jobs_lock:
                run_jobs[job_id].output += f"$ {' '.join(command)}\n"
                run_jobs[job_id].output += f"accounts: {len(sessions)}\n"
                run_jobs[job_id].output += f"proxy: {proxy or '(none)'}\n"
            notify_run(job_id)
            proc = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                with jobs_lock:
                    run_jobs[job_id].output += line
                    if ordered:
                        done_count = min(len(ordered), max(1, len(run_jobs[job_id].output.splitlines()) // 8))
                        run_jobs[job_id].completed_nodes = ordered[: max(0, done_count - 1)]
                        run_jobs[job_id].active_node = ordered[min(done_count - 1, len(ordered) - 1)]
                notify_run(job_id)
            try:
                return_code = proc.wait(timeout=3600)
            except subprocess.TimeoutExpired:
                proc.kill()
                return_code = 124
            with jobs_lock:
                run_jobs[job_id].status = "done" if return_code == 0 else "error"
                run_jobs[job_id].completed_nodes = ordered if return_code == 0 else run_jobs[job_id].completed_nodes
                failed_node = run_jobs[job_id].active_node or (ordered[0] if ordered else None)
                run_jobs[job_id].active_node = None
                run_jobs[job_id].failed_node = None if return_code == 0 else failed_node
                run_jobs[job_id].error = None if return_code == 0 else f"exit code {return_code}"
                if return_code != 0:
                    run_jobs[job_id].output += f"\nexit code {return_code}\n"
            notify_run(job_id)
        except Exception:
            with jobs_lock:
                run_jobs[job_id].status = "error"
                run_jobs[job_id].active_node = None
                run_jobs[job_id].failed_node = ordered[0] if ordered else None
                run_jobs[job_id].error = traceback.format_exc()
                run_jobs[job_id].output += "\n" + run_jobs[job_id].error
            notify_run(job_id)


def notify_run(job_id: str) -> None:
    condition = run_events.get(job_id)
    if condition:
        with condition:
            condition.notify_all()


class Handler(BaseHTTPRequestHandler):
    api_id = DEFAULT_API_ID
    api_hash = DEFAULT_API_HASH
    alt_out = DEFAULT_ALT_OUT
    main_out = DEFAULT_MAIN_OUT
    workflows = DEFAULT_WORKFLOWS
    timeout = 180

    def log_message(self, format: str, *args: Any) -> None:
        if self.path.startswith("/api/run/status") or self.path.startswith("/api/run/stream"):
            return
        print(f"[local-web] {self.address_string()} {format % args}")

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        return json.loads(raw or "{}")

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_sse(self, job_id: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        condition = run_events.setdefault(job_id, threading.Condition())
        last_payload = ""
        try:
            while True:
                with jobs_lock:
                    job = run_jobs.get(job_id)
                    payload = json.dumps(asdict(job) if job else {"status": "not_found"}, ensure_ascii=False)
                    terminal = (job is None) or job.status in {"done", "error"}
                if payload != last_payload:
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    last_payload = payload
                if terminal:
                    break
                with condition:
                    condition.wait(timeout=1.5)
        except BrokenPipeError:
            return

    def do_OPTIONS(self) -> None:
        self.send_json({})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if parsed.path == "/api/accounts":
            cache = load_account_cache(self.alt_out, self.main_out)
            accounts = accounts_from_file(self.main_out, "main", cache) + accounts_from_file(self.alt_out, "alt", cache)
            self.send_json({"accounts": accounts, "alt_file": str(self.alt_out), "main_file": str(self.main_out)})
            return
        if parsed.path == "/api/accounts/refresh":
            proxy = params.get("proxy", [""])[0] or os.environ.get("TG_PROXY")
            accounts = asyncio.run(refresh_profiles(self.api_id, self.api_hash, self.alt_out, self.main_out, parse_proxy(proxy)))
            self.send_json({"accounts": accounts})
            return
        if parsed.path == "/api/qr/status":
            job_id = params.get("id", [""])[0]
            with jobs_lock:
                job = login_jobs.get(job_id)
                payload = asdict(job) if job else {"status": "not_found"}
            self.send_json(payload)
            return
        if parsed.path == "/api/workflows":
            self.send_json({"workflows": read_workflows(self.workflows)})
            return
        if parsed.path == "/api/dialogs":
            account_id = params.get("account", [""])[0]
            proxy = params.get("proxy", [""])[0] or os.environ.get("TG_PROXY")
            limit = int(params.get("limit", ["40"])[0] or "40")
            query = params.get("q", [""])[0]
            try:
                session = session_for_account(account_id, self.alt_out, self.main_out)
                dialogs = asyncio.run(list_dialogs(session, self.api_id, self.api_hash, parse_proxy(proxy), limit, query))
                self.send_json({"dialogs": dialogs})
            except Exception as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/messages":
            account_id = params.get("account", [""])[0]
            peer = params.get("peer", [""])[0]
            proxy = params.get("proxy", [""])[0] or os.environ.get("TG_PROXY")
            limit = int(params.get("limit", ["40"])[0] or "40")
            try:
                if not peer:
                    raise RuntimeError("缺少 peer")
                session = session_for_account(account_id, self.alt_out, self.main_out)
                messages = asyncio.run(list_messages(session, self.api_id, self.api_hash, peer, parse_proxy(proxy), limit))
                self.send_json({"messages": messages})
            except Exception as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/bot/commands":
            account_id = params.get("account", [""])[0]
            peer = params.get("peer", [""])[0]
            proxy = params.get("proxy", [""])[0] or os.environ.get("TG_PROXY")
            try:
                if not peer:
                    raise RuntimeError("缺少 peer")
                session = session_for_account(account_id, self.alt_out, self.main_out)
                payload = asyncio.run(list_bot_commands(session, self.api_id, self.api_hash, peer, parse_proxy(proxy)))
                self.send_json(payload)
            except Exception as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/run/status":
            job_id = params.get("id", [""])[0]
            with jobs_lock:
                job = run_jobs.get(job_id)
                payload = asdict(job) if job else {"status": "not_found"}
            self.send_json(payload)
            return
        if parsed.path == "/api/run/stream":
            job_id = params.get("id", [""])[0]
            self.send_sse(job_id)
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        body = self.read_json()
        if parsed.path == "/api/qr/start":
            role = str(body.get("role") or "alt")
            proxy = str(body.get("proxy") or os.environ.get("TG_PROXY") or "")
            out = self.main_out if role == "main" else self.alt_out
            job_id = uuid.uuid4().hex
            with jobs_lock:
                login_jobs[job_id] = LoginJob(id=job_id, role=role)
            threading.Thread(
                target=lambda: asyncio.run(qr_login(job_id, self.api_id, self.api_hash, out, role, self.timeout, proxy)),
                daemon=True,
            ).start()
            self.send_json({"id": job_id, "status": "pending"})
            return
        if parsed.path == "/api/workflows":
            flow = save_workflow(self.workflows, body)
            self.send_json({"workflow": flow})
            return
        if parsed.path == "/api/workflows/compile":
            signins = yaml.safe_dump(flow_to_signins(body), allow_unicode=True, sort_keys=False)
            tasks = yaml.safe_dump(flow_to_tasks(body), allow_unicode=True, sort_keys=False)
            self.send_json({"signins": signins, "tasks": tasks})
            return
        if parsed.path == "/api/run/start":
            flow = body.get("workflow") or body
            account_limit_raw = body.get("account_limit")
            account_limit = int(account_limit_raw) if account_limit_raw else None
            proxy = str(body.get("proxy") or os.environ.get("TG_PROXY") or "")
            job_id = uuid.uuid4().hex
            with jobs_lock:
                run_jobs[job_id] = RunJob(id=job_id)
            threading.Thread(target=lambda: run_flow(job_id, flow, self.alt_out, account_limit, proxy), daemon=True).start()
            self.send_json({"id": job_id, "status": "running"})
            return
        if parsed.path == "/api/messages/send":
            account_id = str(body.get("account") or "")
            peer = str(body.get("peer") or "")
            text = str(body.get("text") or "")
            proxy = str(body.get("proxy") or os.environ.get("TG_PROXY") or "")
            try:
                if not peer:
                    raise RuntimeError("缺少 peer")
                if not text.strip():
                    raise RuntimeError("消息内容为空")
                session = session_for_account(account_id, self.alt_out, self.main_out)
                message = asyncio.run(send_chat_message(session, self.api_id, self.api_hash, peer, text, parse_proxy(proxy)))
                self.send_json({"message": message})
            except Exception as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local TG web API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--out", default=str(DEFAULT_ALT_OUT))
    parser.add_argument("--main-out", default=str(DEFAULT_MAIN_OUT))
    parser.add_argument("--workflows", default=str(DEFAULT_WORKFLOWS))
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--api-id")
    parser.add_argument("--api-hash")
    args = parser.parse_args()

    Handler.api_id = resolve_api_id(args.api_id)
    Handler.api_hash = resolve_api_hash(args.api_hash)
    Handler.alt_out = Path(args.out).expanduser().resolve()
    Handler.main_out = Path(args.main_out).expanduser().resolve()
    Handler.workflows = Path(args.workflows).expanduser().resolve()
    Handler.timeout = args.timeout

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"TG local web API: http://{args.host}:{args.port}")
    print(f"Alt session pool: {Handler.alt_out}")
    print(f"Main session: {Handler.main_out}")
    print(f"Workflows: {Handler.workflows}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
