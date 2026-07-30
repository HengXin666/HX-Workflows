"""Account and job orchestration for configured TG signing workflows."""

from __future__ import annotations

import sys
import traceback
from asyncio import Semaphore, gather
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.sessions import StringSession

from signer_actions import collect_messages, handle_forward, run_actions
from signer_core import (
    RunResult,
    deep_merge,
    message_text,
    optional_env,
    parse_proxy,
    read_secret_lines,
    required_env,
    should_forward,
    telegram_api_config,
    text_matches,
)
from signer_reporting import (
    bot_api_notify,
    build_summary,
    send_report_with_emall,
    write_report,
)

MAX_ACCOUNT_CONCURRENCY = 8


def account_concurrency(defaults: dict[str, Any], selected: dict[str, Any]) -> int:
    raw = selected.get("account_concurrency") or defaults.get("account_concurrency") or optional_env("TG_ACCOUNT_CONCURRENCY") or "1"
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise RuntimeError("account_concurrency 必须是整数") from None
    if not 1 <= value <= MAX_ACCOUNT_CONCURRENCY:
        raise RuntimeError(f"account_concurrency 必须在 1..{MAX_ACCOUNT_CONCURRENCY} 之间")
    return value


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
    concurrency = account_concurrency(defaults, selected)

    failures = 0
    semaphore = Semaphore(concurrency)

    async def run_indexed(index: int, session: str) -> bool:
        async with semaphore:
            try:
                await run_one_account(selected, defaults, session, index)
                return True
            except Exception as exc:
                print(f"账号 #{index} 执行失败: {exc}", file=sys.stderr)
                return False

    results = await gather(*(run_indexed(index, session) for index, session in enumerate(sessions, start=1)))
    failures = results.count(False)

    if failures:
        print(f"任务 {job_id} 完成，但有 {failures}/{len(sessions)} 个账号失败。")
        return 1
    print(f"任务 {job_id} 全部账号执行成功。")
    return 0


async def run_account_collect(
    selected: dict[str, Any],
    defaults: dict[str, Any],
    job_id: str,
    index: int,
    session: str,
    semaphore: Semaphore,
) -> RunResult:
    async with semaphore:
        try:
            return await run_one_account(selected, defaults, session, index)
        except Exception as exc:
            print(f"账号 #{index} 执行失败: {exc}", file=sys.stderr)
            return RunResult(
                job_id=job_id,
                account_index=index,
                status="failure",
                matched=False,
                summary=str(exc),
                messages=[],
                node_logs={},
            )


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
    concurrency = account_concurrency(defaults, selected)

    semaphore = Semaphore(concurrency)
    results = await gather(
        *(
            run_account_collect(selected, defaults, job_id, index, session, semaphore)
            for index, session in enumerate(sessions, start=1)
        )
    )
    failures = sum(1 for result in results if result.status == "failure")

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
