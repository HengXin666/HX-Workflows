from __future__ import annotations

import datetime as dt
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import runner  # noqa: E402


class SchedulingTests(unittest.TestCase):
    def test_supported_schedule_expressions(self) -> None:
        now = dt.datetime(2026, 7, 18, 8, 30)
        self.assertTrue(runner.minute_matches("every:30", now))
        self.assertTrue(runner.minute_matches("hourly:30", now))
        self.assertTrue(runner.minute_matches("daily:08:30", now))
        self.assertTrue(runner.minute_matches("cron:08:30", now))
        self.assertFalse(runner.minute_matches("daily:09:30", now))

    def test_invalid_schedule_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "N > 0"):
            runner.minute_matches("every:0", dt.datetime.now())
        with self.assertRaisesRegex(ValueError, "unsupported"):
            runner.minute_matches("weekly:monday", dt.datetime.now())
        with self.assertRaisesRegex(ValueError, "0..59"):
            runner.minute_matches("hourly:60", dt.datetime.now())
        with self.assertRaisesRegex(ValueError, "0..23"):
            runner.minute_matches("daily:24:00", dt.datetime.now())


class TaskGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tasks = runner.parse_tasks(
            {
                "tasks": [
                    {"id": "prepare", "command": "true"},
                    {"id": "sign", "command": "true", "needs": ["prepare"]},
                    {"id": "notify", "command": "true", "needs": ["sign"]},
                ]
            }
        )

    def test_dependencies_are_deduplicated_and_ordered(self) -> None:
        self.assertEqual(
            runner.resolve_order(self.tasks, ["notify", "sign"]),
            ["prepare", "sign", "notify"],
        )

    def test_unknown_task_and_cycle_are_rejected(self) -> None:
        with self.assertRaisesRegex(SystemExit, "unknown task"):
            runner.resolve_order(self.tasks, ["missing"])
        cyclic = runner.parse_tasks(
            {"tasks": [{"id": "a", "command": "true", "needs": ["b"]}, {"id": "b", "command": "true", "needs": ["a"]}]}
        )
        with self.assertRaisesRegex(SystemExit, "dependency cycle"):
            runner.resolve_order(cyclic, ["a"])

    def test_duplicate_ids_are_rejected(self) -> None:
        with self.assertRaisesRegex(SystemExit, "duplicate task id"):
            runner.parse_tasks({"tasks": [{"id": "same", "command": "a"}, {"id": "same", "command": "b"}]})


class AccountExecutionTests(unittest.TestCase):
    def test_each_nonempty_secret_line_gets_isolated_account_environment(self) -> None:
        task = runner.Task(
            id="multi",
            enabled=True,
            command="run-sign",
            foreach_secret_lines="TG_SESSIONS_TEST",
        )
        calls: list[dict[str, str]] = []

        def record(_command: str, _cwd: Path, env: dict[str, str], _timeout: int) -> None:
            calls.append(env)

        with patch.dict(os.environ, {"TG_SESSIONS_TEST": " session-a\n\nsession-b \n"}, clear=False):
            with patch.object(runner, "run_shell", side_effect=record):
                runner.run_task(task)

        self.assertEqual([item["TG_ACCOUNT_INDEX"] for item in calls], ["1", "2"])
        self.assertEqual([item["TG_SESSION_STRING"] for item in calls], ["session-a", "session-b"])

    def test_missing_multi_account_secret_fails_before_side_effect(self) -> None:
        task = runner.Task(id="multi", enabled=True, command="run-sign", foreach_secret_lines="TG_SESSIONS_TEST")
        with patch.dict(os.environ, {}, clear=True), patch.object(runner, "run_shell") as run_shell:
            with self.assertRaisesRegex(SystemExit, "empty or missing"):
                runner.run_task(task)
        run_shell.assert_not_called()


if __name__ == "__main__":
    unittest.main()
