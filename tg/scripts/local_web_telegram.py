"""Telegram read/write adapters used by the local web API."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.users import GetFullUserRequest

from local_web_accounts import (
    accounts_from_file,
    iso_date,
    profile_from_session,
    read_lines,
    save_account_cache,
)

COMMAND_RE = re.compile(r"(?<!\S)/[A-Za-z0-9_]{1,32}")

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
