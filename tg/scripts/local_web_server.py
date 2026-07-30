"""HTTP transport and background job state for the local TG web API."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import hmac
import json
import os
import re
import secrets
import signal
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
from collections import OrderedDict
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import yaml
from local_web_accounts import (
    DEFAULT_ALT_OUT,
    DEFAULT_API_HASH,
    DEFAULT_API_ID,
    DEFAULT_MAIN_OUT,
    DEFAULT_WORKFLOWS,
    ROOT,
    accounts_from_file,
    load_account_cache,
    parse_proxy,
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
from local_web_bots import DEFAULT_BOTS, DEFAULT_BOT_WORKFLOWS, bot_route_delete, bot_route_get, bot_route_post
from local_web_github import (
    GitHubCliError,
    github_status,
    local_credential_summary,
    manage_workflow,
    sync_local_credentials,
)
from local_web_login import LoginJob, create_login_job, get_login_job, qr_login, submit_login_password  # noqa: F401
from local_web_resource_groups import get_resource_group_job, start_resource_group_job
from local_web_telegram import (
    download_message_media,
    list_bot_commands,
    list_dialogs,
    list_messages,
    refresh_profiles,
    send_chat_message,
)

CLIENT_DISCONNECT_ERRNOS = {errno.EPIPE, errno.ECONNRESET}
MAX_REQUEST_BODY = 1024 * 1024
MAX_PAGE_LIMIT = 100
RUN_CANCEL_GRACE_SECONDS = 2.0
RUN_CANCEL_POLL_SECONDS = 0.05
MAX_ACCOUNT_CONCURRENCY = 8
ACTIVE_RUN_STATUSES = frozenset({"running", "cancelling"})
TERMINAL_RUN_STATUSES = frozenset({"done", "error", "cancelled"})
TRUSTED_ORIGIN = re.compile(r"^https?://(?:127\.0\.0\.1|localhost)(?::\d+)?$")
RESOURCE_PREVIEWS_EXPORT = ROOT / "sessions" / "resource_previews.json"
MEDIA_URL_SECRET = secrets.token_bytes(32)
MAX_THUMBNAIL_CACHE_ITEMS = 256
MAX_MEDIA_ASSET_ITEMS = 2048
thumbnail_cache: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
thumbnail_cache_lock = threading.Lock()
media_asset_sources: OrderedDict[str, list[tuple[str, str, int, str]]] = OrderedDict()
media_asset_sources_lock = threading.Lock()
telegram_account_locks: dict[str, threading.Lock] = {}
telegram_account_locks_lock = threading.Lock()


def telegram_account_lock(account_id: str) -> threading.Lock:
    with telegram_account_locks_lock:
        return telegram_account_locks.setdefault(account_id, threading.Lock())


def parse_message_ids(value: str) -> list[int]:
    if not value.strip():
        return []
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if len(parts) > 20:
        raise ValueError("ids 最多包含 20 个消息 ID")
    try:
        message_ids = [int(part) for part in parts]
    except ValueError as exc:
        raise ValueError("ids 必须是逗号分隔的整数") from exc
    if any(message_id < 1 for message_id in message_ids):
        raise ValueError("ids 必须是正整数")
    return list(dict.fromkeys(message_ids))


def _media_asset_key(media_id: str, account_id: str, peer: str, message_id: int) -> str:
    identity = media_id.strip() or f"message:{account_id}:{peer}:{message_id}"
    return hmac.new(MEDIA_URL_SECRET, f"asset:{identity}".encode(), hashlib.sha256).hexdigest()


def _media_asset_signature(asset_key: str, variant: str) -> str:
    payload = json.dumps(
        [asset_key, variant],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(MEDIA_URL_SECRET, payload, hashlib.sha256).hexdigest()


def message_media_url(
    account_id: str,
    peer: str,
    message_id: int,
    variant: str,
    proxy: str,
    media_id: str = "",
) -> str:
    asset_key = _media_asset_key(media_id, account_id, peer, message_id)
    source = (account_id, peer, message_id, proxy)
    with media_asset_sources_lock:
        sources = media_asset_sources.setdefault(asset_key, [])
        if source not in sources:
            sources.append(source)
        media_asset_sources.move_to_end(asset_key)
        while len(media_asset_sources) > MAX_MEDIA_ASSET_ITEMS:
            media_asset_sources.popitem(last=False)
    token = _media_asset_signature(asset_key, variant)
    return "/api/message-media?" + urlencode(
        {
            "asset": asset_key,
            "variant": variant,
            "token": token,
        }
    )


def media_sources_for_asset(asset_key: str) -> list[tuple[str, str, int, str]]:
    with media_asset_sources_lock:
        sources = list(media_asset_sources.get(asset_key, []))
        if sources:
            media_asset_sources.move_to_end(asset_key)
        return sources


def attach_message_media_urls(
    messages: list[dict[str, Any]],
    account_id: str,
    peer: str,
    proxy: str,
) -> list[dict[str, Any]]:
    for message in messages:
        media = message.get("media")
        if not isinstance(media, dict):
            continue
        message_id = int(message.get("id") or 0)
        if message_id < 1:
            continue
        if media.get("has_thumbnail"):
            media["thumbnail_url"] = message_media_url(
                account_id,
                peer,
                message_id,
                "thumbnail",
                proxy,
                str(media.get("media_id") or ""),
            )
        media["content_url"] = message_media_url(
            account_id,
            peer,
            message_id,
            "original",
            proxy,
            str(media.get("media_id") or ""),
        )
    return messages


def parse_page_limit(value: str, default: int = 40) -> int:
    try:
        limit = int(value or default)
    except ValueError as exc:
        raise ValueError("limit 必须是整数") from exc
    if not 1 <= limit <= MAX_PAGE_LIMIT:
        raise ValueError(f"limit 必须在 1..{MAX_PAGE_LIMIT} 之间")
    return limit


@dataclass
class RunJob:
    id: str
    status: str = "running"
    output: str = ""
    active_node: str | None = None
    completed_nodes: list[str] = field(default_factory=list)
    failed_node: str | None = None
    error: str | None = None
    cancel_requested: bool = False
    resource_links: list[dict[str, Any]] = field(default_factory=list)
    resource_previews: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


run_jobs: dict[str, RunJob] = {}
run_processes: dict[str, subprocess.Popen[str]] = {}
run_events: dict[str, threading.Condition] = {}
jobs_lock = threading.Lock()


def parse_resource_link_marker(line: str) -> dict[str, Any] | None:
    prefix = "::resource-link::"
    value = line.strip()
    if not value.startswith(prefix):
        return None
    try:
        payload = json.loads(value.removeprefix(prefix))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or not str(payload.get("url") or ""):
        return None
    return payload


def merge_resource_link(job: RunJob, resource: dict[str, Any]) -> None:
    url = str(resource["url"])
    for index, current in enumerate(job.resource_links):
        if str(current.get("url") or "") == url:
            job.resource_links[index] = resource
            return
    job.resource_links.append(resource)


def parse_resource_preview_marker(line: str) -> dict[str, Any] | None:
    prefix = "::resource-preview::"
    value = line.strip()
    if not value.startswith(prefix):
        return None
    try:
        payload = json.loads(value.removeprefix(prefix))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or not str(payload.get("resource_key") or ""):
        return None
    return payload


def merge_resource_preview(job: RunJob, preview: dict[str, Any]) -> None:
    resource_key = str(preview["resource_key"])
    for index, current in enumerate(job.resource_previews):
        if str(current.get("resource_key") or "") == resource_key:
            job.resource_previews[index] = preview
            return
    job.resource_previews.append(preview)


def _set_run_cancelled(job: RunJob) -> None:
    if job.status != "cancelled":
        job.output += "\n运行已取消。\n"
    job.status = "cancelled"
    job.active_node = None
    job.failed_node = None
    job.error = None


def _signal_process_group(proc: subprocess.Popen[str], sig: signal.Signals) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        return
    except OSError:
        if proc.poll() is not None:
            return
        try:
            if sig == signal.SIGTERM:
                proc.terminate()
            else:
                proc.kill()
        except ProcessLookupError:
            return


def _process_group_exists(proc: subprocess.Popen[str]) -> bool:
    try:
        os.killpg(proc.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return proc.poll() is None
    return True


def _wait_for_process_group_exit(proc: subprocess.Popen[str], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while _process_group_exists(proc):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(RUN_CANCEL_POLL_SECONDS, remaining))
    return True


def _terminate_process_group(proc: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + RUN_CANCEL_GRACE_SECONDS
    _signal_process_group(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=RUN_CANCEL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    remaining = max(0.0, deadline - time.monotonic())
    if not _wait_for_process_group_exit(proc, remaining):
        _signal_process_group(proc, signal.SIGKILL)
        try:
            proc.wait(timeout=RUN_CANCEL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass


def cancel_run_job(job_id: str) -> tuple[dict[str, Any], HTTPStatus]:
    should_terminate = False
    proc: subprocess.Popen[str] | None = None
    with jobs_lock:
        job = run_jobs.get(job_id)
        if job is None:
            return {"status": "not_found", "error": "本地运行任务不存在"}, HTTPStatus.NOT_FOUND
        if job.status in TERMINAL_RUN_STATUSES:
            return asdict(job), HTTPStatus.OK
        if job.status == "running":
            job.cancel_requested = True
            job.status = "cancelling"
            should_terminate = True
        elif job.status == "cancelling":
            job.cancel_requested = True
        proc = run_processes.get(job_id)
        payload = asdict(job)
    notify_run(job_id)
    if should_terminate and proc is not None:
        _terminate_process_group(proc)
    return payload, HTTPStatus.OK


def _run_flow(
    job_id: str,
    flow: dict[str, Any],
    alt_out: Path,
    main_out: Path,
    account_limit: int | None = None,
    account_ids: list[str] | None = None,
    proxy: str | None = None,
    account_concurrency: int = 1,
) -> None:
    ordered = ordered_node_ids(flow)
    with jobs_lock:
        job = run_jobs[job_id]
        if job.cancel_requested or job.status == "cancelling":
            _set_run_cancelled(job)
            cancelled_before_setup = True
        else:
            job.active_node = ordered[0] if ordered else None
            cancelled_before_setup = False
    notify_run(job_id)
    if cancelled_before_setup:
        return
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "signins.yml"
        config_path.write_text(yaml.safe_dump(flow_to_signins(flow), allow_unicode=True, sort_keys=False), encoding="utf-8")
        env = os.environ.copy()
        selected_ids = set(account_ids or [])
        available_accounts = sessions_with_accounts(alt_out, main_out)
        if selected_ids:
            selected_accounts = [
                (account, session)
                for account, session in available_accounts
                if account.get("id") in selected_ids
            ]
        else:
            selected_accounts = [
                (account, session)
                for account, session in available_accounts
                if account.get("role") == "alt"
            ]
        if not selected_ids and account_limit and account_limit > 0:
            selected_accounts = selected_accounts[:account_limit]
        sessions = [session for _, session in selected_accounts]
        env["TG_SESSION_STRINGS"] = "\n".join(sessions)
        env["TG_ACCOUNT_IDS"] = "\n".join(
            str(account.get("id") or "") for account, _ in selected_accounts
        )
        env["TG_ACCOUNT_CONCURRENCY"] = str(account_concurrency)
        if proxy:
            env["TG_PROXY"] = proxy
        command = ["uv", "run", "python", "-u", "scripts/sign_from_config.py", "--config", str(config_path), "run-enabled"]
        try:
            with jobs_lock:
                run_jobs[job_id].output += f"$ {' '.join(command)}\n"
                run_jobs[job_id].output += f"accounts: {len(sessions)}\n"
                run_jobs[job_id].output += f"account_concurrency: {account_concurrency}\n"
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
                start_new_session=True,
            )
            with jobs_lock:
                run_processes[job_id] = proc
                job = run_jobs[job_id]
                cancel_after_start = job.cancel_requested or job.status == "cancelling"
                if cancel_after_start:
                    job.cancel_requested = True
                    job.status = "cancelling"
            if cancel_after_start:
                notify_run(job_id)
                _terminate_process_group(proc)
            assert proc.stdout is not None
            for line in proc.stdout:
                resource_link = parse_resource_link_marker(line)
                resource_preview = parse_resource_preview_marker(line)
                with jobs_lock:
                    run_jobs[job_id].output += line
                    if resource_link is not None:
                        merge_resource_link(run_jobs[job_id], resource_link)
                    if resource_preview is not None:
                        merge_resource_preview(run_jobs[job_id], resource_preview)
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
                _signal_process_group(proc, signal.SIGKILL)
                return_code = 124
            with jobs_lock:
                run_processes.pop(job_id, None)
                job = run_jobs[job_id]
                if job.cancel_requested or job.status == "cancelling":
                    _set_run_cancelled(job)
                else:
                    job.status = "done" if return_code == 0 else "error"
                    job.completed_nodes = ordered if return_code == 0 else job.completed_nodes
                    failed_node = job.active_node or (ordered[0] if ordered else None)
                    job.active_node = None
                    job.failed_node = None if return_code == 0 else failed_node
                    job.error = None if return_code == 0 else f"exit code {return_code}"
                    if return_code != 0:
                        job.output += f"\nexit code {return_code}\n"
            notify_run(job_id)
        except Exception:
            with jobs_lock:
                run_processes.pop(job_id, None)
                job = run_jobs[job_id]
                if job.cancel_requested or job.status == "cancelling":
                    _set_run_cancelled(job)
                else:
                    job.status = "error"
                    job.active_node = None
                    job.failed_node = ordered[0] if ordered else None
                    job.error = traceback.format_exc()
                    job.output += "\n" + job.error
            notify_run(job_id)


def run_flow(
    job_id: str,
    flow: dict[str, Any],
    alt_out: Path,
    main_out: Path,
    account_limit: int | None = None,
    account_ids: list[str] | None = None,
    proxy: str | None = None,
    account_concurrency: int = 1,
) -> None:
    try:
        _run_flow(job_id, flow, alt_out, main_out, account_limit, account_ids, proxy, account_concurrency)
    except Exception:
        error = traceback.format_exc()
        with jobs_lock:
            run_processes.pop(job_id, None)
            job = run_jobs[job_id]
            if job.cancel_requested or job.status == "cancelling":
                _set_run_cancelled(job)
            elif job.status in TERMINAL_RUN_STATUSES:
                return
            else:
                job.status = "error"
                job.failed_node = job.active_node
                job.active_node = None
                job.error = error
                job.output += "\n" + error
        notify_run(job_id)


def notify_run(job_id: str) -> None:
    condition = run_events.get(job_id)
    if condition:
        with condition:
            condition.notify_all()


def read_resource_previews(path: Path = RESOURCE_PREVIEWS_EXPORT) -> dict[str, Any]:
    if not path.exists():
        return {"previews": [], "path": str(path), "updated_at": None}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("资源预览清单必须是数组")
    return {
        "previews": [item for item in data if isinstance(item, dict)],
        "path": str(path),
        "updated_at": path.stat().st_mtime,
    }


def _is_resource_preview_export(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, list) and any(
        isinstance(item, dict) and str(item.get("resource_key") or "").strip()
        for item in data
    )


def resource_preview_files(
    workflows_path: Path,
    *,
    root: Path = ROOT,
    default_path: Path = RESOURCE_PREVIEWS_EXPORT,
) -> list[dict[str, Any]]:
    root = root.resolve()
    configured: set[Path] = {default_path.resolve()}
    try:
        workflows = read_workflows(workflows_path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        workflows = []
    for workflow in workflows:
        for node in workflow.get("nodes", []) if isinstance(workflow, dict) else []:
            data = node.get("data", {}) if isinstance(node, dict) else {}
            if not isinstance(data, dict) or str(data.get("kind") or "") != "preview_catalog":
                continue
            raw_path = str(data.get("exportPath") or "").strip()
            if not raw_path:
                continue
            candidate = Path(os.path.expandvars(raw_path)).expanduser()
            configured.add((root / candidate if not candidate.is_absolute() else candidate).resolve())

    sessions = root / "sessions"
    if sessions.is_dir():
        for candidate in sessions.rglob("*.json"):
            if candidate.is_file() and not candidate.is_symlink() and _is_resource_preview_export(candidate):
                configured.add(candidate.resolve())

    files: list[dict[str, Any]] = []
    for path in configured:
        exists = path.is_file()
        try:
            label = str(path.relative_to(root))
        except ValueError:
            label = str(path)
        resource_count: int | None = None
        error = ""
        if exists:
            try:
                resource_count = len(read_resource_previews(path)["previews"])
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                error = str(exc)
        files.append(
            {
                "id": str(path),
                "path": str(path),
                "label": label,
                "exists": exists,
                "resource_count": resource_count,
                "updated_at": path.stat().st_mtime if exists else None,
                "error": error,
            }
        )
    return sorted(
        files,
        key=lambda item: (
            not bool(item["exists"]),
            -float(item["updated_at"] or 0),
            str(item["label"]),
        ),
    )


def select_resource_preview_file(
    files: list[dict[str, Any]],
    requested: str = "",
    *,
    default_path: Path = RESOURCE_PREVIEWS_EXPORT,
) -> Path:
    allowed = {str(item["id"]): Path(str(item["path"])) for item in files}
    if requested:
        selected = allowed.get(requested)
        if selected is None:
            raise ValueError("资源预览索引不在允许的文件列表中")
        return selected
    default = allowed.get(str(default_path.resolve()))
    if default is not None and default.is_file():
        return default
    existing = next((Path(str(item["path"])) for item in files if item["exists"] and not item["error"]), None)
    return existing or default or default_path.resolve()


class Handler(BaseHTTPRequestHandler):
    api_id = DEFAULT_API_ID
    api_hash = DEFAULT_API_HASH
    alt_out = DEFAULT_ALT_OUT
    main_out = DEFAULT_MAIN_OUT
    workflows = DEFAULT_WORKFLOWS
    bots = DEFAULT_BOTS
    bot_workflows = DEFAULT_BOT_WORKFLOWS
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

    def is_trusted_origin(self) -> bool:
        origin = self.headers.get("Origin", "")
        return not origin or bool(TRUSTED_ORIGIN.fullmatch(origin))

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            origin = self.headers.get("Origin", "")
            self.send_header("Access-Control-Allow-Origin", origin if origin and self.is_trusted_origin() else "null")
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError as exc:
            if self.is_client_disconnect(exc):
                return
            raise

    def send_binary(self, content: bytes, content_type: str, *, cache: bool) -> None:
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header(
                "Cache-Control",
                "private, max-age=86400, immutable" if cache else "private, no-store",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Disposition", "inline")
            origin = self.headers.get("Origin", "")
            self.send_header("Access-Control-Allow-Origin", origin if origin and self.is_trusted_origin() else "null")
            self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(content)
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
            origin = self.headers.get("Origin", "")
            self.send_header("Access-Control-Allow-Origin", origin if origin and self.is_trusted_origin() else "null")
            self.send_header("Vary", "Origin")
            self.end_headers()
            while True:
                with jobs_lock:
                    job = run_jobs.get(job_id)
                    payload = json.dumps(asdict(job) if job else {"status": "not_found"}, ensure_ascii=False)
                    terminal = (job is None) or job.status in TERMINAL_RUN_STATUSES
                if payload != last_payload:
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    last_payload = payload
                if terminal:
                    self.close_connection = True
                    break
                with condition:
                    condition.wait(timeout=1.5)
        except OSError as exc:
            if self.is_client_disconnect(exc):
                return
            raise

    def do_OPTIONS(self) -> None:
        if not self.is_trusted_origin():
            self.send_json({"error": "仅允许本机 Web 页面访问"}, HTTPStatus.FORBIDDEN)
            return
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
            job = get_login_job(job_id)
            payload = asdict(job) if job else {"status": "not_found"}
            self.send_json(payload)
            return
        if parsed.path == "/api/workflows":
            self.send_json({"workflows": read_workflows(self.workflows)})
            return
        if parsed.path == "/api/resource-previews":
            try:
                files = resource_preview_files(self.workflows)
                selected = select_resource_preview_file(files, params.get("path", [""])[0])
                payload = read_resource_previews(selected)
                payload["files"] = files
                payload["selected_path"] = str(selected)
                self.send_json(payload)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                self.send_json({"error": f"资源预览清单读取失败: {exc}"}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/message-media":
            asset_key = params.get("asset", [""])[0]
            variant = params.get("variant", [""])[0]
            token = params.get("token", [""])[0]
            try:
                if not asset_key or variant not in {"thumbnail", "original"}:
                    raise ValueError("媒体请求参数不完整")
                expected = _media_asset_signature(asset_key, variant)
                if not token or not hmac.compare_digest(token, expected):
                    self.send_json({"error": "媒体地址签名无效"}, HTTPStatus.FORBIDDEN)
                    return
                sources = media_sources_for_asset(asset_key)
                if not sources:
                    raise FileNotFoundError("媒体预览地址已失效，请刷新消息后重试")
                cache_key = f"{asset_key}|{variant}"
                cached: tuple[bytes, str] | None = None
                if variant == "thumbnail":
                    with thumbnail_cache_lock:
                        cached = thumbnail_cache.get(cache_key)
                        if cached is not None:
                            thumbnail_cache.move_to_end(cache_key)
                if cached is None:
                    last_error: Exception | None = None
                    for account_id, peer, message_id, proxy in sources:
                        try:
                            # Repeated messages share one asset. Only the first
                            # request reaches Telegram; later requests reuse it.
                            with telegram_account_lock(account_id):
                                if variant == "thumbnail":
                                    with thumbnail_cache_lock:
                                        cached = thumbnail_cache.get(cache_key)
                                        if cached is not None:
                                            thumbnail_cache.move_to_end(cache_key)
                                if cached is None:
                                    session = session_for_account(account_id, self.alt_out, self.main_out)
                                    cached = asyncio.run(
                                        download_message_media(
                                            session,
                                            self.api_id,
                                            self.api_hash,
                                            peer,
                                            message_id,
                                            thumbnail=variant == "thumbnail",
                                            proxy=parse_proxy(proxy),
                                        )
                                    )
                            if cached is not None:
                                break
                        except Exception as exc:
                            last_error = exc
                    if cached is None:
                        if last_error is not None:
                            raise last_error
                        raise FileNotFoundError("媒体没有可用来源")
                    if variant == "thumbnail":
                        with thumbnail_cache_lock:
                            thumbnail_cache[cache_key] = cached
                            thumbnail_cache.move_to_end(cache_key)
                            while len(thumbnail_cache) > MAX_THUMBNAIL_CACHE_ITEMS:
                                thumbnail_cache.popitem(last=False)
                self.send_binary(cached[0], cached[1], cache=variant == "thumbnail")
            except FileNotFoundError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            except (OSError, RuntimeError, ValueError) as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                self.send_json({"error": f"Telegram 媒体读取失败: {type(exc).__name__}: {exc}"}, HTTPStatus.BAD_GATEWAY)
            return
        bot_payload = bot_route_get(parsed.path, self.bots, self.bot_workflows)
        if bot_payload is not None:
            self.send_json(bot_payload)
            return
        if parsed.path == "/api/github/status":
            repository = params.get("repository", [""])[0]
            try:
                payload = github_status(ROOT.parent, repository)
                payload["local_credentials"] = local_credential_summary(self.alt_out, self.main_out)
                self.send_json(payload)
            except (ValueError, GitHubCliError) as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/dialogs":
            account_id = params.get("account", [""])[0]
            proxy = params.get("proxy", [""])[0] or os.environ.get("TG_PROXY")
            query = params.get("q", [""])[0]
            try:
                limit = parse_page_limit(params.get("limit", ["40"])[0])
                session = session_for_account(account_id, self.alt_out, self.main_out)
                with telegram_account_lock(account_id):
                    dialogs = asyncio.run(
                        list_dialogs(session, self.api_id, self.api_hash, parse_proxy(proxy), limit, query)
                    )
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
                message_id_raw = params.get("message_id", [""])[0]
                message_id = int(message_id_raw) if message_id_raw else None
                message_ids = parse_message_ids(params.get("ids", [""])[0])
                if message_id is not None and message_id < 1:
                    raise ValueError("message_id 必须是正整数")
                if not peer:
                    raise RuntimeError("缺少 peer")
                session = session_for_account(account_id, self.alt_out, self.main_out)
                with telegram_account_lock(account_id):
                    messages = asyncio.run(
                        list_messages(
                            session,
                            self.api_id,
                            self.api_hash,
                            peer,
                            parse_proxy(proxy),
                            limit,
                            message_id,
                            message_ids,
                        )
                    )
                attach_message_media_urls(messages, account_id, peer, proxy)
                self.send_json({"messages": messages, "target_message_id": message_id})
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
                with telegram_account_lock(account_id):
                    payload = asyncio.run(
                        list_bot_commands(session, self.api_id, self.api_hash, peer, parse_proxy(proxy))
                    )
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
        if parsed.path == "/api/resource-groups/status":
            job_id = params.get("id", [""])[0]
            payload = get_resource_group_job(job_id) or {"status": "not_found"}
            self.send_json(payload)
            return
        if parsed.path == "/api/run/stream":
            job_id = params.get("id", [""])[0]
            self.send_sse(job_id)
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not self.is_trusted_origin():
            self.send_json({"error": "仅允许本机 Web 页面访问"}, HTTPStatus.FORBIDDEN)
            return
        try:
            body = self.read_json()
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/run/cancel":
            payload, status = cancel_run_job(str(body.get("id") or "").strip())
            self.send_json(payload, status)
            return
        if parsed.path == "/api/qr/start":
            role = str(body.get("role") or "alt")
            proxy = str(body.get("proxy") or os.environ.get("TG_PROXY") or "")
            out = self.main_out if role == "main" else self.alt_out
            job_id = uuid.uuid4().hex
            create_login_job(job_id, role)
            threading.Thread(
                target=lambda: asyncio.run(qr_login(job_id, self.api_id, self.api_hash, out, role, self.timeout, proxy)),
                daemon=True,
            ).start()
            self.send_json({"id": job_id, "status": "pending"})
            return
        try:
            bot_payload = bot_route_post(parsed.path, body, self.bots, self.bot_workflows)
        except (ValueError, RuntimeError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if bot_payload is not None:
            self.send_json(bot_payload)
            return
        if parsed.path == "/api/qr/password":
            job_id = str(body.get("id") or "")
            password = str(body.get("password") or "")
            try:
                job = submit_login_password(job_id, password)
                self.send_json({"id": job.id, "status": job.status})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/github/secrets/sync":
            try:
                if body.get("confirm") is not True:
                    raise ValueError("必须明确确认后才能写入 GitHub Secrets")
                repository = str(body.get("repository") or "")
                names = body.get("names")
                if not isinstance(names, list) or not all(isinstance(item, str) for item in names):
                    raise ValueError("names 必须是 Secret 名称数组")
                if not names:
                    raise ValueError("至少选择一个要同步的 Secret")
                uploaded = sync_local_credentials(
                    cwd=ROOT.parent,
                    repository=repository,
                    names=names,
                    alt_out=self.alt_out,
                    main_out=self.main_out,
                    api_id=self.api_id,
                    api_hash=self.api_hash,
                )
                self.send_json({"repository": repository, "uploaded": uploaded})
            except (ValueError, GitHubCliError) as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/github/workflows/action":
            try:
                if body.get("confirm") is not True:
                    raise ValueError("必须明确确认后才能操作远程工作流")
                state = manage_workflow(
                    cwd=ROOT.parent,
                    repository=str(body.get("repository") or ""),
                    workflow=str(body.get("workflow") or ""),
                    action=str(body.get("action") or ""),
                    ref=str(body.get("ref") or ""),
                )
                self.send_json({"state": state})
            except (ValueError, GitHubCliError) as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/workflows":
            previous_id = str(body.pop("previous_id", "") or "")
            flow = save_workflow(self.workflows, body, previous_id)
            self.send_json({"workflow": flow})
            return
        if parsed.path == "/api/workflows/compile":
            try:
                self.send_json(compile_flow_outputs(body))
            except (TypeError, ValueError) as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/workflows/apply":
            try:
                self.send_json(apply_flow_configs(body))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/run/start":
            flow = body.get("workflow") or body
            try:
                if not isinstance(flow, dict):
                    raise ValueError("workflow 必须是对象")
                flow_to_signins(flow)
            except (TypeError, ValueError) as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            account_limit_raw = body.get("account_limit")
            account_limit = int(account_limit_raw) if account_limit_raw else None
            account_ids = [str(item) for item in body.get("account_ids", []) if str(item)]
            account_concurrency_raw = body.get("account_concurrency") or 1
            try:
                account_concurrency = int(account_concurrency_raw)
            except (TypeError, ValueError):
                self.send_json({"error": "账号并发数必须是整数"}, HTTPStatus.BAD_REQUEST)
                return
            if not 1 <= account_concurrency <= MAX_ACCOUNT_CONCURRENCY:
                self.send_json({"error": f"账号并发数必须在 1..{MAX_ACCOUNT_CONCURRENCY} 之间"}, HTTPStatus.BAD_REQUEST)
                return
            proxy = str(body.get("proxy") or os.environ.get("TG_PROXY") or "")
            job_id = uuid.uuid4().hex
            with jobs_lock:
                active_job = next(
                    (job for job in run_jobs.values() if job.status in ACTIVE_RUN_STATUSES),
                    None,
                )
                if active_job is None:
                    run_jobs[job_id] = RunJob(id=job_id)
                    active_payload = None
                else:
                    active_payload = asdict(active_job)
            if active_payload is not None:
                self.send_json(
                    {"error": "已有活动中的本地运行任务", "job": active_payload},
                    HTTPStatus.CONFLICT,
                )
                return
            threading.Thread(
                target=lambda: run_flow(
                    job_id,
                    flow,
                    self.alt_out,
                    self.main_out,
                    account_limit,
                    account_ids,
                    proxy,
                    account_concurrency,
                ),
                daemon=True,
            ).start()
            self.send_json({"id": job_id, "status": "running"})
            return
        if parsed.path == "/api/resource-groups/sync":
            try:
                if body.get("confirm") is not True:
                    raise ValueError("必须明确确认后才能创建资源组或添加账号")
                title = str(body.get("title") or "").strip()
                if not 1 <= len(title) <= 128:
                    raise ValueError("资源组名称长度必须在 1..128 之间")
                owner_account_id = str(body.get("owner_account_id") or "").strip()
                if not owner_account_id:
                    raise ValueError("必须选择群主账号")
                requested_ids = body.get("member_account_ids")
                if requested_ids is not None and (
                    not isinstance(requested_ids, list)
                    or not all(isinstance(item, str) and item for item in requested_ids)
                ):
                    raise ValueError("member_account_ids 必须是账号 ID 数组")
                available = sessions_with_accounts(self.alt_out, self.main_out)
                selected_ids = set(requested_ids or [str(account.get("id") or "") for account, _ in available])
                selected_ids.add(owner_account_id)
                account_sessions = [
                    (account, session)
                    for account, session in available
                    if str(account.get("id") or "") in selected_ids
                ]
                found_ids = {str(account.get("id") or "") for account, _ in account_sessions}
                missing_ids = sorted(selected_ids - found_ids)
                if missing_ids:
                    raise ValueError(f"账号不存在或没有可用 session: {', '.join(missing_ids)}")
                proxy_value = str(body.get("proxy") or os.environ.get("TG_PROXY") or "")
                payload = start_resource_group_job(
                    title,
                    owner_account_id,
                    account_sessions,
                    self.api_id,
                    self.api_hash,
                    str(body.get("existing_target") or ""),
                    parse_proxy(proxy_value),
                )
                self.send_json(payload)
            except (RuntimeError, ValueError) as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
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
                with telegram_account_lock(account_id):
                    message = asyncio.run(
                        send_chat_message(session, self.api_id, self.api_hash, peer, text, parse_proxy(proxy))
                    )
                self.send_json({"message": message})
            except Exception as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        bot_payload = bot_route_delete(parsed.path, self.bots, self.bot_workflows)
        if bot_payload is not None:
            self.send_json(bot_payload)
            return
        if parsed.path.startswith("/api/workflows/"):
            flow_id = unquote(parsed.path.removeprefix("/api/workflows/"))
            if not flow_id:
                self.send_json({"error": "missing workflow id"}, HTTPStatus.BAD_REQUEST)
                return
            deleted = delete_workflow(self.workflows, flow_id)
            self.send_json({"deleted": deleted, "id": flow_id})
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
