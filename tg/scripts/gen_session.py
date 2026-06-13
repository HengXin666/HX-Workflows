#!/usr/bin/env python3
"""Local helper: generate a Telethon StringSession.

Run this on your own machine, not in GitHub Actions.
Never paste the generated session string into chat or commit it to GitHub.
"""

from __future__ import annotations

import asyncio
import getpass
import os

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> int:
    api_id_raw = os.environ.get("TG_API_ID") or input("TG_API_ID: ").strip()
    api_hash = os.environ.get("TG_API_HASH") or getpass.getpass("TG_API_HASH: ").strip()
    phone = os.environ.get("TG_PHONE") or input("Phone, for example +819012345678: ").strip()

    api_id = int(api_id_raw)
    client = TelegramClient(StringSession(), api_id, api_hash)

    async with client:
        await client.start(phone=phone)
        session = client.session.save()

    print("\n=== COPY THIS SESSION STRING TO GITHUB SECRET TG_SESSION_STRINGS ===")
    print(session)
    print("=== END ===\n")
    print("提示：多账号时重复运行本脚本，把每个 session string 各占一行放入 TG_SESSION_STRINGS。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
