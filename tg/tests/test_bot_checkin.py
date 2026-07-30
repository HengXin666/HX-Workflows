from __future__ import annotations
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bot_checkin import CheckinStore, challenge_message  # noqa: E402


class CheckinTests(unittest.TestCase):
    def test_wrong_retry_success_and_daily_idempotency(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CheckinStore(Path(directory) / "checkin.sqlite3")
            store.upsert_user(7, "user", "User", now=100)
            challenge = store.create_challenge(7, 9, now=100, rng=random.Random(3))
            answer = next(
                value for value in challenge.options if store.verify(challenge.id, 7, value, now=101) == "success"
            )
            self.assertIn(answer, challenge.options)
            self.assertEqual(store.user_stats(7), {"points": 1, "days": 1})
            second = store.create_challenge(7, 9, now=102, rng=random.Random(4))
            correct = next(value for value in second.options if value not in [])
            while store.verify(second.id, 7, correct, now=103) == "wrong":
                correct = second.options[second.options.index(correct) + 1]
            self.assertEqual(store.verify(second.id, 7, correct, now=103), "already")
            store.close()

    def test_inline_keyboard_has_four_choices(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CheckinStore(Path(directory) / "db.sqlite3")
            challenge = store.create_challenge(1, 1, now=1, rng=random.Random(1))
            payload = challenge_message(challenge)
            self.assertEqual(sum(map(len, payload["reply_markup"]["inline_keyboard"])), 4)
            store.close()

    def test_difficulty_rotation_and_sqlite_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "persistent.sqlite3"
            store = CheckinStore(path)
            store.upsert_user(5, now=1_700_000_000)
            easy = store.create_challenge(5, 8, now=1_700_000_000, difficulty="easy", rng=random.Random(1))
            wrong = next(option for option in easy.options if store.verify(easy.id, 5, option, now=1_700_000_001) == "wrong")
            self.assertIn(wrong, easy.options)
            retry = store.create_challenge(5, 8, now=1_700_000_002, difficulty="hard", rng=random.Random(2))
            self.assertNotEqual((easy.id, easy.question), (retry.id, retry.question))
            self.assertIn("×", retry.question)
            correct = next(option for option in retry.options if store.verify(retry.id, 5, option, now=1_700_000_003, points=10) == "success")
            self.assertIn(correct, retry.options)
            store.close()
            reopened = CheckinStore(path)
            self.assertEqual(reopened.user_stats(5), {"points": 10, "days": 1})
            self.assertTrue(reopened.checked_in_today(5, now=1_700_000_004))
            reopened.close()


if __name__ == "__main__":
    unittest.main()
