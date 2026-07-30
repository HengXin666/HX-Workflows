"""Private multi-bot storage and generic Telegram Bot API orchestration."""

from __future__ import annotations

import json
import http.client
import hashlib
import io
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from bot_checkin import CheckinStore
from local_web_accounts import ROOT, write_private_text

DEFAULT_BOTS = ROOT / "sessions" / "telegram_bots.json"
DEFAULT_BOT_WORKFLOWS = ROOT / "sessions" / "bot_workflows.json"
TOKEN_PATTERN = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")
METHOD_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,79}$")
BOT_WORKFLOW_API_VERSION = 2
live_debug_sessions: dict[str, dict[str, Any]] = {}
live_debug_lock = threading.Lock()


def _read_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"机器人配置 JSON 损坏: {path}") from exc
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RuntimeError(f"机器人配置必须是对象数组: {path}")
    return value


def _write_list(path: Path, items: list[dict[str, Any]]) -> None:
    write_private_text(path, json.dumps(items, ensure_ascii=False, indent=2) + "\n")


def public_bot(bot: dict[str, Any]) -> dict[str, Any]:
    token = str(bot.get("token") or "")
    return {key: value for key, value in bot.items() if key != "token"} | {
        "token_hint": f"{token[:5]}…{token[-4:]}" if token else "",
    }


def list_bots(path: Path = DEFAULT_BOTS) -> list[dict[str, Any]]:
    return [public_bot(bot) for bot in _read_list(path)]


def _bot_token(path: Path, bot_id: str) -> str:
    bot = next((item for item in _read_list(path) if str(item.get("id")) == bot_id), None)
    if not bot:
        raise ValueError("机器人不存在")
    return str(bot.get("token") or "")


def call_bot_api(token: str, method: str, params: dict[str, Any], timeout: int = 30) -> Any:
    if not TOKEN_PATTERN.fullmatch(token):
        raise ValueError("Bot Token 格式无效")
    if not METHOD_PATTERN.fullmatch(method):
        raise ValueError("Bot API method 格式无效")
    data = json.dumps(params, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "HX-TG-Workflow/1.0"},
    )
    try:
        proxy_url = os.environ.get("TG_PROXY", "").strip()
        if proxy_url:
            proxy = urllib.parse.urlparse(proxy_url)
            connection = http.client.HTTPSConnection(proxy.hostname, proxy.port, timeout=timeout)
            connection.set_tunnel("api.telegram.org", 443)
            connection.request("POST", f"/bot{token}/{method}", body=data, headers=dict(request.headers))
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
        else:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except json.JSONDecodeError:
            raise RuntimeError(f"Telegram Bot API HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"Telegram Bot API 连接失败: {getattr(exc, 'reason', exc)}") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError(str(payload.get("description") if isinstance(payload, dict) else "Bot API 返回异常"))
    return payload.get("result")


def call_bot_api_file(token: str, method: str, params: dict[str, Any], field: str, filename: str, content: bytes, content_type: str, timeout: int = 30) -> Any:
    boundary = f"----HXBot{uuid.uuid4().hex}"
    body = bytearray()
    for key, value in params.items():
        encoded = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, bool)) else str(value)
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{encoded}\r\n".encode())
    body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n".encode())
    body.extend(content)
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": "HX-TG-Workflow/1.0"}
    proxy_url = os.environ.get("TG_PROXY", "").strip()
    try:
        if proxy_url:
            proxy = urllib.parse.urlparse(proxy_url)
            connection = http.client.HTTPSConnection(proxy.hostname, proxy.port, timeout=timeout)
            connection.set_tunnel("api.telegram.org", 443)
            connection.request("POST", f"/bot{token}/{method}", body=bytes(body), headers=headers)
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
        else:
            request = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=bytes(body), method="POST", headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"Telegram Bot API 文件上传失败: {getattr(exc, 'reason', exc)}") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError(str(payload.get("description") if isinstance(payload, dict) else "Bot API 返回异常"))
    return payload.get("result")


def add_bot(path: Path, token: str, label: str = "") -> dict[str, Any]:
    profile = call_bot_api(token.strip(), "getMe", {})
    if not isinstance(profile, dict) or not profile.get("id"):
        raise RuntimeError("getMe 未返回机器人资料")
    bot_id = str(profile["id"])
    items = [item for item in _read_list(path) if str(item.get("id")) != bot_id]
    bot = {
        "id": bot_id,
        "label": label.strip() or profile.get("first_name") or profile.get("username") or bot_id,
        "username": f"@{profile.get('username')}" if profile.get("username") else "",
        "name": profile.get("first_name") or "",
        "token": token.strip(),
    }
    items.append(bot)
    _write_list(path, items)
    return public_bot(bot)


def delete_bot(path: Path, bot_id: str) -> bool:
    items = _read_list(path)
    remaining = [item for item in items if str(item.get("id")) != bot_id]
    if len(remaining) == len(items):
        return False
    _write_list(path, remaining)
    return True


def list_bot_workflows(path: Path = DEFAULT_BOT_WORKFLOWS) -> list[dict[str, Any]]:
    workflows: list[dict[str, Any]] = []
    for item in _read_list(path):
        blueprint = item.get("blueprint")
        if not isinstance(blueprint, dict):
            continue
        workflows.append({
            "api_version": BOT_WORKFLOW_API_VERSION,
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or item.get("id") or "机器人编排"),
            "bot_id": str(item.get("bot_id") or ""),
            "blueprint": blueprint,
        })
    return workflows


def validate_bot_blueprint(blueprint: Any) -> dict[str, Any]:
    if not isinstance(blueprint, dict) or not isinstance(blueprint.get("nodes"), list) or not isinstance(blueprint.get("edges"), list):
        raise ValueError("blueprint 必须包含 nodes 和 edges 数组")
    nodes = blueprint["nodes"]
    edges = blueprint["edges"]
    if len(nodes) > 200 or len(edges) > 400:
        raise ValueError("蓝图最多允许 200 个节点和 400 条连线")

    node_ids: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ValueError(f"节点 {index + 1} 必须是对象")
        node_id = str(node.get("id") or "").strip()
        data = node.get("data")
        if not node_id or node_id in node_ids:
            raise ValueError(f"节点 {index + 1} 的 id 为空或重复")
        if not isinstance(data, dict) or not str(data.get("kind") or "").strip():
            raise ValueError(f"节点 {node_id} 缺少 data.kind")
        if not isinstance(data.get("config"), dict):
            raise ValueError(f"节点 {node_id} 缺少 config 对象")
        node_ids.add(node_id)

    edge_ids: set[str] = set()
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise ValueError(f"连线 {index + 1} 必须是对象")
        edge_id = str(edge.get("id") or "").strip()
        source = str(edge.get("source") or "").strip()
        target = str(edge.get("target") or "").strip()
        if not edge_id or edge_id in edge_ids:
            raise ValueError(f"连线 {index + 1} 的 id 为空或重复")
        if source not in node_ids or target not in node_ids:
            raise ValueError(f"连线 {edge_id} 引用了不存在的节点")
        edge_ids.add(edge_id)
    return blueprint


def save_bot_workflow(path: Path, workflow: dict[str, Any]) -> dict[str, Any]:
    workflow_id = str(workflow.get("id") or f"bot-flow-{uuid.uuid4().hex[:10]}").strip()
    name = str(workflow.get("name") or workflow_id).strip()
    if "steps" in workflow:
        raise ValueError("机器人编排 API v2 已移除 steps，请提交 blueprint")
    blueprint = validate_bot_blueprint(workflow.get("blueprint"))
    saved = {
        "api_version": BOT_WORKFLOW_API_VERSION,
        "id": workflow_id,
        "name": name,
        "bot_id": str(workflow.get("bot_id") or ""),
        "blueprint": blueprint,
    }
    items = [item for item in _read_list(path) if str(item.get("id")) != workflow_id]
    items.append(saved)
    _write_list(path, items)
    return saved


def delete_bot_workflow(path: Path, workflow_id: str) -> bool:
    items = _read_list(path)
    remaining = [item for item in items if str(item.get("id")) != workflow_id]
    if len(remaining) == len(items):
        return False
    _write_list(path, remaining)
    return True


def _debug_branch_index(node: dict[str, Any], debug_input: dict[str, Any]) -> int:
    data = node.get("data") or {}
    branches = data.get("branches") or []
    overrides = debug_input.get("branch_overrides") or {}
    override = overrides.get(str(node.get("id"))) if isinstance(overrides, dict) else None
    if isinstance(override, int) and 0 <= override < len(branches):
        return override
    if isinstance(override, str) and override in branches:
        return branches.index(override)

    kind = str(data.get("kind") or "")
    config = data.get("config") or {}
    if kind == "menu":
        callback_data = str(debug_input.get("callback_data") or "")
        buttons = config.get("buttons") or []
        if isinstance(buttons, list):
            for index, button in enumerate(buttons):
                if isinstance(button, dict) and str(button.get("callback_data") or "") == callback_data:
                    return min(index, max(0, len(branches) - 1))
    if kind == "condition":
        expression = str(config.get("expression") or "")
        if "checked_in_today" in expression:
            checked = bool((debug_input.get("user") or {}).get("checked_in_today"))
            result = not checked if "false" in expression.lower() else checked
            return 0 if result else min(1, max(0, len(branches) - 1))
        results = debug_input.get("condition_results") or {}
        result = results.get(str(node.get("id")), debug_input.get("condition_result", True)) if isinstance(results, dict) else True
        return 0 if bool(result) else min(1, max(0, len(branches) - 1))
    if kind == "checkin":
        return {"correct": 0, "wrong": 1, "expired": 2}.get(str(debug_input.get("checkin_result") or "correct"), 0)
    if kind == "payment_event":
        return 0 if bool(debug_input.get("payment_success", True)) else min(1, max(0, len(branches) - 1))
    if kind == "subscription":
        return {"active": 0, "expired": 1, "missing": 2}.get(str(debug_input.get("subscription_status") or "active"), 0)
    if kind == "random":
        weights = config.get("weights") or []
        if isinstance(weights, list) and weights:
            return max(range(min(len(weights), len(branches))), key=lambda index: float(weights[index] or 0))
    return 0


def _debug_summary(kind: str, config: dict[str, Any]) -> str:
    summaries = {
        "command": f"匹配命令：{', '.join(map(str, config.get('commands') or []))}",
        "message": "接收并标准化 Telegram 消息",
        "callback": f"匹配按钮回调：{config.get('pattern', '')}",
        "send": "模拟 sendMessage；调试模式不会向 Telegram 发送消息",
        "menu": f"生成 {len(config.get('buttons') or [])} 个 Inline Keyboard 按钮",
        "condition": f"计算条件：{config.get('expression', '')}",
        "checkin": "模拟验证码签到与 SQLite 事务",
        "database": f"模拟 SQLite {config.get('operation', '')} · {config.get('table', '')}",
        "invoice": f"模拟 sendInvoice：{config.get('price_xtr', 0)} XTR",
        "payment_event": "模拟预结账校验、successful_payment 与幂等入账",
        "subscription": f"模拟查询订阅套餐：{config.get('plan', '')}",
        "http": f"模拟 HTTP {config.get('method', '')} {config.get('url', '')}",
        "api": f"模拟 Telegram Bot API：{config.get('method', '')}",
        "delay": f"跳过真实等待，记录计划延迟 {config.get('seconds', 0)} 秒",
        "end": "结束当前调试链路",
    }
    return summaries.get(kind, f"模拟执行 {kind} 节点")


def debug_bot_workflow(workflow: dict[str, Any], debug_input: dict[str, Any]) -> dict[str, Any]:
    blueprint = validate_bot_blueprint(workflow.get("blueprint"))
    nodes = {str(node["id"]): node for node in blueprint["nodes"]}
    outgoing: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in nodes}
    indegree = {node_id: 0 for node_id in nodes}
    for edge in blueprint["edges"]:
        outgoing[str(edge["source"])].append(edge)
        indegree[str(edge["target"])] += 1
    trigger_kind = str(debug_input.get("trigger_kind") or "")
    triggers = [node for node in nodes.values() if str((node.get("data") or {}).get("category") or "") == "触发"]
    if trigger_kind:
        triggers = [node for node in triggers if str((node.get("data") or {}).get("kind") or "") == trigger_kind]
    roots = triggers or [nodes[node_id] for node_id, degree in indegree.items() if degree == 0]
    if not roots:
        raise ValueError("蓝图没有可运行的触发入口")

    current = roots[0]
    visits: dict[str, int] = {}
    logs: list[dict[str, Any]] = []
    max_steps = min(max(int(debug_input.get("max_steps") or 50), 1), 100)
    halted = False
    for sequence in range(1, max_steps + 1):
        node_id = str(current["id"])
        visits[node_id] = visits.get(node_id, 0) + 1
        if visits[node_id] > 2:
            logs.append({"sequence": sequence, "node_id": node_id, "label": str(current.get("data", {}).get("label") or node_id), "kind": str(current.get("data", {}).get("kind") or ""), "status": "halted", "summary": "检测到循环节点超过 2 次，调试器已停止以避免死循环"})
            halted = True
            break
        data = current.get("data") or {}
        kind = str(data.get("kind") or "")
        config = data.get("config") if isinstance(data.get("config"), dict) else {}
        branches = data.get("branches") if isinstance(data.get("branches"), list) else []
        branch_index = _debug_branch_index(current, debug_input) if branches else None
        branch = str(branches[branch_index]) if branch_index is not None and branch_index < len(branches) else None
        logs.append({
            "sequence": sequence,
            "node_id": node_id,
            "label": str(data.get("label") or node_id),
            "kind": kind,
            "status": "done",
            "summary": _debug_summary(kind, config),
            "branch": branch,
            "mode": "simulation",
        })
        candidates = outgoing[node_id]
        if not candidates or kind == "end":
            break
        if branch_index is not None:
            selected = next((edge for edge in candidates if edge.get("sourceHandle") == f"branch-{branch_index}"), None)
            if selected is None:
                logs.append({"sequence": sequence + 1, "node_id": node_id, "label": "分支未连接", "kind": "router", "status": "halted", "summary": f"出口“{branch}”没有连接下游节点"})
                halted = True
                break
        else:
            selected = candidates[0]
        current = nodes[str(selected["target"])]
    else:
        halted = True
    return {"status": "halted" if halted else "done", "mode": "simulation", "logs": logs, "visited_nodes": [log["node_id"] for log in logs if log["kind"] != "router"]}


def _live_public(session: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in session.items() if key not in {"token", "workflow", "stop_event", "thread", "challenges", "db_path"}}


def _live_log(session: dict[str, Any], level: str, event: str, message: str, **details: Any) -> None:
    with live_debug_lock:
        session["logs"].append({"sequence": len(session["logs"]) + 1, "time": time.strftime("%H:%M:%S"), "level": level, "event": event, "message": message, "details": details})
        session["logs"] = session["logs"][-500:]


def _live_render(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _live_render(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_live_render(item, context) for item in value]
    if not isinstance(value, str):
        return value
    def replace(match: re.Match[str]) -> str:
        current: Any = context
        for part in match.group(1).split("."):
            current = current.get(part, "") if isinstance(current, dict) else ""
        return str(current)
    return re.sub(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}", replace, value)


def _menu_callback_id(node_id: str, branch_index: int) -> str:
    digest = hashlib.sha256(node_id.encode()).hexdigest()[:12]
    return f"hx:{digest}:{branch_index}"


def _checkin_callback_id(session_id: str, challenge_ref: str, choice: int) -> str:
    return f"debug-checkin:{session_id[-8:]}:{challenge_ref[:8]}:{choice}"


def _workflow_commands(workflow: dict[str, Any]) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    seen: set[str] = set()
    for node in workflow.get("blueprint", {}).get("nodes", []):
        data = node.get("data") or {}
        if data.get("kind") != "command":
            continue
        for raw in (data.get("config") or {}).get("commands") or []:
            command = str(raw).lstrip("/").lower()
            if not re.fullmatch(r"[a-z0-9_]{1,32}", command) or command in seen:
                continue
            description = str(data.get("label") or data.get("detail") or command).lstrip("/")[:256] or command
            commands.append({"command": command, "description": description})
            seen.add(command)
    for node in workflow.get("blueprint", {}).get("nodes", []):
        data = node.get("data") or {}
        if data.get("kind") != "menu":
            continue
        for button in (data.get("config") or {}).get("buttons") or []:
            label = str(button.get("label") or "") if isinstance(button, dict) else str(button)
            command = _menu_command(label)
            if command and command not in seen:
                commands.append({"command": command, "description": label[:256]})
                seen.add(command)
    return commands[:100]


def _menu_command(label: str) -> str:
    for keyword, command in (("签到", "checkin"), ("排行", "ranking"), ("邀请", "invite"), ("商店", "shop"), ("菜单", "menu"), ("帮助", "help")):
        if keyword in label:
            return command
    slug = re.sub(r"[^a-z0-9_]+", "_", label.lower()).strip("_")
    return slug[:32] if re.fullmatch(r"[a-z0-9_]{1,32}", slug) else ""


def _captcha_png(expression: str) -> bytes:
    rng = random.SystemRandom()
    canvas = Image.new("RGB", (520, 170), (rng.randint(220, 245), rng.randint(225, 248), rng.randint(225, 248)))
    draw = ImageDraw.Draw(canvas)
    font_path = "/usr/share/fonts/noto/NotoSans-Regular.ttf"
    font = ImageFont.truetype(font_path, 56) if Path(font_path).exists() else ImageFont.load_default()
    x = 28
    for char in expression:
        glyph = Image.new("RGBA", (70, 90), (0, 0, 0, 0))
        glyph_draw = ImageDraw.Draw(glyph)
        glyph_draw.text((8, 5), char, font=font, fill=(rng.randint(15, 80), rng.randint(20, 95), rng.randint(30, 110), 255), stroke_width=1)
        glyph = glyph.rotate(rng.uniform(-22, 22), resample=Image.Resampling.BICUBIC, expand=False)
        canvas.paste(glyph, (x, rng.randint(34, 58)), glyph)
        x += rng.randint(43, 49)
    for _ in range(2):
        points = [(rng.randint(0, 520), rng.randint(0, 170)) for _ in range(4)]
        draw.line(points, fill=(rng.randint(80, 180), rng.randint(80, 180), rng.randint(80, 180)), width=1)
    for _ in range(450):
        x, y = rng.randrange(520), rng.randrange(170)
        draw.point((x, y), fill=(rng.randrange(256), rng.randrange(256), rng.randrange(256)))
    canvas = canvas.transform(canvas.size, Image.Transform.AFFINE, (1, rng.uniform(-0.08, 0.08), 0, rng.uniform(-0.04, 0.04), 1, 0), resample=Image.Resampling.BICUBIC)
    canvas = canvas.filter(ImageFilter.GaussianBlur(0.35))
    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _live_execute(session: dict[str, Any], start_id: str, context: dict[str, Any], start_branch: int | None = None) -> None:
    blueprint = session["workflow"]["blueprint"]
    nodes = {str(node["id"]): node for node in blueprint["nodes"]}
    outgoing: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in nodes}
    for edge in blueprint["edges"]:
        outgoing[str(edge["source"])].append(edge)
    node_id = start_id
    branch_override = start_branch
    visits: dict[str, int] = {}
    for _ in range(60):
        node = nodes[node_id]
        data = node.get("data") or {}
        kind = str(data.get("kind") or "")
        label = str(data.get("label") or node_id)
        config = data.get("config") if isinstance(data.get("config"), dict) else {}
        visits[node_id] = visits.get(node_id, 0) + 1
        if visits[node_id] > 2:
            _live_log(session, "error", "guard", f"节点 {label} 重复超过 2 次，已阻止死循环", node_id=node_id)
            return
        _live_log(session, "info", "node", f"进入节点：{label}", node_id=node_id, kind=kind)
        chat_id = context.get("chat_id")
        if kind == "send" and chat_id:
            result = call_bot_api(session["token"], "sendMessage", {"chat_id": chat_id, "text": _live_render(config.get("text", ""), context), "parse_mode": config.get("parse_mode", "HTML")})
            _live_log(session, "success", "api", "sendMessage 调用成功", node_id=node_id, message_id=result.get("message_id") if isinstance(result, dict) else None)
        elif kind == "menu" and chat_id:
            buttons = config.get("buttons") or []
            keyboard = [[{"text": str(button.get("label") or "按钮"), "callback_data": _menu_callback_id(node_id, index)}] for index, button in enumerate(buttons) if isinstance(button, dict)]
            call_bot_api(session["token"], "sendMessage", {"chat_id": chat_id, "text": _live_render(config.get("text", "请选择"), context), "reply_markup": {"inline_keyboard": keyboard}})
            _live_log(session, "success", "wait", f"菜单已发送，等待你在 Telegram 点击（{len(keyboard)} 个按钮）", node_id=node_id)
            return
        elif kind == "invoice" and chat_id:
            call_bot_api(session["token"], "sendInvoice", {"chat_id": chat_id, "title": str(config.get("title") or "商品"), "description": str(config.get("description") or "商品说明"), "payload": str(config.get("payload") or node_id), "currency": "XTR", "prices": [{"label": str(config.get("title") or "商品"), "amount": int(config.get("price_xtr") or 1)}]})
            _live_log(session, "success", "wait", "Stars 发票已真实发送，等待支付事件", node_id=node_id)
            return
        elif kind == "checkin" and chat_id:
            store = CheckinStore(Path(session["db_path"]))
            challenge = store.create_challenge(int(context["user"]["id"]), int(chat_id), ttl=int(config.get("challenge_ttl_seconds") or 120), difficulty=str(config.get("difficulty") or "normal"))
            store.close()
            challenge_ref = challenge.id[:8]
            session["challenges"][challenge_ref] = {"db_id": challenge.id, "node_id": node_id, "user_id": context.get("user", {}).get("id"), "reward_points": int(config.get("reward_points") or 10)}
            keyboard = [[{"text": str(choice), "callback_data": _checkin_callback_id(session["id"], challenge_ref, choice)} for choice in challenge.options]]
            call_bot_api_file(session["token"], "sendPhoto", {"chat_id": chat_id, "caption": "请选择图片中计算题的正确答案", "reply_markup": {"inline_keyboard": keyboard}}, "photo", "captcha.png", _captcha_png(challenge.question), "image/png")
            _live_log(session, "success", "wait", f"已发送新的 {config.get('difficulty', 'normal')} 难度图片验证码", node_id=node_id, challenge_id=challenge.id)
            return
        elif kind == "api":
            params = config.get("params", {})
            if isinstance(params, str):
                params = json.loads(params or "{}")
            call_bot_api(session["token"], str(config.get("method") or "getMe"), _live_render(params, context))
            _live_log(session, "success", "api", f"{config.get('method')} 调用成功", node_id=node_id)
        elif kind == "http":
            _live_log(session, "warning", "skip", "实时调试暂不调用外部 HTTP，避免非预期写入", node_id=node_id)
        elif kind == "database":
            _live_log(session, "info", "database", f"调试数据库动作：{config.get('operation', '')} {config.get('table', '')}", node_id=node_id)
        elif kind == "delay":
            seconds = min(float(config.get("seconds") or 0), 2.0)
            if seconds > 0:
                time.sleep(seconds)
            _live_log(session, "info", "delay", f"等待 {seconds:g} 秒（调试上限 2 秒）", node_id=node_id)
        elif kind == "end":
            _live_log(session, "success", "end", "流程结束", node_id=node_id)
            return
        edges = outgoing[node_id]
        if not edges:
            _live_log(session, "success", "end", "当前分支执行完成", node_id=node_id)
            return
        branches = data.get("branches") if isinstance(data.get("branches"), list) else []
        branch_index = branch_override if branch_override is not None else (_debug_branch_index(node, context) if branches else None)
        branch_override = None
        selected = next((edge for edge in edges if edge.get("sourceHandle") == f"branch-{branch_index}"), None) if branch_index is not None else edges[0]
        if selected is None:
            _live_log(session, "warning", "route", "选中的分支没有连接下游节点", node_id=node_id, branch_index=branch_index)
            return
        node_id = str(selected["target"])


def _live_handle_update(session: dict[str, Any], update: dict[str, Any]) -> None:
    workflow = session["workflow"]
    nodes = workflow["blueprint"]["nodes"]
    message = update.get("message") or {}
    callback = update.get("callback_query") or {}
    pre_checkout = update.get("pre_checkout_query") or {}
    user = message.get("from") or callback.get("from") or pre_checkout.get("from") or {}
    chat = message.get("chat") or (callback.get("message") or {}).get("chat") or {}
    store = CheckinStore(Path(session["db_path"]))
    store.upsert_user(int(user.get("id") or 0), str(user.get("username") or ""), str(user.get("first_name") or ""))
    stats = store.user_stats(int(user.get("id") or 0))
    checked_in_today = store.checked_in_today(int(user.get("id") or 0))
    store.close()
    user = {"points": stats["points"], "streak": stats["days"], "referral_count": 0, "rank": 0, "checked_in_today": checked_in_today, **user}
    context = {"user": user, "bot": {"username": session["bot_username"].lstrip("@")}, "ranking": {"lines": "暂无排行数据"}, "selected_item": {"missing_points": 0, "price": 0, "name": ""}, "chat": chat, "chat_id": chat.get("id"), "message": message, "callback_data": callback.get("data", ""), "condition_result": True, "checkin_result": "correct", "payment_success": True, "subscription_status": "active"}
    update_type = "pre_checkout_query" if pre_checkout else "callback_query" if callback else "message"
    _live_log(session, "info", "update", f"收到 Telegram {update_type}", update_id=update.get("update_id"), user_id=user.get("id"), chat_id=chat.get("id"))
    if pre_checkout:
        call_bot_api(session["token"], "answerPreCheckoutQuery", {"pre_checkout_query_id": pre_checkout["id"], "ok": True})
        _live_log(session, "success", "payment", "预结账请求已在 10 秒内确认", payload=pre_checkout.get("invoice_payload"))
        return
    if message.get("successful_payment"):
        payment_nodes = [node for node in nodes if (node.get("data") or {}).get("kind") == "payment_event"]
        if payment_nodes:
            _live_execute(session, str(payment_nodes[0]["id"]), context, 0)
        return
    if callback:
        value = str(callback.get("data") or "")
        if value.startswith(f"debug-checkin:{session['id'][-8:]}:"):
            _, _, challenge_ref, choice = value.split(":", 3)
            challenge = session["challenges"].pop(challenge_ref, None)
            if not challenge or int(challenge.get("user_id") or 0) != int(user.get("id") or 0):
                call_bot_api(session["token"], "answerCallbackQuery", {"callback_query_id": callback["id"], "text": "题目无效或不属于你", "show_alert": True})
                _live_log(session, "warning", "checkin", "拒绝了无效的签到答案", challenge_id=challenge_ref)
                return
            store = CheckinStore(Path(session["db_path"]))
            challenge_id = str(challenge["db_id"])
            state = store.verify(challenge_id, int(user.get("id") or 0), int(choice), points=int(challenge.get("reward_points") or 10))
            stats = store.user_stats(int(user.get("id") or 0))
            store.close()
            call_bot_api(session["token"], "answerCallbackQuery", {"callback_query_id": callback["id"], "text": {"success": "签到成功", "already": "今天已经签到", "wrong": "回答错误，正在生成新题", "expired": "题目已过期，正在生成新题"}[state]})
            _live_log(session, "success" if state in {"success", "already"} else "warning", "checkin", f"签到验证结果：{state}", challenge_id=challenge_id)
            context["user"] = {**context.get("user", {}), "points": stats["points"], "streak": stats["days"], "checked_in_today": state in {"success", "already"}}
            branch_index = 0 if state in {"success", "already"} else 1 if state == "wrong" else 2
            selected = next((edge for edge in workflow["blueprint"]["edges"] if str(edge.get("source")) == str(challenge["node_id"]) and edge.get("sourceHandle") == f"branch-{branch_index}"), None)
            if selected:
                _live_execute(session, str(selected["target"]), context)
            return
        call_bot_api(session["token"], "answerCallbackQuery", {"callback_query_id": callback["id"]})
        for node in nodes:
            data = node.get("data") or {}
            buttons = (data.get("config") or {}).get("buttons") or []
            for index, button in enumerate(buttons if isinstance(buttons, list) else []):
                if isinstance(button, dict) and _menu_callback_id(str(node["id"]), index) == value:
                    _live_log(session, "success", "callback", f"按钮命中：{button.get('label')}", node_id=node["id"], callback_data=value)
                    selected = next((edge for edge in workflow["blueprint"]["edges"] if str(edge.get("source")) == str(node["id"]) and edge.get("sourceHandle") == f"branch-{index}"), None)
                    if selected:
                        _live_execute(session, str(selected["target"]), context)
                    else:
                        _live_log(session, "warning", "route", f"按钮“{button.get('label')}”没有连接下游节点", node_id=node["id"])
                    return
        _live_log(session, "warning", "callback", f"没有节点处理 callback_data={value}")
        return
    text = str(message.get("text") or "").split("@")[0]
    if text.startswith("/start inv_"):
        inviter_id = text.removeprefix("/start inv_").split(maxsplit=1)[0]
        if inviter_id.isdigit():
            context["inviter_id"] = int(inviter_id)
            _live_log(session, "success", "invite", f"识别邀请参数 inv_{inviter_id}", inviter_id=int(inviter_id), invited_user_id=user.get("id"))
    requested_command = text.split(maxsplit=1)[0].lstrip("/").lower()
    for node in nodes:
        data = node.get("data") or {}
        if data.get("kind") != "menu":
            continue
        for index, button in enumerate((data.get("config") or {}).get("buttons") or []):
            label = str(button.get("label") or "") if isinstance(button, dict) else str(button)
            if _menu_command(label) == requested_command:
                selected = next((edge for edge in workflow["blueprint"]["edges"] if str(edge.get("source")) == str(node["id"]) and edge.get("sourceHandle") == f"branch-{index}"), None)
                if selected:
                    _live_log(session, "success", "command", f"快捷命令 /{requested_command} 直达菜单项：{label}")
                    _live_execute(session, str(selected["target"]), context)
                    return
    for node in nodes:
        data = node.get("data") or {}
        config = data.get("config") or {}
        if data.get("kind") == "command" and text.split(maxsplit=1)[0] in (config.get("commands") or []):
            _live_execute(session, str(node["id"]), context)
            return
    _live_log(session, "warning", "trigger", f"没有命令入口匹配：{text or '(非文本消息)'}")


def _live_debug_worker(session: dict[str, Any]) -> None:
    offset = 0
    try:
        while not session["stop_event"].is_set():
            updates = call_bot_api(session["token"], "getUpdates", {"offset": offset, "timeout": 3, "allowed_updates": ["message", "callback_query", "pre_checkout_query"]}, timeout=8)
            for update in updates if isinstance(updates, list) else []:
                offset = int(update["update_id"]) + 1
                try:
                    _live_handle_update(session, update)
                except Exception as exc:
                    _live_log(session, "error", "runtime", f"{type(exc).__name__}: {exc}", update_id=update.get("update_id"))
    except Exception as exc:
        _live_log(session, "error", "polling", f"轮询停止：{type(exc).__name__}: {exc}")
        session["status"] = "error"
    finally:
        if session["status"] != "error":
            session["status"] = "stopped"
        _live_log(session, "info", "session", "实时调试会话已停止")


def start_live_debug(bots_path: Path, workflow: dict[str, Any]) -> dict[str, Any]:
    validate_bot_blueprint(workflow.get("blueprint"))
    bot_id = str(workflow.get("bot_id") or "")
    token = _bot_token(bots_path, bot_id)
    webhook = call_bot_api(token, "getWebhookInfo", {})
    if isinstance(webhook, dict) and webhook.get("url"):
        raise ValueError("当前机器人已设置 Webhook，Telegram 不允许同时使用 getUpdates；请先停止部署或移除 Webhook")
    with live_debug_lock:
        if any(item.get("bot_id") == bot_id and item.get("status") == "running" for item in live_debug_sessions.values()):
            raise ValueError("这个机器人已经有一个实时调试会话")
    profile = call_bot_api(token, "getMe", {})
    commands = _workflow_commands(workflow)
    if commands:
        call_bot_api(token, "setMyCommands", {"commands": commands})
    session_id = f"bot-debug-{uuid.uuid4().hex[:10]}"
    db_path = ROOT / "sessions" / f"bot_debug_{bot_id}_{workflow.get('id', 'draft')}.sqlite3"
    session: dict[str, Any] = {"id": session_id, "bot_id": bot_id, "bot_username": f"@{profile.get('username')}" if isinstance(profile, dict) and profile.get("username") else "", "status": "running", "created_at": time.time(), "logs": [], "token": token, "workflow": workflow, "stop_event": threading.Event(), "challenges": {}, "db_path": str(db_path)}
    thread = threading.Thread(target=_live_debug_worker, args=(session,), daemon=True, name=session_id)
    session["thread"] = thread
    with live_debug_lock:
        live_debug_sessions[session_id] = session
    _live_log(session, "success", "session", f"已连接 {session['bot_username']}，请到 Telegram 手动操作")
    _live_log(session, "success", "commands", f"已同步 {len(commands)} 个快捷命令到 Telegram 命令栏", commands=[item["command"] for item in commands])
    thread.start()
    return _live_public(session)


def stop_live_debug(session_id: str) -> dict[str, Any]:
    session = live_debug_sessions.get(session_id)
    if not session:
        raise ValueError("实时调试会话不存在")
    session["stop_event"].set()
    session["status"] = "stopping"
    return _live_public(session)


def bot_route_get(path: str, bots_path: Path, workflows_path: Path) -> dict[str, Any] | None:
    if path == "/api/bots":
        return {"bots": list_bots(bots_path)}
    if path == "/api/bot-workflows":
        return {"workflows": list_bot_workflows(workflows_path)}
    if path.startswith("/api/bot-workflows/live-debug/"):
        session_id = urllib.parse.unquote(path.removeprefix("/api/bot-workflows/live-debug/"))
        session = live_debug_sessions.get(session_id)
        if not session:
            return {"session": {"id": session_id, "bot_id": "", "bot_username": "", "status": "stopped", "created_at": 0, "logs": [{"sequence": 1, "time": time.strftime("%H:%M:%S"), "level": "warning", "event": "session", "message": "后端已重启，请重新启动真人调试", "details": {}}]}}
        return {"session": _live_public(session)}
    return None


def bot_route_post(path: str, body: dict[str, Any], bots_path: Path, workflows_path: Path) -> dict[str, Any] | None:
    if path == "/api/bots":
        return {"bot": add_bot(bots_path, str(body.get("token") or ""), str(body.get("label") or ""))}
    if path == "/api/bot-workflows":
        return {"workflow": save_bot_workflow(workflows_path, body)}
    if path == "/api/bot-workflows/live-debug/start":
        workflow = body.get("workflow")
        if not isinstance(workflow, dict):
            raise ValueError("workflow 必须是对象")
        return {"session": start_live_debug(bots_path, workflow)}
    if path.startswith("/api/bot-workflows/live-debug/") and path.endswith("/stop"):
        session_id = urllib.parse.unquote(path.removeprefix("/api/bot-workflows/live-debug/").removesuffix("/stop"))
        return {"session": stop_live_debug(session_id)}
    return None


def bot_route_delete(path: str, bots_path: Path, workflows_path: Path) -> dict[str, Any] | None:
    if path.startswith("/api/bots/"):
        bot_id = urllib.parse.unquote(path.removeprefix("/api/bots/"))
        return {"deleted": delete_bot(bots_path, bot_id), "id": bot_id}
    if path.startswith("/api/bot-workflows/"):
        workflow_id = urllib.parse.unquote(path.removeprefix("/api/bot-workflows/"))
        return {"deleted": delete_bot_workflow(workflows_path, workflow_id), "id": workflow_id}
    return None
