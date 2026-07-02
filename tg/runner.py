#!/usr/bin/env python3
"""Composable task runner for Telegram-related GitHub workflows.

The runner intentionally keeps scheduling/orchestration in YAML while leaving
actual Telegram logic in separate scripts. This makes it easy to add, disable,
or reorder tasks without editing the GitHub Actions workflow.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent
CONFIG_DIR = ROOT / "config"
DEFAULT_CONFIG = CONFIG_DIR / "tasks.yml"
LEGACY_DEFAULT_CONFIG = ROOT / "tasks.yml"


@dataclass(frozen=True)
class Task:
    id: str
    enabled: bool
    command: str
    schedule: list[str] = field(default_factory=list)
    needs: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    timeout_minutes: int = 20
    foreach_secret_lines: str | None = None


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise SystemExit("Config root must be a mapping")
    data.setdefault("timezone", "Asia/Tokyo")
    data.setdefault("tasks", [])
    return data


def parse_tasks(config: dict[str, Any]) -> dict[str, Task]:
    raw_tasks = config.get("tasks", [])
    if not isinstance(raw_tasks, list):
        raise SystemExit("tasks must be a list")

    tasks: dict[str, Task] = {}
    for raw in raw_tasks:
        if not isinstance(raw, dict):
            raise SystemExit("each task must be a mapping")
        task_id = str(raw.get("id", "")).strip()
        command = str(raw.get("command", "")).strip()
        if not task_id:
            raise SystemExit("task.id is required")
        if not command:
            raise SystemExit(f"task {task_id}: command is required")
        if task_id in tasks:
            raise SystemExit(f"duplicate task id: {task_id}")

        tasks[task_id] = Task(
            id=task_id,
            enabled=bool(raw.get("enabled", True)),
            command=command,
            schedule=[str(x).strip() for x in raw.get("schedule", [])],
            needs=[str(x).strip() for x in raw.get("needs", [])],
            env={str(k): str(v) for k, v in (raw.get("env", {}) or {}).items()},
            cwd=str(raw["cwd"]) if raw.get("cwd") else None,
            timeout_minutes=int(raw.get("timeout_minutes", 20)),
            foreach_secret_lines=(str(raw.get("foreach_secret_lines")) if raw.get("foreach_secret_lines") else None),
        )
    return tasks


def minute_matches(expr: str, now: dt.datetime) -> bool:
    """Tiny scheduler predicate.

    Supported expressions:
    - every:N          every N minutes
    - hourly:MM       every hour at minute MM
    - daily:HH:MM     every day at HH:MM
    - cron:HH:MM      alias of daily:HH:MM for readability

    GitHub Actions is the coarse timer. This predicate decides which configured
    task should run during the current wake-up.
    """
    if expr.startswith("every:"):
        n = int(expr.split(":", 1)[1])
        if n <= 0:
            raise ValueError("every:N requires N > 0")
        return (now.hour * 60 + now.minute) % n == 0

    if expr.startswith("hourly:"):
        minute = int(expr.split(":", 1)[1])
        return now.minute == minute

    if expr.startswith("daily:") or expr.startswith("cron:"):
        _, value = expr.split(":", 1)
        hour_s, minute_s = value.split(":", 1)
        return now.hour == int(hour_s) and now.minute == int(minute_s)

    raise ValueError(f"unsupported schedule expression: {expr}")


def due_tasks(tasks: dict[str, Task], timezone: str) -> list[str]:
    now = dt.datetime.now(ZoneInfo(timezone)).replace(second=0, microsecond=0)
    selected: list[str] = []
    for task in tasks.values():
        if not task.enabled or not task.schedule:
            continue
        if any(minute_matches(expr, now) for expr in task.schedule):
            selected.append(task.id)
    return selected


def resolve_order(tasks: dict[str, Task], requested: list[str]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    ordered: list[str] = []

    def visit(task_id: str) -> None:
        if task_id not in tasks:
            raise SystemExit(f"unknown task: {task_id}")
        if task_id in visited:
            return
        if task_id in visiting:
            raise SystemExit(f"dependency cycle detected at task: {task_id}")
        visiting.add(task_id)
        for dep in tasks[task_id].needs:
            visit(dep)
        visiting.remove(task_id)
        visited.add(task_id)
        ordered.append(task_id)

    for task_id in requested:
        visit(task_id)
    return ordered


def build_env(task: Task, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.update(task.env)
    if extra:
        env.update(extra)
    return env


def run_shell(command: str, cwd: Path, env: dict[str, str], timeout_minutes: int) -> None:
    printable_cwd = cwd.relative_to(ROOT.parent) if cwd.is_relative_to(ROOT.parent) else cwd
    print(f"$ {command}")
    print(f"cwd={printable_cwd}")
    subprocess.run(
        command,
        shell=True,
        cwd=str(cwd),
        env=env,
        check=True,
        timeout=timeout_minutes * 60,
    )


def run_task(task: Task) -> None:
    if not task.enabled:
        print(f"skip disabled task: {task.id}")
        return

    cwd = (ROOT / task.cwd).resolve() if task.cwd else ROOT
    if not cwd.exists():
        raise SystemExit(f"task {task.id}: cwd not found: {cwd}")

    print(f"::group::task {task.id}")
    try:
        if task.foreach_secret_lines:
            raw = os.environ.get(task.foreach_secret_lines, "")
            lines = [line.strip() for line in raw.splitlines() if line.strip()]
            if not lines:
                raise SystemExit(
                    f"task {task.id}: secret {task.foreach_secret_lines} is empty or missing"
                )
            for index, line in enumerate(lines, start=1):
                print(f"--- account #{index} ---")
                run_shell(
                    task.command,
                    cwd,
                    build_env(task, {"TG_SESSION_STRING": line, "TG_ACCOUNT_INDEX": str(index)}),
                    task.timeout_minutes,
                )
        else:
            run_shell(task.command, cwd, build_env(task), task.timeout_minutes)
    finally:
        print("::endgroup::")


def list_tasks(tasks: dict[str, Task], timezone: str) -> None:
    now = dt.datetime.now(ZoneInfo(timezone)).replace(second=0, microsecond=0)
    print(f"timezone: {timezone}")
    print(f"now: {now.isoformat()}")
    for task in tasks.values():
        status = "enabled" if task.enabled else "disabled"
        schedules = ", ".join(task.schedule) if task.schedule else "manual-only"
        deps = ", ".join(task.needs) if task.needs else "-"
        print(f"- {task.id} [{status}] schedule={schedules} needs={deps}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run composable TG tasks")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to tasks.yml")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List configured tasks")
    sub.add_parser("due", help="Print due task ids")
    sub.add_parser("run-due", help="Run tasks due right now")
    sub.add_parser("run-all", help="Run all enabled tasks")
    run = sub.add_parser("run", help="Run one or more task ids")
    run.add_argument("task_ids", nargs="+")

    args = parser.parse_args(argv)
    config_path = Path(args.config)
    if config_path == DEFAULT_CONFIG and not config_path.exists() and LEGACY_DEFAULT_CONFIG.exists():
        config_path = LEGACY_DEFAULT_CONFIG
    config_path = config_path.resolve()
    config = load_config(config_path)
    timezone = str(config.get("timezone", "Asia/Tokyo"))
    tasks = parse_tasks(config)

    if args.cmd == "list":
        list_tasks(tasks, timezone)
        return 0

    if args.cmd == "due":
        for task_id in due_tasks(tasks, timezone):
            print(task_id)
        return 0

    if args.cmd == "run-due":
        requested = due_tasks(tasks, timezone)
        if not requested:
            print("no due tasks")
            return 0
    elif args.cmd == "run-all":
        requested = [task.id for task in tasks.values() if task.enabled]
    elif args.cmd == "run":
        requested = args.task_ids
    else:
        raise AssertionError(args.cmd)

    for task_id in resolve_order(tasks, requested):
        run_task(tasks[task_id])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
