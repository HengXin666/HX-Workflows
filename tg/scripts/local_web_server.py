"""HTTP transport and background job state for the local TG web API."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import yaml
from telethon import TelegramClient, errors
from telethon.sessions import StringSession

from local_web_accounts import (
    DEFAULT_ALT_OUT,
    DEFAULT_API_HASH,
    DEFAULT_API_ID,
    DEFAULT_MAIN_OUT,
    DEFAULT_WORKFLOWS,
    ROOT,
    accounts_from_file,
    append_session,
    load_account_cache,
    make_qr_data_url,
    masked_session,
    parse_proxy,
    profile_from_session,
    read_lines,
    save_account_cache,
    session_for_account,
    sessions_with_accounts,
)
from local_web_flows import (
    apply_flow_configs,
    compile_flow_outputs,
    delete_workflow,
    flow_to_signins,
    ordered_node_ids,
    read_workflows,
    save_workflow,
)
from local_web_telegram import (
    list_bot_commands,
    list_dialogs,
    list_messages,
    refresh_profiles,
    send_chat_message,
)

CLIENT_DISCONNECT_ERRNOS = {errno.EPIPE, errno.ECONNRESET}
MAX_REQUEST_BODY = 1024 * 1024
MAX_PAGE_LIMIT = 100


def parse_page_limit(value: str, default: int = 40) -> int:
    try:
        limit = int(value or default)
    except ValueError as exc:
        raise ValueError("limit 必须是整数") from exc
    if not 1 <= limit <= MAX_PAGE_LIMIT:
        raise ValueError(f"limit 必须在 1..{MAX_PAGE_LIMIT} 之间")
    return limit


@dataclass
class LoginJob:
    id: str
    role: str
    status: str = "pending"
    url: str | None = None
    qr_image: str | None = None
    account: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)


@dataclass
class RunJob:
    id: str
    status: str = "running"
    output: str = ""
    active_node: str | None = None
    completed_nodes: list[str] = field(default_factory=list)
    failed_node: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)


login_jobs: dict[str, LoginJob] = {}
run_jobs: dict[str, RunJob] = {}
run_events: dict[str, threading.Condition] = {}
jobs_lock = threading.Lock()

async def qr_login(job_id: str, api_id: int, api_hash: str, out: Path, role: str, timeout: int, proxy_url: str | None) -> None:
    proxy = parse_proxy(proxy_url)
    client = TelegramClient(StringSession(), api_id, api_hash, proxy=proxy)
    await client.connect()
    try:
        qr = await client.qr_login()
        with jobs_lock:
            login_jobs[job_id].status = "waiting"
            login_jobs[job_id].url = qr.url
            login_jobs[job_id].qr_image = make_qr_data_url(qr.url)
        try:
            await qr.wait(timeout=timeout)
        except errors.SessionPasswordNeededError:
            with jobs_lock:
                login_jobs[job_id].status = "needs_password"
                login_jobs[job_id].error = "该账号开启了二步验证，请使用命令行导入。"
            return

        session = client.session.save()
        if not session:
            raise RuntimeError("未能生成 session string")
        append_session(out, session)
        fallback_profile = {
            "telegram_id": "",
            "username": "",
            "name": "本地主账号" if role == "main" else "本地小号",
            "avatar": "",
        }
        with jobs_lock:
            login_jobs[job_id].status = "done"
            login_jobs[job_id].account = {"role": role, **fallback_profile, "masked_session": masked_session(session)}
        try:
            profile = await profile_from_session(session, api_id, api_hash, proxy)
        except Exception:
            profile = fallback_profile
        cache = load_account_cache(out)
        cache[session] = {"session": session, "role": role, **profile}
        save_account_cache(out, list(cache.values()))
        with jobs_lock:
            login_jobs[job_id].status = "done"
            login_jobs[job_id].account = {"role": role, **profile, "masked_session": masked_session(session)}
    except Exception as exc:
        with jobs_lock:
            login_jobs[job_id].status = "error"
            login_jobs[job_id].error = f"{type(exc).__name__}: {exc}"
    finally:
        await client.disconnect()

def run_flow(
    job_id: str,
    flow: dict[str, Any],
    alt_out: Path,
    main_out: Path,
    account_limit: int | None = None,
    account_ids: list[str] | None = None,
    proxy: str | None = None,
) -> None:
    ordered = ordered_node_ids(flow)
    with jobs_lock:
        run_jobs[job_id].active_node = ordered[0] if ordered else None
    notify_run(job_id)
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "signins.yml"
        config_path.write_text(yaml.safe_dump(flow_to_signins(flow), allow_unicode=True, sort_keys=False), encoding="utf-8")
        env = os.environ.copy()
        selected_ids = set(account_ids or [])
        if selected_ids:
            sessions = [session for account, session in sessions_with_accounts(alt_out, main_out) if account.get("id") in selected_ids]
        else:
            sessions = read_lines(alt_out)
        if not selected_ids and account_limit and account_limit > 0:
            sessions = sessions[:account_limit]
        env["TG_SESSION_STRINGS"] = "\n".join(sessions)
        if proxy:
            env["TG_PROXY"] = proxy
        command = ["uv", "run", "python", "-u", "scripts/sign_from_config.py", "--config", str(config_path), "run-enabled"]
        try:
            with jobs_lock:
                run_jobs[job_id].output += f"$ {' '.join(command)}\n"
                run_jobs[job_id].output += f"accounts: {len(sessions)}\n"
                run_jobs[job_id].output += f"proxy: {proxy or '(none)'}\n"
            notify_run(job_id)
            proc = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                with jobs_lock:
                    run_jobs[job_id].output += line
                    marker = re.search(r"^::node::([^:]+)::(start|done|error)::", line.strip())
                    if marker:
                        node_id, state = marker.group(1), marker.group(2)
                        if state == "start":
                            run_jobs[job_id].active_node = node_id
                        elif state == "done":
                            if node_id not in run_jobs[job_id].completed_nodes:
                                run_jobs[job_id].completed_nodes.append(node_id)
                            if run_jobs[job_id].active_node == node_id:
                                run_jobs[job_id].active_node = None
                        elif state == "error":
                            run_jobs[job_id].failed_node = node_id
                            run_jobs[job_id].active_node = None
                notify_run(job_id)
            try:
                return_code = proc.wait(timeout=3600)
            except subprocess.TimeoutExpired:
                proc.kill()
                return_code = 124
            with jobs_lock:
                run_jobs[job_id].status = "done" if return_code == 0 else "error"
                run_jobs[job_id].completed_nodes = ordered if return_code == 0 else run_jobs[job_id].completed_nodes
                failed_node = run_jobs[job_id].active_node or (ordered[0] if ordered else None)
                run_jobs[job_id].active_node = None
                run_jobs[job_id].failed_node = None if return_code == 0 else failed_node
                run_jobs[job_id].error = None if return_code == 0 else f"exit code {return_code}"
                if return_code != 0:
                    run_jobs[job_id].output += f"\nexit code {return_code}\n"
            notify_run(job_id)
        except Exception:
            with jobs_lock:
                run_jobs[job_id].status = "error"
                run_jobs[job_id].active_node = None
                run_jobs[job_id].failed_node = ordered[0] if ordered else None
                run_jobs[job_id].error = traceback.format_exc()
                run_jobs[job_id].output += "\n" + run_jobs[job_id].error
            notify_run(job_id)


def notify_run(job_id: str) -> None:
    condition = run_events.get(job_id)
    if condition:
        with condition:
            condition.notify_all()

class Handler(BaseHTTPRequestHandler):
    api_id = DEFAULT_API_ID
    api_hash = DEFAULT_API_HASH
    alt_out = DEFAULT_ALT_OUT
    main_out = DEFAULT_MAIN_OUT
    workflows = DEFAULT_WORKFLOWS
    timeout = 180

    def log_message(self, format: str, *args: Any) -> None:
        if self.path.startswith("/api/run/status") or self.path.startswith("/api/run/stream"):
            return
        print(f"[local-web] {self.address_string()} {format % args}")

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length < 0 or length > MAX_REQUEST_BODY:
            raise ValueError(f"请求体不能超过 {MAX_REQUEST_BODY} 字节")
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        payload = json.loads(raw or "{}")
        if not isinstance(payload, dict):
            raise ValueError("JSON 请求体必须是对象")
        return payload

    def is_client_disconnect(self, exc: BaseException) -> bool:
        return isinstance(exc, (BrokenPipeError, ConnectionResetError)) or (
            isinstance(exc, OSError) and exc.errno in CLIENT_DISCONNECT_ERRNOS
        )

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError as exc:
            if self.is_client_disconnect(exc):
                return
            raise

    def send_sse(self, job_id: str) -> None:
        condition = run_events.setdefault(job_id, threading.Condition())
        last_payload = ""
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            while True:
                with jobs_lock:
                    job = run_jobs.get(job_id)
                    payload = json.dumps(asdict(job) if job else {"status": "not_found"}, ensure_ascii=False)
                    terminal = (job is None) or job.status in {"done", "error"}
                if payload != last_payload:
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    last_payload = payload
                if terminal:
                    break
                with condition:
                    condition.wait(timeout=1.5)
        except OSError as exc:
            if self.is_client_disconnect(exc):
                return
            raise

    def do_OPTIONS(self) -> None:
        self.send_json({})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if parsed.path == "/api/accounts":
            cache = load_account_cache(self.alt_out, self.main_out)
            accounts = accounts_from_file(self.main_out, "main", cache) + accounts_from_file(self.alt_out, "alt", cache)
            self.send_json({"accounts": accounts, "alt_file": str(self.alt_out), "main_file": str(self.main_out)})
            return
        if parsed.path == "/api/accounts/refresh":
            proxy = params.get("proxy", [""])[0] or os.environ.get("TG_PROXY")
            accounts = asyncio.run(refresh_profiles(self.api_id, self.api_hash, self.alt_out, self.main_out, parse_proxy(proxy)))
            self.send_json({"accounts": accounts})
            return
        if parsed.path == "/api/qr/status":
            job_id = params.get("id", [""])[0]
            with jobs_lock:
                job = login_jobs.get(job_id)
                payload = asdict(job) if job else {"status": "not_found"}
            self.send_json(payload)
            return
        if parsed.path == "/api/workflows":
            self.send_json({"workflows": read_workflows(self.workflows)})
            return
        if parsed.path == "/api/dialogs":
            account_id = params.get("account", [""])[0]
            proxy = params.get("proxy", [""])[0] or os.environ.get("TG_PROXY")
            query = params.get("q", [""])[0]
            try:
                limit = parse_page_limit(params.get("limit", ["40"])[0])
                session = session_for_account(account_id, self.alt_out, self.main_out)
                dialogs = asyncio.run(list_dialogs(session, self.api_id, self.api_hash, parse_proxy(proxy), limit, query))
                self.send_json({"dialogs": dialogs})
            except Exception as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/messages":
            account_id = params.get("account", [""])[0]
            peer = params.get("peer", [""])[0]
            proxy = params.get("proxy", [""])[0] or os.environ.get("TG_PROXY")
            try:
                limit = parse_page_limit(params.get("limit", ["40"])[0])
                if not peer:
                    raise RuntimeError("缺少 peer")
                session = session_for_account(account_id, self.alt_out, self.main_out)
                messages = asyncio.run(list_messages(session, self.api_id, self.api_hash, peer, parse_proxy(proxy), limit))
                self.send_json({"messages": messages})
            except Exception as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/bot/commands":
            account_id = params.get("account", [""])[0]
            peer = params.get("peer", [""])[0]
            proxy = params.get("proxy", [""])[0] or os.environ.get("TG_PROXY")
            try:
                if not peer:
                    raise RuntimeError("缺少 peer")
                session = session_for_account(account_id, self.alt_out, self.main_out)
                payload = asyncio.run(list_bot_commands(session, self.api_id, self.api_hash, peer, parse_proxy(proxy)))
                self.send_json(payload)
            except Exception as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/run/status":
            job_id = params.get("id", [""])[0]
            with jobs_lock:
                job = run_jobs.get(job_id)
                payload = asdict(job) if job else {"status": "not_found"}
            self.send_json(payload)
            return
        if parsed.path == "/api/run/stream":
            job_id = params.get("id", [""])[0]
            self.send_sse(job_id)
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            body = self.read_json()
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/qr/start":
            role = str(body.get("role") or "alt")
            proxy = str(body.get("proxy") or os.environ.get("TG_PROXY") or "")
            out = self.main_out if role == "main" else self.alt_out
            job_id = uuid.uuid4().hex
            with jobs_lock:
                login_jobs[job_id] = LoginJob(id=job_id, role=role)
            threading.Thread(
                target=lambda: asyncio.run(qr_login(job_id, self.api_id, self.api_hash, out, role, self.timeout, proxy)),
                daemon=True,
            ).start()
            self.send_json({"id": job_id, "status": "pending"})
            return
        if parsed.path == "/api/workflows":
            previous_id = str(body.pop("previous_id", "") or "")
            flow = save_workflow(self.workflows, body, previous_id)
            self.send_json({"workflow": flow})
            return
        if parsed.path == "/api/workflows/compile":
            self.send_json(compile_flow_outputs(body))
            return
        if parsed.path == "/api/workflows/apply":
            try:
                self.send_json(apply_flow_configs(body))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/run/start":
            flow = body.get("workflow") or body
            account_limit_raw = body.get("account_limit")
            account_limit = int(account_limit_raw) if account_limit_raw else None
            account_ids = [str(item) for item in body.get("account_ids", []) if str(item)]
            proxy = str(body.get("proxy") or os.environ.get("TG_PROXY") or "")
            job_id = uuid.uuid4().hex
            with jobs_lock:
                run_jobs[job_id] = RunJob(id=job_id)
            threading.Thread(target=lambda: run_flow(job_id, flow, self.alt_out, self.main_out, account_limit, account_ids, proxy), daemon=True).start()
            self.send_json({"id": job_id, "status": "running"})
            return
        if parsed.path == "/api/messages/send":
            account_id = str(body.get("account") or "")
            peer = str(body.get("peer") or "")
            text = str(body.get("text") or "")
            proxy = str(body.get("proxy") or os.environ.get("TG_PROXY") or "")
            try:
                if not peer:
                    raise RuntimeError("缺少 peer")
                if not text.strip():
                    raise RuntimeError("消息内容为空")
                session = session_for_account(account_id, self.alt_out, self.main_out)
                message = asyncio.run(send_chat_message(session, self.api_id, self.api_hash, peer, text, parse_proxy(proxy)))
                self.send_json({"message": message})
            except Exception as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/workflows/"):
            flow_id = unquote(parsed.path.removeprefix("/api/workflows/"))
            if not flow_id:
                self.send_json({"error": "missing workflow id"}, HTTPStatus.BAD_REQUEST)
                return
            deleted = delete_workflow(self.workflows, flow_id)
            self.send_json({"deleted": deleted, "id": flow_id})
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
