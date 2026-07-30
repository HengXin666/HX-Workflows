"""Managed Telegram resource-group creation and membership synchronization."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telethon.tl.functions.channels import CreateChannelRequest
from telethon.tl.functions.messages import (
    CheckChatInviteRequest,
    ExportChatInviteRequest,
    ImportChatInviteRequest,
)

from local_web_accounts import ROOT, write_private_text
from local_web_telegram import resolve_dialog_entity

DEFAULT_RESOURCE_GROUPS = ROOT / "sessions" / "resource_groups.json"
RESOURCE_GROUP_TIMEOUT = 20.0


@dataclass
class ResourceGroupJob:
    id: str
    title: str
    owner_account_id: str
    status: str = "running"
    total: int = 0
    completed: int = 0
    joined: int = 0
    already: int = 0
    failed: int = 0
    invite_link: str = ""
    group_id: str = ""
    results: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None
    created_at: float = field(default_factory=time.time)


resource_group_jobs: dict[str, ResourceGroupJob] = {}
resource_group_jobs_lock = threading.Lock()
resource_group_storage_lock = threading.Lock()


def _invite_hash(value: str) -> str:
    target = value.strip()
    if target.startswith("tg://join"):
        return parse_qs(urlparse(target).query).get("invite", [""])[0]
    parsed = urlparse(target if "://" in target else f"https://{target}")
    if parsed.netloc.lower() in {"t.me", "telegram.me"}:
        path = parsed.path.strip("/")
        if path.startswith("+"):
            return path[1:]
        if path.startswith("joinchat/"):
            return path.split("/", 1)[1]
    return ""


def _group_id(entity: Any) -> str:
    raw = str(getattr(entity, "id", "") or "")
    if not raw:
        return ""
    if entity.__class__.__name__ == "Channel":
        return f"-100{raw.lstrip('-')}"
    return f"-{raw.lstrip('-')}"


async def _wait(awaitable, timeout: float = RESOURCE_GROUP_TIMEOUT):
    return await asyncio.wait_for(awaitable, timeout=timeout)


async def _owner_group(
    session: str,
    api_id: int,
    api_hash: str,
    title: str,
    existing_target: str,
    proxy,
    client_factory: Callable[..., Any],
) -> tuple[Any, str]:
    client = client_factory(StringSession(session), api_id, api_hash, proxy=proxy)
    try:
        await _wait(client.connect())
        invite_hash = _invite_hash(existing_target)
        if invite_hash:
            try:
                checked = await _wait(client(CheckChatInviteRequest(invite_hash)))
                entity = getattr(checked, "chat", None)
                if entity is None:
                    updates = await _wait(client(ImportChatInviteRequest(invite_hash)))
                    chats = getattr(updates, "chats", None) or []
                    entity = chats[0] if chats else None
            except errors.UserAlreadyParticipantError:
                checked = await _wait(client(CheckChatInviteRequest(invite_hash)))
                entity = getattr(checked, "chat", None)
        elif existing_target.strip():
            entity = await _wait(resolve_dialog_entity(client, existing_target))
        else:
            updates = await _wait(
                client(
                    CreateChannelRequest(
                        title=title,
                        about="Managed resource group created by HX TG Workflows",
                        megagroup=True,
                    )
                )
            )
            chats = getattr(updates, "chats", None) or []
            entity = chats[0] if chats else None
        if entity is None:
            raise RuntimeError("无法解析或创建资源组")
        exported = await _wait(
            client(
                ExportChatInviteRequest(
                    entity,
                    legacy_revoke_permanent=False,
                    request_needed=False,
                    title="HX resource sync",
                )
            )
        )
        invite_link = str(getattr(exported, "link", "") or "")
        if not _invite_hash(invite_link):
            raise RuntimeError("Telegram 未返回可直接加入的邀请链接")
        return entity, invite_link
    finally:
        try:
            await _wait(client.disconnect())
        except Exception:
            pass


async def _join_member(
    session: str,
    api_id: int,
    api_hash: str,
    invite_link: str,
    proxy,
    client_factory: Callable[..., Any],
) -> str:
    invite_hash = _invite_hash(invite_link)
    if not invite_hash:
        raise RuntimeError("资源组邀请链接无效")
    client = client_factory(StringSession(session), api_id, api_hash, proxy=proxy)
    try:
        await _wait(client.connect())
        checked = await _wait(client(CheckChatInviteRequest(invite_hash)))
        if getattr(checked, "chat", None) is not None:
            return "already"
        await _wait(client(ImportChatInviteRequest(invite_hash)))
        return "joined"
    except errors.UserAlreadyParticipantError:
        return "already"
    finally:
        try:
            await _wait(client.disconnect())
        except Exception:
            pass


async def synchronize_resource_group(
    title: str,
    owner_account_id: str,
    account_sessions: list[tuple[dict[str, Any], str]],
    api_id: int,
    api_hash: str,
    existing_target: str = "",
    proxy=None,
    *,
    client_factory: Callable[..., Any] = TelegramClient,
    join_delay: float = 0.35,
    on_progress: Callable[[dict[str, str]], None] | None = None,
) -> dict[str, Any]:
    by_id = {str(account.get("id") or ""): (account, session) for account, session in account_sessions}
    if owner_account_id not in by_id:
        raise ValueError("群主账号不存在或没有可用 session")
    owner, owner_session = by_id[owner_account_id]
    entity, invite_link = await _owner_group(
        owner_session,
        api_id,
        api_hash,
        title,
        existing_target,
        proxy,
        client_factory,
    )
    results: list[dict[str, str]] = []
    owner_result = {
        "account_id": owner_account_id,
        "account_name": str(owner.get("name") or owner_account_id),
        "status": "owner",
    }
    results.append(owner_result)
    if on_progress:
        on_progress(owner_result)

    for account, session in account_sessions:
        account_id = str(account.get("id") or "")
        if account_id == owner_account_id:
            continue
        try:
            status = await _join_member(
                session,
                api_id,
                api_hash,
                invite_link,
                proxy,
                client_factory,
            )
            result = {
                "account_id": account_id,
                "account_name": str(account.get("name") or account_id),
                "status": status,
            }
        except Exception as exc:
            result = {
                "account_id": account_id,
                "account_name": str(account.get("name") or account_id),
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
        results.append(result)
        if on_progress:
            on_progress(result)
        if join_delay > 0:
            await asyncio.sleep(join_delay)

    return {
        "title": title,
        "owner_account_id": owner_account_id,
        "invite_link": invite_link,
        "group_id": _group_id(entity),
        "results": results,
    }


def _save_resource_group(path: Path, payload: dict[str, Any]) -> None:
    with resource_group_storage_lock:
        records: list[dict[str, Any]] = []
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    records = [item for item in loaded if isinstance(item, dict)]
            except json.JSONDecodeError:
                records = []
        group_id = str(payload.get("group_id") or "")
        records = [item for item in records if str(item.get("group_id") or "") != group_id]
        records.append({**payload, "updated_at": int(time.time())})
        write_private_text(path, json.dumps(records, ensure_ascii=False, indent=2) + "\n")


def create_resource_group_job(title: str, owner_account_id: str, total: int) -> ResourceGroupJob:
    job = ResourceGroupJob(
        id=uuid.uuid4().hex,
        title=title,
        owner_account_id=owner_account_id,
        total=total,
    )
    with resource_group_jobs_lock:
        resource_group_jobs[job.id] = job
    return job


def get_resource_group_job(job_id: str) -> dict[str, Any] | None:
    with resource_group_jobs_lock:
        job = resource_group_jobs.get(job_id)
        return asdict(job) if job else None


def run_resource_group_job(
    job_id: str,
    account_sessions: list[tuple[dict[str, Any], str]],
    api_id: int,
    api_hash: str,
    existing_target: str,
    proxy,
    storage_path: Path = DEFAULT_RESOURCE_GROUPS,
) -> None:
    def progress(result: dict[str, str]) -> None:
        with resource_group_jobs_lock:
            job = resource_group_jobs[job_id]
            job.results.append(result)
            job.completed += 1
            status = result.get("status")
            if status == "joined":
                job.joined += 1
            elif status in {"already", "owner"}:
                job.already += 1
            elif status == "failed":
                job.failed += 1

    with resource_group_jobs_lock:
        job = resource_group_jobs[job_id]
        title = job.title
        owner_account_id = job.owner_account_id
    try:
        payload = asyncio.run(
            synchronize_resource_group(
                title,
                owner_account_id,
                account_sessions,
                api_id,
                api_hash,
                existing_target,
                proxy,
                on_progress=progress,
            )
        )
        _save_resource_group(storage_path, payload)
        with resource_group_jobs_lock:
            job = resource_group_jobs[job_id]
            job.invite_link = str(payload["invite_link"])
            job.group_id = str(payload["group_id"])
            job.status = "done"
    except Exception as exc:
        with resource_group_jobs_lock:
            job = resource_group_jobs[job_id]
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"


def start_resource_group_job(
    title: str,
    owner_account_id: str,
    account_sessions: list[tuple[dict[str, Any], str]],
    api_id: int,
    api_hash: str,
    existing_target: str,
    proxy,
) -> dict[str, Any]:
    job = create_resource_group_job(title, owner_account_id, len(account_sessions))
    threading.Thread(
        target=run_resource_group_job,
        args=(job.id, account_sessions, api_id, api_hash, existing_target, proxy),
        daemon=True,
    ).start()
    return asdict(job)
