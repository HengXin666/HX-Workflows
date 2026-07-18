"""Account, session, proxy, and QR helpers for the local TG web API."""

from __future__ import annotations

import base64
import json
import os
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import qrcode
import socks
from telethon import TelegramClient
from telethon.sessions import StringSession

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DEFAULT_ALT_OUT = ROOT / "sessions" / "tg_session_strings.txt"
DEFAULT_MAIN_OUT = ROOT / "sessions" / "tg_main_session_string.txt"
DEFAULT_WORKFLOWS = ROOT / "sessions" / "web_workflows.json"
SIGNINS_CONFIG = CONFIG_DIR / "signins.yml"
TASKS_CONFIG = CONFIG_DIR / "tasks.yml"
GITHUB_WORKFLOW = ROOT.parent / ".github" / "workflows" / "tg-orchestrator.yml"
DEFAULT_API_ID = 611335
DEFAULT_API_HASH = "d524b414d21f4d37f08684c1df41ac9c"


def write_private_text(path: Path, content: str) -> None:
    """Atomically replace a credential-adjacent file with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

def resolve_api_id(value: str | None) -> int:
    return int(value or os.environ.get("TG_API_ID") or DEFAULT_API_ID)


def resolve_api_hash(value: str | None) -> str:
    return value or os.environ.get("TG_API_HASH") or DEFAULT_API_HASH


def parse_proxy(proxy_url: str | None):
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    proxy_types = {
        "http": socks.HTTP,
        "socks4": socks.SOCKS4,
        "socks5": socks.SOCKS5,
    }
    if parsed.scheme not in proxy_types:
        raise RuntimeError(f"不支持的代理协议: {parsed.scheme}")
    if not parsed.hostname or not parsed.port:
        raise RuntimeError("代理地址必须包含 host 和 port")
    return (
        proxy_types[parsed.scheme],
        parsed.hostname,
        parsed.port,
        True,
        parsed.username,
        parsed.password,
    )


def make_qr_data_url(url: str) -> str:
    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="#0b1220", back_color="#ffffff")
    buf = BytesIO()
    image.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_lines(path: Path, lines: list[str]) -> None:
    write_private_text(path, "\n".join(lines) + ("\n" if lines else ""))


def append_session(path: Path, session: str) -> None:
    sessions = read_lines(path)
    if session not in sessions:
        sessions.append(session)
        write_lines(path, sessions)


def account_cache_path(path: Path) -> Path:
    return path.with_suffix(".accounts.json")


def load_account_cache(*paths: Path) -> dict[str, dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = {}
    for path in paths:
        meta = account_cache_path(path)
        if meta.exists():
            try:
                items = json.loads(meta.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"账号缓存 JSON 损坏: {meta}") from exc
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                raise RuntimeError(f"账号缓存必须是对象数组: {meta}")
            for item in items:
                session = str(item.get("session") or "")
                if session:
                    cache[session] = item
    return cache


def save_account_cache(path: Path, accounts: list[dict[str, Any]]) -> None:
    meta = account_cache_path(path)
    write_private_text(meta, json.dumps(accounts, ensure_ascii=False, indent=2) + "\n")


async def profile_from_session(session: str, api_id: int, api_hash: str, proxy=None) -> dict[str, Any]:
    client = TelegramClient(StringSession(session), api_id, api_hash, proxy=proxy)
    await client.connect()
    try:
        me = await client.get_me()
        if not me:
            raise RuntimeError("无法读取账号信息")
        avatar_data = ""
        photo = await client.download_profile_photo(me, file=bytes)
        if photo:
            avatar_data = "data:image/jpeg;base64," + base64.b64encode(photo).decode("ascii")
        username = f"@{me.username}" if getattr(me, "username", None) else ""
        full_name = " ".join(x for x in [getattr(me, "first_name", ""), getattr(me, "last_name", "")] if x).strip()
        return {
            "telegram_id": str(me.id),
            "username": username,
            "name": full_name or username or str(me.id),
            "avatar": avatar_data,
        }
    finally:
        await client.disconnect()


def masked_session(session: str) -> str:
    return f"{session[:8]}...{session[-6:]}" if len(session) > 18 else "stored"


def accounts_from_file(path: Path, role: str, cache: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    accounts = []
    for index, session in enumerate(read_lines(path), start=1):
        cached = cache.get(session, {})
        accounts.append(
            {
                "id": f"{role}-{index}",
                "role": role,
                "name": cached.get("name") or ("本地主账号" if role == "main" else f"本地小号 {index}"),
                "username": cached.get("username") or "",
                "telegram_id": cached.get("telegram_id") or "",
                "avatar": cached.get("avatar") or "",
                "source": str(path),
                "masked_session": masked_session(session),
            }
        )
    return accounts


def sessions_with_accounts(alt_out: Path, main_out: Path) -> list[tuple[dict[str, Any], str]]:
    cache = load_account_cache(alt_out, main_out)
    rows: list[tuple[dict[str, Any], str]] = []
    for account in accounts_from_file(main_out, "main", cache):
        sessions = read_lines(Path(account["source"]))
        index = int(account["id"].split("-")[-1]) - 1
        if 0 <= index < len(sessions):
            rows.append((account, sessions[index]))
    for account in accounts_from_file(alt_out, "alt", cache):
        sessions = read_lines(Path(account["source"]))
        index = int(account["id"].split("-")[-1]) - 1
        if 0 <= index < len(sessions):
            rows.append((account, sessions[index]))
    return rows


def session_for_account(account_id: str, alt_out: Path, main_out: Path) -> str:
    for account, session in sessions_with_accounts(alt_out, main_out):
        if account.get("id") == account_id:
            return session
    raise RuntimeError(f"账号不存在或未导入 session: {account_id}")


def iso_date(value: Any) -> str:
    if not value:
        return ""
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)
