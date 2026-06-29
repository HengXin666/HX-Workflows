#!/usr/bin/env python3
"""Local helper: create Telegram StringSession values by QR login.

Run this only on your own machine. The output file contains account credentials
and is ignored by git through tg/.gitignore.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
from pathlib import Path

from telethon import TelegramClient, errors
from telethon.sessions import StringSession

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "sessions" / "tg_session_strings.txt"
DEFAULT_API_ID = 611335
DEFAULT_API_HASH = "d524b414d21f4d37f08684c1df41ac9c"


def resolve_api_id(api_id: str | None) -> int:
    raw = api_id or os.environ.get("TG_API_ID") or str(DEFAULT_API_ID)
    return int(raw)


def resolve_api_hash(api_hash: str | None) -> str:
    return api_hash or os.environ.get("TG_API_HASH") or DEFAULT_API_HASH


def print_qr(url: str) -> None:
    try:
        import qrcode
    except Exception:
        print("未安装 qrcode，无法显示终端二维码。请复制下面的登录链接到 Telegram 打开：")
        print(url)
        return

    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    print("如果终端二维码无法扫描，也可以复制下面的登录链接到 Telegram：")
    print(url)


async def login_one(api_id: int, api_hash: str, timeout: int) -> str:
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    try:
        qr_login = await client.qr_login()
        print_qr(qr_login.url)
        print("用已登录的 Telegram 手机端扫描：Settings -> Devices -> Link Desktop Device")
        try:
            await qr_login.wait(timeout=timeout)
        except errors.SessionPasswordNeededError:
            password = getpass.getpass("此账号开启了二步验证，请输入 2FA 密码: ")
            await client.sign_in(password=password)

        me = await client.get_me()
        username = f"@{me.username}" if getattr(me, "username", None) else "(no username)"
        print(f"登录成功: id={me.id} username={username}")
        session = client.session.save()
        if not session:
            raise RuntimeError("未能生成 session string")
        return session
    finally:
        await client.disconnect()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Telegram session strings by QR login")
    parser.add_argument("-n", "--count", type=int, default=1, help="要扫码登录的账号数量")
    parser.add_argument("-o", "--out", default=str(DEFAULT_OUT), help="输出文件，默认写到 tg/sessions/")
    parser.add_argument("--timeout", type=int, default=180, help="每个二维码等待秒数")
    parser.add_argument("--append", action="store_true", help="追加到输出文件，而不是覆盖")
    parser.add_argument("--api-id", help="可选：自定义 Telegram API ID；默认复用 tg-signer 内置值")
    parser.add_argument("--api-hash", help="可选：自定义 Telegram API Hash；默认复用 tg-signer 内置值")
    args = parser.parse_args()

    if args.count <= 0:
        raise SystemExit("--count 必须大于 0")

    api_id = resolve_api_id(args.api_id)
    api_hash = resolve_api_hash(args.api_hash)
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    sessions: list[str] = []
    for index in range(1, args.count + 1):
        print(f"\n=== Account {index}/{args.count} ===")
        sessions.append(await login_one(api_id, api_hash, args.timeout))

    mode = "a" if args.append else "w"
    with out.open(mode, encoding="utf-8") as f:
        for session in sessions:
            f.write(session + "\n")

    print(f"\n已写入 {len(sessions)} 个 session string: {out}")
    print("本地测试可执行：")
    print(f"TG_SESSION_STRINGS=\"$(cat {out})\" uv run python scripts/sign_from_config.py list")
    print("上工作流时，把这个文件内容逐行复制到 GitHub Secret: TG_SESSION_STRINGS。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
