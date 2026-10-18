"""Read branches and skills from a blobless git clone.

The GitHub API still lists repositories and writes changes. File contents and
merge checks come from local git so a partial API response cannot invent a
canonical skill.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from hygiene.github_api import _frontmatter
from hygiene.models import SkillInstance, parse_version
from hygiene.trees import GitFile

NAME = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")


class GitCloneError(RuntimeError):
    pass


def _safe_name(value: str) -> str:
    if value == "" or any(char not in NAME for char in value):
        raise GitCloneError(f"仓库名不安全: {value}")
    return value


def clone_args(owner: str, repo: str, dest: Path) -> list[str]:
    owner = _safe_name(owner)
    repo = _safe_name(repo)
    return [
        "clone",
        "--bare",
        "--filter=blob:none",
        "--no-tags",
        f"https://github.com/{owner}/{repo}.git",
        str(dest),
    ]


def _git(repo: Path, args: list[str], token: str = "", timeout: int = 180) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    env.pop("GIT_CONFIG_COUNT", None)
    env.pop("GIT_CONFIG_KEY_0", None)
    env.pop("GIT_CONFIG_VALUE_0", None)
    if token:
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.extraheader"
        env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: bearer {token}"
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as error:
        raise GitCloneError("git 超时") from error


def _check(repo: Path, args: list[str], token: str = "") -> str:
    result = _git(repo, args, token)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace")[:300]
        if token:
            detail = detail.replace(token, "***")
        raise GitCloneError(detail.strip() or "git 失败")
    return result.stdout.decode("utf-8", errors="replace")


class LocalRepo:
    def __init__(self, path: Path, token: str = "") -> None:
        self.path = path
        self.token = token

    @classmethod
    def clone(cls, owner: str, repo: str, dest: Path, token: str) -> LocalRepo:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        env = os.environ.copy()
        if token:
            env["GIT_CONFIG_COUNT"] = "1"
            env["GIT_CONFIG_KEY_0"] = "http.extraheader"
            env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: bearer {token}"
        result = subprocess.run(
            ["git", *clone_args(owner, repo, dest)],
            capture_output=True,
            timeout=300,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace")[:300]
            if token:
                detail = detail.replace(token, "***")
            raise GitCloneError(detail.strip() or "克隆失败")
        return cls(dest, token)

    def close(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def empty(self) -> bool:
        result = _git(self.path, ["for-each-ref", "--format=%(refname)", "refs/heads"])
        return result.returncode != 0 or not result.stdout.strip()

    def branch_names(self) -> list[str]:
        text = _check(self.path, ["for-each-ref", "--format=%(refname:short)", "refs/heads"], self.token)
        return [line for line in text.splitlines() if line]

    def tip(self, branch: str) -> str:
        return _check(self.path, ["rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"], self.token).strip()

    def ahead_behind(self, base: str, head: str) -> tuple[int, int]:
        base_sha = self.tip(base)
        head_sha = self.tip(head)
        ahead = int(_check(self.path, ["rev-list", "--count", f"{base_sha}..{head_sha}"], self.token).strip())
        behind = int(_check(self.path, ["rev-list", "--count", f"{head_sha}..{base_sha}"], self.token).strip())
        return ahead, behind

    def read_skills(
        self,
        owner: str,
        repo: str,
        commit_sha: str,
        roots: tuple[str, ...],
    ) -> tuple[list[SkillInstance], list[str]]:
        listing = _check(self.path, ["ls-tree", "-r", "-z", commit_sha], self.token)
        found = _skill_entries(listing, roots)
        skills: list[SkillInstance] = []
        errors: list[str] = []
        for path, folder, blob_sha in found:
            try:
                text = _check(self.path, ["cat-file", "blob", blob_sha], self.token)
                tree_sha = _check(
                    self.path,
                    ["rev-parse", "--verify", f"{commit_sha}:{path}"],
                    self.token,
                ).strip()
                meta = _commit_meta(self.path, commit_sha, path, self.token)
            except GitCloneError as error:
                errors.append(f"{path} 的 SKILL.md 读取失败: {error}")
                continue
            name, version = _frontmatter(text)
            skills.append(
                SkillInstance(
                    owner=owner,
                    repo=repo,
                    path=path,
                    folder=folder,
                    declared_name=name,
                    version=version,
                    parsed_version=parse_version(version),
                    tree_sha=tree_sha,
                    commit_sha=meta[0],
                    committed_at=meta[1],
                )
            )
        return skills, errors

    def list_skill_files(self, commit_sha: str, skill_path: str) -> list[GitFile]:
        listing = _check(self.path, ["ls-tree", "-r", "-z", f"{commit_sha}:{skill_path}"], self.token)
        prefix = skill_path.rstrip("/") + "/"
        files: list[GitFile] = []
        for mode, kind, sha, relative in _tree_rows(listing):
            if kind != "blob" or mode not in {"100644", "100755", "120000"}:
                continue
            files.append(GitFile(path=prefix + relative, mode=mode, sha=sha))
        return files

    def blob_bytes(self, sha: str) -> bytes:
        result = _git(self.path, ["cat-file", "blob", sha], self.token)
        if result.returncode != 0:
            raise GitCloneError("无法读取文件内容")
        return result.stdout


def _commit_meta(repo: Path, commit_sha: str, path: str, token: str) -> tuple[str | None, str | None]:
    text = _check(repo, ["log", "-1", "--format=%H%n%cI", commit_sha, "--", path], token).strip()
    if not text:
        return None, None
    sha, _, when = text.partition("\n")
    return sha or None, when or None


def _tree_rows(listing: str) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for raw in listing.split("\0"):
        if not raw.strip():
            continue
        meta, tab, path = raw.partition("\t")
        if tab != "\t" or path == "":
            continue
        parts = meta.split()
        if len(parts) < 3:
            continue
        rows.append((parts[0], parts[1], parts[2], path))
    return rows


def _skill_entries(listing: str, roots: tuple[str, ...]) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for mode, kind, sha, path in _tree_rows(listing):
        if kind != "blob" or not path.endswith("/SKILL.md"):
            continue
        parent, _slash, filename = path.rpartition("/")
        if filename != "SKILL.md" or "/" not in parent:
            continue
        root, _slash, folder = parent.rpartition("/")
        if root not in roots or folder == "" or "/" in folder:
            continue
        if mode not in {"100644", "100755"}:
            continue
        found.append((parent, folder, sha))
    return found
