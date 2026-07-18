#!/usr/bin/env python3
"""Run Telegram sign-in jobs from a plaintext YAML config.

Implementation is split by responsibility; imports remain here for compatibility.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from signer_actions import collect_messages, handle_forward, join_target, run_actions
from signer_core import (
    DEFAULT_CONFIG,
    DEFAULT_REPORT,
    LEGACY_DEFAULT_CONFIG,
    RunResult,
    deep_merge,
    entity_label,
    init_resource_pool_db,
    listify,
    load_yaml,
    mark_resource_seen,
    media_fingerprint,
    media_kind,
    message_text,
    normalize_join_target,
    optional_env,
    parse_proxy,
    read_secret_lines,
    required_env,
    resolve_local_path,
    resource_seen,
    should_forward,
    telegram_api_config,
    text_matches,
)
from signer_jobs import list_jobs, run_enabled, run_job, run_job_collect, run_one_account
from signer_reporting import (
    bot_api_notify,
    build_summary,
    job_to_report_workflow,
    result_status_label,
    result_texts,
    result_to_report_item,
    send_report_with_emall,
    write_report,
)

__all__ = [
    "RunResult", "bot_api_notify", "build_summary", "collect_messages", "deep_merge", "entity_label",
    "handle_forward", "init_resource_pool_db", "job_to_report_workflow", "join_target", "list_jobs",
    "listify", "load_yaml", "mark_resource_seen", "media_fingerprint", "media_kind", "message_text",
    "normalize_join_target", "optional_env", "parse_proxy", "read_secret_lines", "required_env",
    "resolve_local_path", "resource_seen", "result_status_label", "result_texts", "result_to_report_item",
    "run_actions", "run_enabled", "run_job", "run_job_collect", "run_one_account",
    "send_report_with_emall", "should_forward", "telegram_api_config", "text_matches", "write_report",
]

def main() -> int:
    parser = argparse.ArgumentParser(description="Run TG sign-in jobs from signins.yml")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to signins.yml")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    run = sub.add_parser("run")
    run.add_argument("job_id")
    run_enabled_parser = sub.add_parser("run-enabled")
    run_enabled_parser.add_argument("--mail", action="store_true", help="运行后发送 HTML 邮件报告")
    run_enabled_parser.add_argument("--report", default=str(DEFAULT_REPORT), help="输出 JSON 报告路径")

    args = parser.parse_args()
    config_path = Path(args.config)
    if config_path == DEFAULT_CONFIG and not config_path.exists() and LEGACY_DEFAULT_CONFIG.exists():
        config_path = LEGACY_DEFAULT_CONFIG
    config = load_yaml(config_path.resolve())
    if args.cmd == "list":
        return asyncio.run(list_jobs(config))
    if args.cmd == "run":
        return asyncio.run(run_job(config, args.job_id))
    if args.cmd == "run-enabled":
        return asyncio.run(run_enabled(config, args.mail, Path(args.report).resolve()))
    raise AssertionError(args.cmd)



if __name__ == "__main__":
    raise SystemExit(main())
