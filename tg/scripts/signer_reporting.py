"""Report rendering and notification adapters for TG signing."""

from __future__ import annotations

import json
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from signer_core import RunResult, WORKFLOWS_ROOT, message_text

def bot_api_notify(bot_token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text[:3900],
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()


def build_summary(result: RunResult, selected_texts: list[str]) -> str:
    lines = [
        f"TG 签到任务: {result.job_id}",
        f"账号序号: {result.account_index}",
        f"状态: {result.status}",
        f"命中过滤: {'是' if result.matched else '否'}",
        "",
        result.summary,
    ]
    if selected_texts:
        lines.append("")
        lines.append("匹配消息:")
        for idx, text in enumerate(selected_texts, start=1):
            lines.append(f"--- #{idx} ---")
            lines.append(text[:1200])
    return "\n".join(lines)


def result_texts(result: RunResult) -> list[str]:
    return [message_text(msg) for msg in result.messages if message_text(msg)]


def result_status_label(status: str) -> str:
    return {
        "success": "成功",
        "failure": "失败",
    }.get(status, status)


def result_to_report_item(result: RunResult) -> dict[str, Any]:
    return {
        "account_index": result.account_index,
        "status": result.status,
        "matched": result.matched,
        "summary": result.summary,
        "messages": result_texts(result),
        "node_logs": result.node_logs,
    }


def job_to_report_workflow(job: dict[str, Any]) -> dict[str, Any]:
    nodes: list[dict[str, str]] = []
    edges: list[dict[str, str]] = []
    previous = str(job.get("node_id") or "")
    if previous:
        nodes.append({"id": previous, "kind": "task", "label": str(job.get("id") or previous)})
    seen = {previous} if previous else set()
    for action in job.get("actions", []) or []:
        node_id = str(action.get("node_id") or "")
        if not node_id:
            continue
        if node_id not in seen:
            nodes.append({"id": node_id, "kind": str(action.get("type") or "action"), "label": str(action.get("type") or node_id)})
            seen.add(node_id)
        if previous and previous != node_id:
            edges.append({"source": previous, "target": node_id})
        previous = node_id
    return {"nodes": nodes, "edges": edges}


def write_report(grouped: dict[str, list[RunResult]], path: Path, jobs: list[dict[str, Any]] | None = None) -> None:
    jobs_by_id = {str(job.get("id")): job for job in jobs or [] if isinstance(job, dict)}
    report = {
        "tasks": [
            {
                "id": job_id,
                "workflow": job_to_report_workflow(jobs_by_id.get(job_id, {})),
                "accounts": [result_to_report_item(result) for result in results],
            }
            for job_id, results in grouped.items()
        ]
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 TG 签到报告: {path}")


def send_report_with_emall(report_path: Path) -> None:
    command = [
        "uv",
        "run",
        "python",
        "send_tg_report.py",
        str(report_path),
    ]
    subprocess.run(command, cwd=WORKFLOWS_ROOT / "emall", check=True)
