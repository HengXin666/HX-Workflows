"""SQLite-backed native check-in component for Telegram bots."""

from __future__ import annotations

import random
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class Challenge:
    id: str
    question: str
    options: list[int]
    expires_at: int


class CheckinStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, username TEXT, display_name TEXT, points INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS checkins(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, day TEXT NOT NULL, points INTEGER NOT NULL, created_at INTEGER NOT NULL, UNIQUE(user_id, day));
        CREATE TABLE IF NOT EXISTS challenges(id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, answer INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS cooldowns(user_id INTEGER NOT NULL, key TEXT NOT NULL, until_at INTEGER NOT NULL, PRIMARY KEY(user_id, key));
        """)

    def close(self) -> None:
        self.db.close()

    def upsert_user(self, user_id: int, username: str = "", display_name: str = "", now: int | None = None) -> None:
        now = now or int(time.time())
        self.db.execute(
            "INSERT INTO users(user_id,username,display_name,created_at,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,display_name=excluded.display_name,updated_at=excluded.updated_at",
            (user_id, username, display_name, now, now),
        )
        self.db.commit()

    def create_challenge(
        self, user_id: int, chat_id: int, *, now: int | None = None, ttl: int = 180, difficulty: str = "normal", rng: random.Random | None = None
    ) -> Challenge:
        now, rng = now or int(time.time()), rng or random.Random()
        if difficulty == "easy":
            a, b, operator = rng.randint(1, 10), rng.randint(1, 10), "+"
        elif difficulty == "hard":
            a, b, operator = rng.randint(2, 12), rng.randint(2, 12), "×"
        else:
            a, b, operator = rng.randint(5, 40), rng.randint(1, 25), rng.choice(["+", "-"])
            if operator == "-" and b > a:
                a, b = b, a
        answer = a * b if operator == "×" else a - b if operator == "-" else a + b
        options = {answer}
        while len(options) < 4:
            options.add(max(0, answer + rng.choice([-7, -5, -3, -2, -1, 1, 2, 3, 5, 7])))
        choices = list(options)
        rng.shuffle(choices)
        challenge_id = uuid.uuid4().hex
        self.db.execute("DELETE FROM challenges WHERE user_id=?", (user_id,))
        self.db.execute(
            "INSERT INTO challenges(id,user_id,chat_id,answer,expires_at,created_at) VALUES(?,?,?,?,?,?)",
            (challenge_id, user_id, chat_id, answer, now + ttl, now),
        )
        self.db.commit()
        return Challenge(challenge_id, f"{a} {operator} {b} = ?", choices, now + ttl)

    def checked_in_today(self, user_id: int, now: int | None = None) -> bool:
        day = datetime.fromtimestamp(now or int(time.time())).strftime("%Y-%m-%d")
        return self.db.execute("SELECT 1 FROM checkins WHERE user_id=? AND day=?", (user_id, day)).fetchone() is not None

    def verify(
        self,
        challenge_id: str,
        user_id: int,
        choice: int,
        *,
        now: int | None = None,
        points: int = 1,
        cooldown: int = 86400,
    ) -> str:
        now = now or int(time.time())
        row = self.db.execute("SELECT * FROM challenges WHERE id=? AND user_id=?", (challenge_id, user_id)).fetchone()
        if not row or row["expires_at"] < now:
            return "expired"
        if choice != row["answer"]:
            self.db.execute("UPDATE challenges SET attempts=attempts+1 WHERE id=?", (challenge_id,))
            self.db.commit()
            return "wrong"
        day = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        try:
            self.db.execute(
                "INSERT INTO checkins(user_id,day,points,created_at) VALUES(?,?,?,?)", (user_id, day, points, now)
            )
        except sqlite3.IntegrityError:
            return "already"
        self.db.execute("UPDATE users SET points=points+?,updated_at=? WHERE user_id=?", (points, now, user_id))
        self.db.execute(
            "INSERT INTO cooldowns(user_id,key,until_at) VALUES(?,?,?) ON CONFLICT(user_id,key) DO UPDATE SET until_at=excluded.until_at",
            (user_id, "checkin", now + cooldown),
        )
        self.db.execute("DELETE FROM challenges WHERE id=?", (challenge_id,))
        self.db.commit()
        return "success"

    def user_stats(self, user_id: int) -> dict[str, int]:
        row = self.db.execute(
            "SELECT points,(SELECT COUNT(*) FROM checkins WHERE user_id=?) AS days FROM users WHERE user_id=?",
            (user_id, user_id),
        ).fetchone()
        return {"points": int(row["points"]), "days": int(row["days"])} if row else {"points": 0, "days": 0}


def challenge_message(challenge: Challenge) -> dict:
    return {
        "text": f"签到验证：{challenge.question}",
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": str(value), "callback_data": f"checkin:{challenge.id}:{value}"}
                    for value in challenge.options[:2]
                ],
                [
                    {"text": str(value), "callback_data": f"checkin:{challenge.id}:{value}"}
                    for value in challenge.options[2:]
                ],
            ]
        },
    }
