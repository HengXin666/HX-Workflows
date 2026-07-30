#!/usr/bin/env python3
"""Long-polling runtime for the native check-in component."""

from __future__ import annotations
import argparse
import time
from pathlib import Path
from bot_checkin import CheckinStore, challenge_message
from local_web_bots import DEFAULT_BOTS, _bot_token, call_bot_api


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot-id", required=True)
    parser.add_argument("--db", default="sessions/bot_checkin.sqlite3")
    args = parser.parse_args()
    token = _bot_token(DEFAULT_BOTS, args.bot_id)
    store = CheckinStore(Path(args.db))
    offset = 0
    try:
        while True:
            for update in call_bot_api(
                token,
                "getUpdates",
                {"offset": offset, "timeout": 25, "allowed_updates": ["message", "callback_query"]},
                timeout=35,
            ):
                offset = int(update["update_id"]) + 1
                message = update.get("message") or {}
                callback = update.get("callback_query") or {}
                user = message.get("from") or callback.get("from") or {}
                chat = message.get("chat") or (callback.get("message") or {}).get("chat") or {}
                if not user or not chat:
                    continue
                store.upsert_user(int(user["id"]), user.get("username", ""), user.get("first_name", ""))
                if str(message.get("text", "")).split("@")[0] in {"/checkin", "/签到"}:
                    challenge = store.create_challenge(int(user["id"]), int(chat["id"]))
                    call_bot_api(token, "sendMessage", {"chat_id": chat["id"], **challenge_message(challenge)})
                data = str(callback.get("data", ""))
                if data.startswith("checkin:"):
                    _, challenge_id, choice = data.split(":", 2)
                    state = store.verify(challenge_id, int(user["id"]), int(choice))
                    call_bot_api(
                        token,
                        "answerCallbackQuery",
                        {
                            "callback_query_id": callback["id"],
                            "text": {
                                "success": "签到成功",
                                "already": "今天已经签到",
                                "wrong": "答案错误，已生成新题",
                                "expired": "题目已过期",
                            }[state],
                        },
                    )
                    if state in {"wrong", "expired"}:
                        challenge = store.create_challenge(int(user["id"]), int(chat["id"]))
                        call_bot_api(token, "sendMessage", {"chat_id": chat["id"], **challenge_message(challenge)})
            time.sleep(0.2)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
