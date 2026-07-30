"""Safe GitHub CLI adapter for local workflow and credential management."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from local_web_accounts import read_lines

REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
WORKFLOW_PATTERN = re.compile(r"^[A-Za-z0-9_. /-]+$")
SUPPORTED_SECRET_NAMES = {
    "TG_API_HASH",
    "TG_API_ID",
    "TG_MAIN_SESSION_STRING",
    "TG_SESSION_STRINGS",
}


class GitHubCliError(RuntimeError):
    """Raised when a guarded GitHub CLI operation fails."""


def _run_gh(args: list[str], *, cwd: Path, stdin: str | None = None, timeout: int = 30) -> str:
    if shutil.which("gh") is None:
        raise GitHubCliError("未安装 GitHub CLI（gh）")
    try:
        result = subprocess.run(
            ["gh", *args],
            cwd=cwd,
            input=stdin,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitHubCliError("GitHub CLI 请求超时") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "gh 执行失败").strip().splitlines()[-1]
        raise GitHubCliError(detail[:400])
    return result.stdout


def _json_output(args: list[str], *, cwd: Path) -> Any:
    raw = _run_gh(args, cwd=cwd)
    try:
        return json.loads(raw or "null")
    except json.JSONDecodeError as exc:
        raise GitHubCliError("GitHub CLI 返回了无法解析的数据") from exc


def validate_repository(repository: str) -> str:
    value = repository.strip()
    if not REPOSITORY_PATTERN.fullmatch(value):
        raise ValueError("仓库必须使用 owner/repo 格式")
    return value


def validate_workflow(workflow: str) -> str:
    value = workflow.strip()
    if not value or not WORKFLOW_PATTERN.fullmatch(value) or ".." in value:
        raise ValueError("工作流名称或路径无效")
    return value


def github_status(cwd: Path, repository: str = "") -> dict[str, Any]:
    if shutil.which("gh") is None:
        return {"gh_installed": False, "authenticated": False, "repository": repository, "secrets": [], "workflows": []}
    try:
        auth = subprocess.run(
            ["gh", "auth", "status"],
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"gh_installed": True, "authenticated": False, "repository": repository, "secrets": [], "workflows": []}
    if auth.returncode != 0:
        return {"gh_installed": True, "authenticated": False, "repository": repository, "secrets": [], "workflows": []}
    repo = validate_repository(repository) if repository else ""
    if not repo:
        info = _json_output(["repo", "view", "--json", "nameWithOwner,url,viewerPermission"], cwd=cwd)
        repo = validate_repository(str(info.get("nameWithOwner") or ""))
    else:
        info = _json_output(["repo", "view", repo, "--json", "nameWithOwner,url,viewerPermission"], cwd=cwd)
    secrets = _json_output(["secret", "list", "--repo", repo, "--json", "name,updatedAt"], cwd=cwd)
    workflows = _json_output(["workflow", "list", "--repo", repo, "--all", "--json", "id,name,path,state"], cwd=cwd)
    return {
        "gh_installed": True,
        "authenticated": True,
        "repository": repo,
        "url": info.get("url", ""),
        "permission": info.get("viewerPermission", ""),
        "secrets": secrets if isinstance(secrets, list) else [],
        "workflows": workflows if isinstance(workflows, list) else [],
    }


def local_credential_summary(alt_out: Path, main_out: Path) -> list[dict[str, Any]]:
    alt_sessions = read_lines(alt_out)
    main_sessions = read_lines(main_out)
    return [
        {
            "name": "TG_SESSION_STRINGS",
            "available": bool(alt_sessions),
            "detail": f"{len(alt_sessions)} 个小号 session",
            "sensitive": True,
        },
        {
            "name": "TG_MAIN_SESSION_STRING",
            "available": bool(main_sessions),
            "detail": f"{len(main_sessions)} 个主账号 session（工作流默认不读取）",
            "sensitive": True,
        },
        {"name": "TG_API_ID", "available": True, "detail": "Telegram 应用 ID", "sensitive": True},
        {"name": "TG_API_HASH", "available": True, "detail": "Telegram 应用 Hash", "sensitive": True},
    ]


def sync_local_credentials(
    *,
    cwd: Path,
    repository: str,
    names: list[str],
    alt_out: Path,
    main_out: Path,
    api_id: int,
    api_hash: str,
) -> list[str]:
    repo = validate_repository(repository)
    requested = list(dict.fromkeys(names))
    unsupported = sorted(set(requested) - SUPPORTED_SECRET_NAMES)
    if unsupported:
        raise ValueError(f"不支持同步这些 Secret: {', '.join(unsupported)}")
    values = {
        "TG_SESSION_STRINGS": "\n".join(read_lines(alt_out)),
        "TG_MAIN_SESSION_STRING": "\n".join(read_lines(main_out)),
        "TG_API_ID": str(api_id),
        "TG_API_HASH": api_hash,
    }
    uploaded: list[str] = []
    for name in requested:
        value = values[name]
        if not value:
            raise ValueError(f"本地 {name} 为空，已停止同步")
        # stdin keeps credential values out of argv and process listings.
        _run_gh(["secret", "set", name, "--repo", repo], cwd=cwd, stdin=value, timeout=45)
        uploaded.append(name)
    return uploaded


def manage_workflow(*, cwd: Path, repository: str, workflow: str, action: str, ref: str = "") -> str:
    repo = validate_repository(repository)
    target = validate_workflow(workflow)
    if action == "enable":
        _run_gh(["workflow", "enable", target, "--repo", repo], cwd=cwd)
        return "enabled"
    if action == "disable":
        _run_gh(["workflow", "disable", target, "--repo", repo], cwd=cwd)
        return "disabled"
    if action == "run":
        args = ["workflow", "run", target, "--repo", repo]
        if ref.strip():
            args.extend(["--ref", ref.strip()])
        _run_gh(args, cwd=cwd)
        return "dispatched"
    raise ValueError("不支持的工作流操作")
