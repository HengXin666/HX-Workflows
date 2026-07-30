"""Telegram read/write adapters used by the local web API."""

from __future__ import annotations

import asyncio
import base64
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
COMMAND_REQUEST_TIMEOUT = 12.0
COMMAND_SOURCE_TIMEOUT = 5.0
COMMAND_DISCONNECT_TIMEOUT = 1.0
MAX_MEDIA_DOWNLOAD_BYTES = 256 * 1024 * 1024
dialog_avatar_cache: dict[str, str] = {}


async def _dialog_avatar(client: TelegramClient, entity: Any, is_group: bool, semaphore: asyncio.Semaphore) -> str:
    if not is_group or not getattr(entity, "photo", None):
        return ""
    photo = getattr(entity, "photo", None)
    cache_key = f"{getattr(entity, 'id', '')}:{getattr(photo, 'photo_id', '')}"
    if cache_key in dialog_avatar_cache:
        return dialog_avatar_cache[cache_key]
    try:
        async with semaphore:
            content = await client.download_profile_photo(entity, file=bytes, thumb=-1)
    except Exception:
        return ""
    if not content:
        return ""
    avatar = "data:image/jpeg;base64," + base64.b64encode(content).decode("ascii")
    dialog_avatar_cache[cache_key] = avatar
    return avatar


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
        rows: list[tuple[dict[str, Any], Any, bool]] = []
        needle = query.casefold().strip()
        for dialog in dialogs:
            entity = dialog.entity
            username = getattr(entity, "username", "") or ""
            title = dialog.name or username or str(dialog.id)
            if needle and needle not in title.casefold() and needle not in username.casefold():
                continue
            message = dialog.message
            is_group = bool(getattr(dialog, "is_group", False))
            rows.append(
                (
                    {
                        "id": str(dialog.id),
                        "title": title,
                        "username": f"@{username}" if username else "",
                        "type": entity.__class__.__name__,
                        "is_group": is_group,
                        "avatar": "",
                        "unread_count": int(getattr(dialog, "unread_count", 0) or 0),
                        "date": iso_date(getattr(message, "date", None)),
                        "last_message": getattr(message, "message", "") if message else "",
                    },
                    entity,
                    is_group,
                )
            )
        semaphore = asyncio.Semaphore(6)
        avatars = await asyncio.gather(
            *(_dialog_avatar(client, entity, is_group, semaphore) for _, entity, is_group in rows)
        )
        items: list[dict[str, Any]] = []
        for (item, _entity, _is_group), avatar in zip(rows, avatars, strict=True):
            item["avatar"] = avatar
            items.append(item)
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
    anchor_message_id: int | None = None,
    message_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    client = TelegramClient(StringSession(session), api_id, api_hash, proxy=proxy)
    await client.connect()
    try:
        entity = await resolve_dialog_entity(client, peer)
        if anchor_message_id is not None and anchor_message_id < 1:
            raise ValueError("anchor_message_id 必须是正整数")
        if message_ids:
            if len(message_ids) > 20 or any(message_id < 1 for message_id in message_ids):
                raise ValueError("message_ids 必须包含不超过 20 个正整数")
            requested = await client.get_messages(entity, ids=message_ids)
            messages = [message for message in requested if message is not None]
        else:
            messages = await client.get_messages(
                entity,
                limit=limit,
                offset_id=(anchor_message_id + 1) if anchor_message_id is not None else 0,
            )
        rows: list[dict[str, Any]] = []
        for message in sorted(messages, key=lambda item: int(getattr(item, "id", 0) or 0)):
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
            rows.append(
                {
                    "id": str(message.id),
                    "date": iso_date(getattr(message, "date", None)),
                    "out": bool(getattr(message, "out", False)),
                    "sender": sender_name,
                    "text": text,
                    "media": message_media_info(message),
                }
            )
        return rows
    finally:
        await client.disconnect()


def message_media_info(message: Any) -> dict[str, Any] | None:
    if not getattr(message, "media", None):
        return None
    file = getattr(message, "file", None)
    mime_type = str(getattr(file, "mime_type", "") or "")
    document = getattr(message, "document", None)
    if getattr(message, "photo", None) or mime_type.startswith("image/"):
        kind = "image"
    elif (
        getattr(message, "video", None)
        or getattr(message, "video_note", None)
        or mime_type.startswith("video/")
    ):
        kind = "video"
    else:
        kind = "file"
    photo_sizes = getattr(getattr(message, "photo", None), "sizes", None) or []
    document_thumbs = getattr(document, "thumbs", None) or []
    result = {
        "kind": kind,
        "mime_type": mime_type or ("image/jpeg" if kind == "image" else "application/octet-stream"),
        "file_name": str(getattr(file, "name", "") or ""),
        "size": int(getattr(file, "size", 0) or 0),
        "width": int(getattr(file, "width", 0) or 0),
        "height": int(getattr(file, "height", 0) or 0),
        "duration": int(getattr(file, "duration", 0) or 0),
        "has_thumbnail": bool(photo_sizes or document_thumbs),
    }
    media_object = getattr(message, "photo", None) or document
    media_id = int(getattr(media_object, "id", 0) or 0)
    if media_id > 0:
        dc_id = int(getattr(media_object, "dc_id", 0) or 0)
        result["media_id"] = f"{kind}:{dc_id}:{media_id}"
    return result


def _thumbnail_size(message: Any) -> Any | None:
    sizes = (
        getattr(getattr(message, "photo", None), "sizes", None)
        or getattr(getattr(message, "document", None), "thumbs", None)
        or []
    )
    candidates = [
        item
        for item in sizes
        if int(getattr(item, "w", 0) or 0) > 0 and int(getattr(item, "h", 0) or 0) > 0
    ]
    if not candidates:
        return None
    within_preview = [
        item
        for item in candidates
        if max(int(getattr(item, "w", 0) or 0), int(getattr(item, "h", 0) or 0)) <= 360
    ]
    def size_key(item: Any) -> int:
        return int(getattr(item, "w", 0) or 0) * int(getattr(item, "h", 0) or 0)

    return max(within_preview, key=size_key) if within_preview else min(candidates, key=size_key)


async def download_message_media(
    session: str,
    api_id: int,
    api_hash: str,
    peer: str,
    message_id: int,
    *,
    thumbnail: bool,
    proxy=None,
) -> tuple[bytes, str]:
    if message_id < 1:
        raise ValueError("message_id 必须是正整数")
    client = TelegramClient(StringSession(session), api_id, api_hash, proxy=proxy)
    await client.connect()
    try:
        entity = await resolve_dialog_entity(client, peer)
        message = await client.get_messages(entity, ids=message_id)
        if isinstance(message, (list, tuple)):
            message = next((item for item in message if item is not None), None)
        if message is None or not getattr(message, "media", None):
            raise FileNotFoundError("消息不存在或不包含媒体")
        info = message_media_info(message)
        if info is None:
            raise FileNotFoundError("消息不包含可下载媒体")
        if thumbnail:
            thumb = _thumbnail_size(message)
            if thumb is None:
                raise FileNotFoundError("该媒体没有可用预览图")
            content = await client.download_media(message, file=bytes, thumb=thumb)
            content_type = "image/jpeg"
        else:
            if int(info["size"] or 0) > MAX_MEDIA_DOWNLOAD_BYTES:
                raise ValueError(f"媒体原文件不能超过 {MAX_MEDIA_DOWNLOAD_BYTES // (1024 * 1024)} MB")
            content = await client.download_media(message, file=bytes)
            content_type = str(info["mime_type"] or "application/octet-stream")
        if not isinstance(content, bytes) or not content:
            raise FileNotFoundError("媒体下载结果为空")
        return content, content_type
    finally:
        await client.disconnect()


async def _list_bot_commands(
    session: str,
    api_id: int,
    api_hash: str,
    peer: str,
    proxy=None,
) -> dict[str, Any]:
    client = TelegramClient(StringSession(session), api_id, api_hash, proxy=proxy)
    try:
        await asyncio.wait_for(client.connect(), timeout=COMMAND_REQUEST_TIMEOUT)

        def add_suggestion(items: list[dict[str, str]], seen: set[str], text: str, description: str) -> bool:
            value = text.strip()
            if not value or value in seen:
                return False
            seen.add(value)
            items.append({"command": value, "description": description})
            return True

        entity = await asyncio.wait_for(
            resolve_dialog_entity(client, peer),
            timeout=COMMAND_REQUEST_TIMEOUT,
        )
        if not bool(getattr(entity, "bot", False)):
            return {"commands": [], "is_bot": False, "source": "not_bot"}

        async def load_full_user() -> tuple[Any | None, str]:
            try:
                return (
                    await asyncio.wait_for(
                        client(GetFullUserRequest(entity)),
                        timeout=COMMAND_SOURCE_TIMEOUT,
                    ),
                    "",
                )
            except Exception as exc:
                return None, f"官方菜单: {type(exc).__name__}"

        async def load_recent_messages() -> tuple[list[Any], str]:
            try:
                return (
                    list(
                        await asyncio.wait_for(
                            client.get_messages(entity, limit=40),
                            timeout=COMMAND_SOURCE_TIMEOUT,
                        )
                    ),
                    "",
                )
            except Exception as exc:
                return [], f"最近消息: {type(exc).__name__}"

        (full, full_error), (messages, messages_error) = await asyncio.gather(
            load_full_user(),
            load_recent_messages(),
        )
        full_user = getattr(full, "full_user", None) if full is not None else None
        bot_info = (
            getattr(full_user, "bot_info", None)
            or (getattr(full, "bot_info", None) if full is not None else None)
        )
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
        warnings = [value for value in (full_error, messages_error) if value]
        return {
            "commands": suggestions,
            "is_bot": True,
            "source": source,
            "partial": bool(warnings),
            "warning": "；".join(warnings),
        }
    finally:
        try:
            await asyncio.wait_for(client.disconnect(), timeout=COMMAND_DISCONNECT_TIMEOUT)
        except Exception:
            pass


async def list_bot_commands(
    session: str,
    api_id: int,
    api_hash: str,
    peer: str,
    proxy=None,
) -> dict[str, Any]:
    return await asyncio.wait_for(
        _list_bot_commands(session, api_id, api_hash, peer, proxy),
        timeout=COMMAND_REQUEST_TIMEOUT,
    )


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
