"""Plan a full skill-directory replacement against an existing git tree."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GitFile:
    path: str
    mode: str
    sha: str


@dataclass(frozen=True)
class TreeChange:
    path: str
    mode: str
    sha: str | None
    upload_sha: str | None


def git_blob_mode(mode: str) -> str | None:
    if mode in {"100644", "100755", "120000"}:
        return mode
    return None


def _under(prefix: str, path: str) -> bool:
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def plan_skill_replacement(
    prefix: str,
    target: list[GitFile],
    source: list[GitFile],
) -> list[TreeChange]:
    """Return create-tree operations. Unchanged blobs are omitted.

    `upload_sha` is a source blob that must be copied into the target
    repository before the tree update. A null `sha` deletes the path.
    """

    known = {item.sha for item in target}
    source_paths = {item.path for item in source}
    target_by_path = {item.path: item for item in target if _under(prefix, item.path)}
    changes: list[TreeChange] = []
    for item in source:
        current = target_by_path.get(item.path)
        if current is not None and current.sha == item.sha and current.mode == item.mode:
            continue
        changes.append(
            TreeChange(
                path=item.path,
                mode=item.mode,
                sha=item.sha,
                upload_sha=None if item.sha in known else item.sha,
            )
        )
    for path, current in sorted(target_by_path.items()):
        if path not in source_paths:
            changes.append(TreeChange(path=path, mode=current.mode, sha=None, upload_sha=None))
    return changes


def safe_skill_dir(path: str, roots: tuple[str, ...]) -> bool:
    parts = path.split("/")
    if path.startswith("/") or ".." in parts or "" in parts:
        return False
    return any(path == f"{root}/{parts[-1]}" and path.startswith(root + "/") for root in roots)


def safe_branch_name(branch: str, default_branch: str) -> bool:
    if branch == "" or branch == default_branch or branch.startswith("-") or branch.endswith("/"):
        return False
    if ".." in branch.split("/") or "//" in branch or " " in branch or "\\" in branch:
        return False
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/._+-")
    return all(char in allowed for char in branch)
