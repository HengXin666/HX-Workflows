"""Telegram action interpreter for configured TG signing workflows."""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any

from telethon import TelegramClient, errors
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.types import Channel, Chat, User

from signer_core import (
    DEFAULT_RESOURCE_POOL_DB,
    TG_LINK_RE,
    RunResult,
    entity_label,
    init_resource_pool_db,
    listify,
    mark_resource_seen,
    media_fingerprint,
    media_kind,
    message_text,
    normalize_join_target,
    required_env,
    resolve_local_path,
    resource_seen,
    should_forward,
    text_matches,
)
from signer_reporting import bot_api_notify, build_summary

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

        if action_type == "open_link":
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
