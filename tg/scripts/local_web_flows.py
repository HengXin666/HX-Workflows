"""Pure workflow compilation and persistence for the local TG web app."""

from __future__ import annotations

import json
import math
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from local_web_accounts import GITHUB_WORKFLOW, ROOT, SIGNINS_CONFIG, TASKS_CONFIG


def _bounded_number(
    data: dict[str, Any],
    key: str,
    default: int | float,
    minimum: int | float,
    maximum: int | float,
    *,
    integer: bool = False,
) -> int | float:
    raw = data.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"{key} 必须是数字")
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f"{key} 必须是有限数字")
    if integer and not value.is_integer():
        raise ValueError(f"{key} 必须是整数")
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} 必须在 {minimum}..{maximum} 之间")
    return int(value) if integer else value


def _compile_continuous_request(data: dict[str, Any], node_id: str) -> dict[str, Any]:
    request_mode_raw = data.get("requestMode", "message")
    if not isinstance(request_mode_raw, str):
        raise ValueError("requestMode 必须是 message 或 button")
    request_mode = request_mode_raw.strip()
    if request_mode not in {"message", "button"}:
        raise ValueError("requestMode 必须是 message 或 button")
    request_text_raw = data.get("requestText")
    request_text = request_text_raw.strip() if isinstance(request_text_raw, str) else ""
    if not request_text:
        raise ValueError("requestText 不能为空")

    repeat_count = int(_bounded_number(data, "repeatCount", 1, 1, 1000, integer=True))
    max_failures = int(
        _bounded_number(data, "maxFailures", repeat_count, 1, repeat_count, integer=True)
    )
    save_as_raw = data.get("saveAs", "resource_responses")
    links_save_as_raw = data.get("linksSaveAs", "resource_records")
    save_as = save_as_raw.strip() if isinstance(save_as_raw, str) else ""
    links_save_as = links_save_as_raw.strip() if isinstance(links_save_as_raw, str) else ""
    if not save_as:
        raise ValueError("saveAs 不能为空")
    if not links_save_as:
        raise ValueError("linksSaveAs 不能为空")

    return {
        "type": "continuous_request",
        "node_id": node_id,
        "request_mode": request_mode,
        "request_text": request_text,
        "repeat_count": repeat_count,
        "interval_seconds": float(_bounded_number(data, "intervalSeconds", 3, 0.5, 3600)),
        "response_wait_seconds": float(_bounded_number(data, "responseWaitSeconds", 3, 0, 300)),
        "response_limit": int(_bounded_number(data, "responseLimit", 5, 1, 100, integer=True)),
        "save_as": save_as,
        "links_save_as": links_save_as,
        "max_failures": max_failures,
    }


def _resolve_node_input(
    data: dict[str, Any],
    inferred: str,
    default: str,
    legacy_defaults: set[str],
) -> str:
    mode = str(data.get("inputMode") or "").strip().lower()
    if mode and mode not in {"auto", "variable"}:
        raise ValueError("inputMode 必须是 auto 或 variable")
    configured = str(data.get("source") or "").strip()
    if mode == "variable":
        if not configured:
            raise ValueError("自定义输入变量不能为空")
        return configured
    if not mode and configured and configured not in legacy_defaults:
        return configured
    return inferred or configured or default


def flow_to_signins(flow: dict[str, Any]) -> dict[str, Any]:
    nodes = flow.get("nodes", [])
    edges = flow.get("edges", [])
    by_id = {str(node.get("id")): node for node in nodes}
    next_map: dict[str, str] = {}
    for edge in edges:
        source = str(edge.get("source"))
        target = str(edge.get("target"))
        if source and target:
            next_map[source] = target

    start = next((node for node in nodes if (node.get("data") or {}).get("kind") == "task"), nodes[0] if nodes else None)
    jobs: list[dict[str, Any]] = []
    seen_job_ids: dict[str, int] = {}

    def unique_job_id(raw: str) -> str:
        value = raw or str(flow.get("id") or "web-flow")
        count = seen_job_ids.get(value, 0)
        seen_job_ids[value] = count + 1
        return value if count == 0 else f"{value}-{count + 1}"

    def compile_job(start_node: dict[str, Any], inherited_peer: str = "") -> tuple[dict[str, Any], str, str]:
        data = start_node.get("data", {}) or {}
        job_id = unique_job_id(str(data.get("taskId") or flow.get("id") or "web-flow"))
        actions: list[dict[str, Any]] = []
        peer = inherited_peer
        collect_limit = 5
        latest_link_source = ""
        latest_message_source = ""
        include_contains: list[str] = []
        forward: dict[str, Any] = {"enabled": False}

        current = str(start_node.get("id"))
        visited: set[str] = set()
        next_start = ""
        while current and current not in visited:
            visited.add(current)
            node = by_id.get(current, {})
            data = node.get("data", {}) or {}
            node_type = data.get("kind") or node.get("type")
            if node_type == "task" and current != str(start_node.get("id")):
                next_start = current
                break
            if node_type == "open":
                peer = str(data.get("peer") or peer)
                actions.append({"type": "open", "node_id": current, "peer": peer, "regex": bool(data.get("regex"))})
            elif node_type == "send":
                if str(data.get("sendMode") or "") == "command":
                    text = str(data.get("command") if data.get("command") is not None else data.get("cmd", ""))
                else:
                    text = str(data.get("text") if data.get("text") is not None else data.get("cmd", ""))
                actions.append({"type": "send", "node_id": current, "text": text})
            elif node_type == "parse":
                pattern = str(data.get("pattern") or "")
                if pattern:
                    include_contains.append(pattern)
                parse_mode = str(data.get("parseMode") or data.get("mode") or "match")
                after_send = parse_mode == "after_send" or bool(data.get("afterSend"))
                save_as = str(data.get("saveAs") or "last_parse")
                extract = str(data.get("extract") or "messages")
                if not after_send:
                    collect_limit = int(data.get("limit") or collect_limit)
                actions.append({
                    "type": "parse",
                    "node_id": current,
                    "pattern": pattern,
                    "regex": bool(data.get("regex")),
                    "limit": int(data.get("limit") or collect_limit),
                    "save_as": save_as,
                    "extract": extract,
                    "after_send": after_send,
                    "wait_seconds": float(data.get("waitSeconds") or 3),
                })
                latest_message_source = "last_parse"
                if extract in {"links", "join_links"}:
                    latest_link_source = save_as
            elif node_type == "continuous_request":
                compiled_request = _compile_continuous_request(data, current)
                actions.append(compiled_request)
                latest_message_source = str(compiled_request["save_as"])
                latest_link_source = str(compiled_request["links_save_as"])
            elif node_type == "preview_catalog":
                callback_prefix = str(data.get("callbackPrefix") or "unlock:").strip()
                if not callback_prefix:
                    raise ValueError("callbackPrefix 不能为空")
                source = _resolve_node_input(
                    data,
                    latest_message_source,
                    "resource_responses",
                    {"resource_responses", "last_parse"},
                )
                actions.append({
                    "type": "preview_catalog",
                    "node_id": current,
                    "source": source,
                    "callback_prefix": callback_prefix,
                    "dedupe_store": str(data.get("dedupeStore") or "sessions/resource_previews.sqlite3"),
                    "export_path": str(data.get("exportPath") or "sessions/resource_previews.json"),
                })
            elif node_type == "resource_catalog":
                source = _resolve_node_input(
                    data,
                    latest_link_source,
                    "resource_records",
                    {"resource_records", "last_links"},
                )
                actions.append({
                    "type": "resource_catalog",
                    "node_id": current,
                    "source": source,
                    "dedupe_store": str(data.get("dedupeStore") or "sessions/resource_catalog.sqlite3"),
                    "export_path": str(data.get("exportPath") or "sessions/resource_links.json"),
                })
            elif node_type == "forward":
                forward = {
                    "enabled": True,
                    "mode": "user_forward",
                    "to_peer": str(data.get("toPeer") or ""),
                    "when": ["matched", "failure"],
                    "include": {"contains": include_contains or ["签到"], "regex": []},
                    "exclude": {"contains": [], "regex": []},
                }
                actions.append({"type": "forward", "node_id": current, "to_peer": str(data.get("toPeer") or ""), "source": str(data.get("source") or "last_parse")})
            elif node_type == "resource_pool":
                source = _resolve_node_input(
                    data,
                    latest_message_source,
                    "last_parse",
                    {"resource_responses", "last_parse"},
                )
                actions.append({
                    "type": "resource_pool",
                    "node_id": current,
                    "pool_peer": str(data.get("poolPeer") or ""),
                    "source": source,
                    "media_types": [
                        kind
                        for kind, enabled in {
                            "photo": data.get("includePhotos", True),
                            "video": data.get("includeVideos", True),
                        }.items()
                        if bool(enabled)
                    ] or ["photo", "video"],
                    "dedupe": bool(data.get("dedupe", True)),
                    "dedupe_store": str(data.get("dedupeStore") or "sessions/resource_pool_seen.sqlite3"),
                    "join_pool": bool(data.get("joinPool", True)),
                })
                latest_link_source = "resource_records"
            elif node_type == "link":
                actions.append({"type": "open_link", "node_id": current, "url": str(data.get("url") or "")})
            elif node_type == "join":
                source_mode = str(data.get("sourceMode") or "manual")
                actions.append({
                    "type": "join",
                    "node_id": current,
                    "target": str(data.get("target") or ""),
                    "source": str(data.get("source") or "") if source_mode == "variable" else "",
                })
            current = next_map.get(current, "")

        return {
            "id": job_id,
            "node_id": str(start_node.get("id")),
            "enabled": True,
            "peer": peer or "@example_bot",
            "accounts_secret": "TG_SESSION_STRINGS",
            "actions": actions or [{"type": "send", "text": "签到"}, {"type": "wait", "seconds": 5}],
            "collect": {"last_messages": collect_limit},
            "forward": forward,
        }, next_start, peer

    current_start = start
    inherited_peer = ""
    visited_starts: set[str] = set()
    while current_start and str(current_start.get("id")) not in visited_starts:
        visited_starts.add(str(current_start.get("id")))
        job, next_start_id, inherited_peer = compile_job(current_start, inherited_peer)
        jobs.append(job)
        current_start = by_id.get(next_start_id) if next_start_id else None
    default_include = [
        str(action.get("pattern"))
        for job in jobs
        for action in job.get("actions", [])
        if action.get("type") == "parse" and str(action.get("pattern") or "")
    ]
    default_collect_limit = max((int(job.get("collect", {}).get("last_messages", 5) or 5) for job in jobs), default=5)

    return {
        "defaults": {
            "api_id_secret": "TG_API_ID",
            "api_hash_secret": "TG_API_HASH",
            "accounts_secret": "TG_SESSION_STRINGS",
            "proxy_secret": "TG_PROXY",
            "forward": {
                "enabled": True,
                "mode": "notify",
                "when": ["failure", "matched"],
                "bot_token_secret": "TG_FORWARD_BOT_TOKEN",
                "chat_id_secret": "TG_FORWARD_CHAT_ID",
                "include": {"contains": default_include or ["签到成功", "积分"], "regex": []},
                "exclude": {"contains": ["广告"], "regex": []},
                "max_messages": default_collect_limit,
            },
        },
        "jobs": jobs,
    }


def flow_to_tasks(flow: dict[str, Any]) -> dict[str, Any]:
    job_id = str(flow.get("id") or "web-flow")
    return {
        "timezone": "Asia/Shanghai",
        "tasks": [
            {
                "id": f"tg-sign-{job_id}",
                "enabled": True,
                "schedule": ["daily:00:15"],
                "command": "uv run python scripts/sign_from_config.py --config config/signins.yml run-enabled --mail",
                "timeout_minutes": 30,
            }
        ],
    }


def flow_to_github_actions(flow: dict[str, Any]) -> dict[str, Any]:
    job_id = str(flow.get("id") or "web-flow")
    return {
        "name": f"TG {job_id}",
        "on": {
            "workflow_dispatch": None,
            "schedule": [
                {"cron": "15 16 * * *"},
            ],
        },
        "jobs": {
            "tg": {
                "runs-on": "ubuntu-latest",
                "env": {
                    "TG_SESSION_STRINGS": "${{ secrets.TG_SESSION_STRINGS }}",
                    "TG_PROXY": "${{ secrets.TG_PROXY }}",
                    "TG_RESOURCE_POOL_DB": "components/HX-Workflows/tg/sessions/resource_pool_seen.sqlite3",
                    "SMTP_HOST": "${{ secrets.SMTP_HOST }}",
                    "SMTP_USERNAME": "${{ secrets.SMTP_USERNAME }}",
                    "SMTP_PASSWORD": "${{ secrets.SMTP_PASSWORD }}",
                    "SMTP_TO": "${{ secrets.SMTP_TO }}",
                },
                "steps": [
                    {"uses": "actions/checkout@v4"},
                    {
                        "name": "Restore resource indexes",
                        "uses": "actions/cache@v4",
                        "with": {
                            "path": "\n".join(
                                [
                                    "components/HX-Workflows/tg/sessions/resource_pool_seen.sqlite3",
                                    "components/HX-Workflows/tg/sessions/resource_pool_seen.sqlite3-*",
                                    "components/HX-Workflows/tg/sessions/resource_catalog.sqlite3",
                                    "components/HX-Workflows/tg/sessions/resource_catalog.sqlite3-*",
                                    "components/HX-Workflows/tg/sessions/resource_links.json",
                                    "components/HX-Workflows/tg/sessions/resource_previews.sqlite3",
                                    "components/HX-Workflows/tg/sessions/resource_previews.sqlite3-*",
                                    "components/HX-Workflows/tg/sessions/resource_previews.json",
                                ]
                            ),
                            "key": "tg-resource-pool-${{ github.repository }}-${{ github.run_id }}",
                            "restore-keys": "tg-resource-pool-${{ github.repository }}-",
                        },
                    },
                    {
                        "name": "Install uv",
                        "uses": "astral-sh/setup-uv@v5",
                    },
                    {
                        "name": "Run TG tasks",
                        "run": "uv run python runner.py --config config/tasks.yml run-due",
                        "working-directory": "components/HX-Workflows/tg",
                    },
                ],
            },
        },
    }


def read_workflows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def write_workflows(path: Path, workflows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(workflows, ensure_ascii=False, indent=2), encoding="utf-8")


def compile_flow_outputs(flow: dict[str, Any]) -> dict[str, str]:
    github = GITHUB_WORKFLOW.read_text(encoding="utf-8") if GITHUB_WORKFLOW.exists() else ""
    return {
        "signins": yaml.safe_dump(flow_to_signins(flow), allow_unicode=True, sort_keys=False),
        "tasks": yaml.safe_dump(flow_to_tasks(flow), allow_unicode=True, sort_keys=False),
        "github": github,
    }


def is_default_example_flow(flow: dict[str, Any]) -> bool:
    if str(flow.get("id") or "") != "daily-sign":
        return False
    nodes = flow.get("nodes", [])
    if len(nodes) != 5:
        return False
    node_data = [(node.get("data") or {}) for node in nodes]
    has_example_peer = any(str(data.get("peer") or "") == "@example_bot" for data in node_data)
    has_example_task = any(str(data.get("taskId") or "") == "daily-sign" for data in node_data)
    return has_example_peer and has_example_task


def apply_flow_configs(flow: dict[str, Any]) -> dict[str, Any]:
    if is_default_example_flow(flow):
        raise ValueError("当前画布还是默认示例任务，请先加载或保存真实工作流后再应用")
    outputs = compile_flow_outputs(flow)
    files = {
        "signins": SIGNINS_CONFIG,
        "tasks": TASKS_CONFIG,
    }
    for key, path in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(outputs[key], encoding="utf-8")
    return {
        "files": {key: str(path.relative_to(ROOT.parent)) for key, path in files.items()},
        "github_workflow": str(GITHUB_WORKFLOW.relative_to(ROOT.parent)),
    }


def save_workflow(path: Path, flow: dict[str, Any], previous_id: str = "") -> dict[str, Any]:
    workflows = read_workflows(path)
    flow_id = str(flow.get("id") or uuid.uuid4().hex[:10])
    flow["id"] = flow_id
    flow["updated_at"] = int(time.time())
    replaced_ids = {flow_id}
    if previous_id:
        replaced_ids.add(previous_id)
    workflows = [item for item in workflows if str(item.get("id")) not in replaced_ids]
    workflows.append(flow)
    write_workflows(path, workflows)
    return flow


def delete_workflow(path: Path, flow_id: str) -> bool:
    workflows = read_workflows(path)
    next_workflows = [item for item in workflows if str(item.get("id")) != flow_id]
    write_workflows(path, next_workflows)
    return len(next_workflows) != len(workflows)


def ordered_node_ids(flow: dict[str, Any]) -> list[str]:
    nodes = flow.get("nodes", [])
    edges = flow.get("edges", [])
    by_id = {str(node.get("id")): node for node in nodes}
    next_map = {str(edge.get("source")): str(edge.get("target")) for edge in edges if edge.get("source") and edge.get("target")}
    start = next((node for node in nodes if (node.get("data") or {}).get("kind") == "task"), nodes[0] if nodes else None)
    current = str(start.get("id")) if start else ""
    ordered: list[str] = []
    visited: set[str] = set()
    while current and current not in visited and current in by_id:
        visited.add(current)
        ordered.append(current)
        current = next_map.get(current, "")
    return ordered
