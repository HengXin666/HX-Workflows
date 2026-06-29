#!/usr/bin/env python3
"""Interactively enroll Telegram accounts by QR login.

Each successful login appends one StringSession to sessions/tg_session_strings.txt.
Run locally only. The output file is a credential store and must not be shared.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import urllib.parse
from pathlib import Path

import socks
from telethon import TelegramClient, errors
from telethon.sessions import StringSession

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "sessions" / "tg_session_strings.txt"
DEFAULT_API_ID = 611335
DEFAULT_API_HASH = "d524b414d21f4d37f08684c1df41ac9c"


def resolve_api_id(api_id: str | None) -> int:
    return int(api_id or os.environ.get("TG_API_ID") or DEFAULT_API_ID)


def resolve_api_hash(api_hash: str | None) -> str:
    return api_hash or os.environ.get("TG_API_HASH") or DEFAULT_API_HASH


def parse_proxy(proxy_url: str | None):
    if not proxy_url:
        return None
    parsed = urllib.parse.urlparse(proxy_url)
    scheme = parsed.scheme.lower()
    proxy_types = {
        "http": socks.HTTP,
        "socks4": socks.SOCKS4,
        "socks5": socks.SOCKS5,
    }
    if scheme not in proxy_types:
        raise SystemExit(f"不支持的代理协议: {scheme}")
    if not parsed.hostname or not parsed.port:
        raise SystemExit("代理地址必须包含 host 和 port")
    return (
        proxy_types[scheme],
        parsed.hostname,
        parsed.port,
        True,
        urllib.parse.unquote(parsed.username) if parsed.username else None,
        urllib.parse.unquote(parsed.password) if parsed.password else None,
    )


def print_qr(url: str) -> None:
    try:
        import qrcode
    except Exception:
        print("未安装 qrcode，复制下面链接到 Telegram 打开：")
        print(url)
        return

    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    print("如果终端二维码无法扫描，也可以复制下面链接到 Telegram：")
    print(url)


def read_existing(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def append_session(path: Path, session: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(session + "\n")


async def qr_login(api_id: int, api_hash: str, proxy, timeout: int) -> tuple[str, str]:
    client = TelegramClient(StringSession(), api_id, api_hash, proxy=proxy)
    await client.connect()
    try:
        qr = await client.qr_login()
        print_qr(qr.url)
        print("手机 Telegram: Settings -> Devices -> Link Desktop Device")
        try:
            await qr.wait(timeout=timeout)
        except errors.SessionPasswordNeededError:
            password = getpass.getpass("此账号开启了二步验证，请输入 2FA 密码: ")
            await client.sign_in(password=password)

        me = await client.get_me()
        username = f"@{me.username}" if getattr(me, "username", None) else "(no username)"
        label = f"id={me.id} username={username}"
        session = client.session.save()
        if not session:
            raise RuntimeError("未能生成 session string")
        return session, label
    finally:
        await client.disconnect()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Interactively enroll Telegram accounts by QR login")
    parser.add_argument("-o", "--out", default=str(DEFAULT_OUT), help="凭证输出文件")
    parser.add_argument("--timeout", type=int, default=180, help="每个二维码等待秒数")
    parser.add_argument("--proxy", default=os.environ.get("TG_PROXY"), help="代理，例如 http://127.0.0.1:2334")
    parser.add_argument("--api-id", help="可选：自定义 Telegram API ID")
    parser.add_argument("--api-hash", help="可选：自定义 Telegram API Hash")
    args = parser.parse_args()

    api_id = resolve_api_id(args.api_id)
    api_hash = resolve_api_hash(args.api_hash)
    proxy = parse_proxy(args.proxy)
    out = Path(args.out).expanduser().resolve()
    existing = read_existing(out)

    print(f"凭证文件: {out}")
    print(f"已有账号凭证: {len(existing)} 行")
    if args.proxy:
        print(f"使用代理: {args.proxy}")

    enrolled = 0
    while True:
        answer = input("\n按 Enter 添加一个账号，输入 q 退出: ").strip().lower()
        if answer == "q":
            break
        try:
            session, label = await qr_login(api_id, api_hash, proxy, args.timeout)
        except Exception as exc:
            print(f"登录失败: {type(exc).__name__}: {exc}")
            continue

        if session in existing:
            print(f"账号已存在，跳过写入: {label}")
            continue
        append_session(out, session)
        existing.add(session)
        enrolled += 1
        print(f"已添加账号 #{len(existing)}: {label}")

    print(f"\n本次新增: {enrolled}")
    print(f"凭证总数: {len(existing)}")
    print("本地运行时使用：")
    print(f'TG_PROXY="{args.proxy or ""}" TG_SESSION_STRINGS="$(cat {out})" uv run python scripts/sign_from_config.py list')
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
