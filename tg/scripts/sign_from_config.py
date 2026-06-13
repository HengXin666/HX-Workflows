#!/usr/bin/env python3
"""Run Telegram sign-in jobs from a plaintext YAML config.

Sensitive values are read from GitHub Actions Secrets via environment variables.
The YAML config should only contain editable non-secret workflow configuration.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import traceback
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from telethon import TelegramClient
from telethon.sessions import StringSession

try:
    import socks  # type: ignore
except Exception:  # pragma: no cover - only needed when proxy is used
    socks = None

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "signins.yml"


@dataclass
class RunResult:
    job_id: str
    account_index: int
    status: str
    matched: bool
    summary: str
    messages: list[Any]


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


async def run_actions(client: TelegramClient, peer: str, actions: list[dict[str, Any]]) -> None:
    for action in actions:
        action_type = str(action.get("type", "")).strip().lower()
        if action_type == "send":
            text = str(action.get("text", ""))
            if not text:
                raise RuntimeError("send action 缺少 text")
            print(f"发送消息到 {peer}: {text}")
            await client.send_message(peer, text)

        elif action_type == "click":
            text = str(action.get("text", ""))
            if not text:
                raise RuntimeError("click action 缺少 text")
            search_limit = int(action.get("search_limit", 5))
            print(f"查找并点击按钮: {text}")
            messages = await client.get_messages(peer, limit=search_limit)
            clicked = False
            for msg in messages:
                try:
                    result = await msg.click(text=text)
                    clicked = True
                    print(f"已点击按钮: {text}")
                    break
                except Exception:
                    continue
            if not clicked:
                raise RuntimeError(f"没有找到可点击按钮: {text}")

        elif action_type == "wait":
            seconds = float(action.get("seconds", 1))
            print(f"等待 {seconds} 秒")
            await asyncio.sleep(seconds)

        else:
            raise RuntimeError(f"不支持的 action type: {action_type}")

        if action_type != "wait" and action.get("wait_seconds"):
            seconds = float(action.get("wait_seconds", 1))
            print(f"动作后等待 {seconds} 秒")
            await asyncio.sleep(seconds)


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
    api_id_secret = str(job.get("api_id_secret") or defaults.get("api_id_secret") or "TG_API_ID")
    api_hash_secret = str(job.get("api_hash_secret") or defaults.get("api_hash_secret") or "TG_API_HASH")
    proxy_secret = str(job.get("proxy_secret") or defaults.get("proxy_secret") or "TG_PROXY")

    api_id = int(required_env(api_id_secret))
    api_hash = required_env(api_hash_secret)
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

    print(f"开始任务 {job_id} / 账号 #{account_index} / peer={peer}")
    client = TelegramClient(StringSession(session), api_id, api_hash, proxy=proxy)

    messages: list[Any] = []
    try:
        async with client:
            await run_actions(client, peer, actions)
            messages = await collect_messages(client, peer, collect, job_forward)
            matched = any(text_matches(message_text(msg), job_forward) for msg in messages)
            result = RunResult(
                job_id=job_id,
                account_index=account_index,
                status="success",
                matched=matched,
                summary="任务执行成功。",
                messages=messages,
            )
            await handle_forward(client, job_forward, result)
            return result
    except Exception as exc:
        summary = "任务执行失败。\n" + "".join(traceback.format_exception_only(type(exc), exc)).strip()
        result = RunResult(
            job_id=job_id,
            account_index=account_index,
            status="failure",
            matched=False,
            summary=summary,
            messages=messages,
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

    args = parser.parse_args()
    config = load_yaml(Path(args.config).resolve())
    if args.cmd == "list":
        return asyncio.run(list_jobs(config))
    if args.cmd == "run":
        return asyncio.run(run_job(config, args.job_id))
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    raise SystemExit(main())
