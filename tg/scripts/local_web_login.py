"""QR login state machine with in-memory Telegram 2FA continuation."""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from telethon import TelegramClient, errors
from telethon.sessions import StringSession

from local_web_accounts import (
    append_session,
    load_account_cache,
    make_qr_data_url,
    masked_session,
    parse_proxy,
    profile_from_session,
    save_account_cache,
)


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


login_jobs: dict[str, LoginJob] = {}
password_events: dict[str, threading.Event] = {}
passwords: dict[str, str] = {}
login_lock = threading.Lock()


def create_login_job(job_id: str, role: str) -> LoginJob:
    with login_lock:
        job = LoginJob(id=job_id, role=role)
        login_jobs[job_id] = job
        password_events[job_id] = threading.Event()
        return job


def get_login_job(job_id: str) -> LoginJob | None:
    with login_lock:
        return login_jobs.get(job_id)


def submit_login_password(job_id: str, password: str) -> LoginJob:
    with login_lock:
        job = login_jobs.get(job_id)
        event = password_events.get(job_id)
        if not job or not event or job.status != "needs_password":
            raise ValueError("登录任务不存在或当前不需要密码")
        if not password:
            raise ValueError("请输入两步验证密码")
        passwords[job_id] = password
        job.status = "verifying_password"
        job.error = None
        event.set()
        return job


async def _wait_for_2fa_password(job_id: str, client: TelegramClient, timeout: int = 300) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = password_events[job_id]
        received = await asyncio.to_thread(event.wait, min(max(0.0, deadline - time.monotonic()), 30.0))
        if not received:
            continue
        with login_lock:
            password = passwords.pop(job_id, "")
            event.clear()
        if not password:
            continue
        try:
            await client.sign_in(password=password)
            return
        except errors.PasswordHashInvalidError:
            with login_lock:
                login_jobs[job_id].status = "needs_password"
                login_jobs[job_id].error = "两步验证密码不正确，请重试。"
    raise TimeoutError("等待两步验证密码超时，请重新扫码")


async def qr_login(job_id: str, api_id: int, api_hash: str, out: Path, role: str, timeout: int, proxy_url: str | None) -> None:
    proxy = parse_proxy(proxy_url)
    client = TelegramClient(StringSession(), api_id, api_hash, proxy=proxy)
    await client.connect()
    try:
        qr = await client.qr_login()
        with login_lock:
            login_jobs[job_id].status = "waiting"
            login_jobs[job_id].url = qr.url
            login_jobs[job_id].qr_image = make_qr_data_url(qr.url)
        try:
            await qr.wait(timeout=timeout)
        except errors.SessionPasswordNeededError:
            with login_lock:
                login_jobs[job_id].status = "needs_password"
                login_jobs[job_id].error = None
            await _wait_for_2fa_password(job_id, client)

        session = client.session.save()
        if not session:
            raise RuntimeError("未能生成 session string")
        append_session(out, session)
        fallback = {"telegram_id": "", "username": "", "name": "本地主账号" if role == "main" else "本地小号", "avatar": ""}
        with login_lock:
            login_jobs[job_id].status = "done"
            login_jobs[job_id].account = {"role": role, **fallback, "masked_session": masked_session(session)}
        try:
            profile = await profile_from_session(session, api_id, api_hash, proxy)
        except Exception:
            profile = fallback
        cache = load_account_cache(out)
        cache[session] = {"session": session, "role": role, **profile}
        save_account_cache(out, list(cache.values()))
        with login_lock:
            login_jobs[job_id].account = {"role": role, **profile, "masked_session": masked_session(session)}
    except Exception as exc:
        with login_lock:
            login_jobs[job_id].status = "error"
            login_jobs[job_id].error = f"{type(exc).__name__}: {exc}"
    finally:
        with login_lock:
            passwords.pop(job_id, None)
            password_events.pop(job_id, None)
        await client.disconnect()
