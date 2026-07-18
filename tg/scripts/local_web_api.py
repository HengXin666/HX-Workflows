#!/usr/bin/env python3
"""Local-only HTTP API for the TG workflow web app.

Implementation is split by responsibility; imports remain here for compatibility.
"""

from __future__ import annotations

import argparse
from http.server import ThreadingHTTPServer
from pathlib import Path

from local_web_accounts import (
    DEFAULT_ALT_OUT,
    DEFAULT_MAIN_OUT,
    DEFAULT_WORKFLOWS,
    account_cache_path,
    accounts_from_file,
    append_session,
    load_account_cache,
    make_qr_data_url,
    masked_session,
    parse_proxy,
    profile_from_session,
    read_lines,
    resolve_api_hash,
    resolve_api_id,
    save_account_cache,
    session_for_account,
    sessions_with_accounts,
    write_lines,
)
from local_web_flows import (
    apply_flow_configs,
    compile_flow_outputs,
    delete_workflow,
    flow_to_github_actions,
    flow_to_signins,
    flow_to_tasks,
    is_default_example_flow,
    ordered_node_ids,
    read_workflows,
    save_workflow,
    write_workflows,
)
from local_web_server import Handler, LoginJob, RunJob, parse_page_limit, qr_login, run_flow
from local_web_telegram import (
    list_bot_commands,
    list_dialogs,
    list_messages,
    refresh_profiles,
    resolve_dialog_entity,
    resolve_peer_value,
    send_chat_message,
)

__all__ = [
    "DEFAULT_ALT_OUT", "DEFAULT_MAIN_OUT", "DEFAULT_WORKFLOWS", "Handler", "LoginJob", "RunJob",
    "account_cache_path", "accounts_from_file", "append_session", "apply_flow_configs",
    "compile_flow_outputs", "delete_workflow", "flow_to_github_actions", "flow_to_signins",
    "flow_to_tasks", "is_default_example_flow", "list_bot_commands", "list_dialogs", "list_messages",
    "load_account_cache", "make_qr_data_url", "masked_session", "ordered_node_ids", "parse_page_limit",
    "parse_proxy", "profile_from_session", "qr_login", "read_lines", "read_workflows", "refresh_profiles",
    "resolve_api_hash", "resolve_api_id", "resolve_dialog_entity", "resolve_peer_value", "run_flow",
    "save_account_cache", "save_workflow", "send_chat_message", "session_for_account",
    "sessions_with_accounts", "write_lines", "write_workflows",
]

def main() -> int:
    parser = argparse.ArgumentParser(description="Run local TG web API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--out", default=str(DEFAULT_ALT_OUT))
    parser.add_argument("--main-out", default=str(DEFAULT_MAIN_OUT))
    parser.add_argument("--workflows", default=str(DEFAULT_WORKFLOWS))
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--api-id")
    parser.add_argument("--api-hash")
    args = parser.parse_args()

    Handler.api_id = resolve_api_id(args.api_id)
    Handler.api_hash = resolve_api_hash(args.api_hash)
    Handler.alt_out = Path(args.out).expanduser().resolve()
    Handler.main_out = Path(args.main_out).expanduser().resolve()
    Handler.workflows = Path(args.workflows).expanduser().resolve()
    Handler.timeout = args.timeout

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"TG local web API: http://{args.host}:{args.port}")
    print(f"Alt session pool: {Handler.alt_out}")
    print(f"Main session: {Handler.main_out}")
    print(f"Workflows: {Handler.workflows}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
