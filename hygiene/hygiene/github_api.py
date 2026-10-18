"""GitHub REST and GraphQL adapter. Callers pass tokens; this module never logs them."""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

from hygiene.models import SkillInstance, parse_version
from hygiene.trees import GitFile, git_blob_mode

ACCEPT = "application/vnd.github+json"
API_VERSION = "2022-11-28"
USER_AGENT = "hx-workflows-hygiene"


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def gql_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def branch_ref_path(branch: str) -> str:
    return "heads/" + urllib.parse.quote(branch, safe="")


class GitHub:
    def __init__(self, token: str, base_url: str = "https://api.github.com") -> None:
        self._token = token
        self._base = base_url.rstrip("/")

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        accept: str = ACCEPT,
        raw: bool = False,
    ) -> Any:
        url = path if path.startswith("https://") else self._base + path
        payload = None if json_body is None else json.dumps(json_body).encode("utf-8")
        last_error: GitHubError | None = None
        for attempt in range(3):
            request = urllib.request.Request(url, data=payload, method=method)
            request.add_header("Authorization", f"Bearer {self._token}")
            request.add_header("Accept", accept)
            request.add_header("User-Agent", USER_AGENT)
            request.add_header("X-GitHub-Api-Version", API_VERSION)
            if payload is not None:
                request.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    body = response.read()
                    if raw:
                        return body
                    if not body:
                        return None
                    return json.loads(body.decode("utf-8"))
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")[:500]
                last_error = GitHubError(error.code, f"GitHub {method} {path} 返回 {error.code}: {detail}")
                if method in {"GET", "HEAD"} and error.code in {403, 429, 502, 503} and attempt < 2:
                    time.sleep(2**attempt)
                    continue
                raise last_error from None
            except (urllib.error.URLError, TimeoutError, OSError):
                last_error = GitHubError(0, f"GitHub {method} {path} 网络失败")
                if method in {"GET", "HEAD"} and attempt < 2:
                    time.sleep(2**attempt)
                    continue
                raise last_error from None
        if last_error:
            raise last_error
        raise GitHubError(0, f"GitHub {method} {path} 没有返回")

    def graphql(self, query: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        payload = self.request("POST", "/graphql", json_body={"query": query})
        if not isinstance(payload, dict):
            raise GitHubError(0, "GraphQL 返回无法解析")
        data = payload.get("data") or {}
        errors = payload.get("errors") or []
        return data, errors

    def paginate(self, path: str, params: dict[str, str] | None = None) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            query = dict(params or {})
            query["per_page"] = "100"
            query["page"] = str(page)
            suffix = "?" + urllib.parse.urlencode(query)
            batch = self.request("GET", path + suffix)
            if not isinstance(batch, list) or not batch:
                return
            for item in batch:
                if isinstance(item, dict):
                    yield item
            if len(batch) < 100:
                return
            page += 1

    def list_owner_repos(self, owner: str) -> list[dict[str, Any]]:
        repos: list[dict[str, Any]] = []
        for repo in self.paginate("/user/repos", {"affiliation": "owner", "visibility": "all"}):
            login = str((repo.get("owner") or {}).get("login") or "")
            if login == owner:
                repos.append(repo)
        return repos

    def list_branches(self, owner: str, repo: str) -> list[dict[str, Any]]:
        return list(self.paginate(f"/repos/{owner}/{repo}/branches"))

    def branch_tip(self, owner: str, repo: str, branch: str) -> str:
        ref = self.request("GET", f"/repos/{owner}/{repo}/git/ref/{branch_ref_path(branch)}")
        return str((ref.get("object") or {}).get("sha") or "")

    def open_pulls(self, owner: str, repo: str) -> list[dict[str, str]]:
        pulls: list[dict[str, str]] = []
        for pull in self.paginate(f"/repos/{owner}/{repo}/pulls", {"state": "open"}):
            head = pull.get("head") or {}
            repository = head.get("repo") or {}
            if repository.get("name") != repo or (repository.get("owner") or {}).get("login") != owner:
                continue
            pulls.append(
                {
                    "number": str(pull.get("number") or ""),
                    "url": str(pull.get("html_url") or ""),
                    "ref": str(head.get("ref") or ""),
                }
            )
        return pulls

    def open_pr_heads(self, owner: str, repo: str) -> list[str]:
        return [item["ref"] for item in self.open_pulls(owner, repo) if item["ref"]]

    def compare_to_base(self, owner: str, repo: str, base: str, heads: list[str]) -> dict[str, dict[str, Any]]:
        compared: dict[str, dict[str, Any]] = {}
        for offset in range(0, len(heads), 20):
            chunk = heads[offset : offset + 20]
            fields = []
            for index, head in enumerate(chunk):
                fields.append(
                    f"c{index}: compare(headRef: {gql_str('refs/heads/' + head)}) "
                    "{ aheadBy behindBy status }"
                )
            query = (
                "query { repository(owner: "
                + gql_str(owner)
                + ", name: "
                + gql_str(repo)
                + ") { ref(qualifiedName: "
                + gql_str("refs/heads/" + base)
                + ") { "
                + " ".join(fields)
                + " } } }"
            )
            data, errors = self.graphql(query)
            ref = ((data.get("repository") or {}).get("ref") or {}) if isinstance(data, dict) else {}
            failed = _alias_errors(errors, prefix="c")
            for index, head in enumerate(chunk):
                if index in failed or not isinstance(ref, dict) or ref.get(f"c{index}") is None:
                    compared[head] = {"error": failed.get(index, "无法比较")}
                    continue
                node = ref[f"c{index}"]
                compared[head] = {
                    "ahead": node.get("aheadBy"),
                    "behind": node.get("behindBy"),
                    "status": node.get("status"),
                }
        return compared

    def read_skills(
        self,
        owner: str,
        repo: str,
        default_branch: str,
        roots: tuple[str, ...],
        commit_sha: str | None = None,
    ) -> tuple[list[SkillInstance], list[str]]:
        if not commit_sha:
            try:
                ref = self.request("GET", f"/repos/{owner}/{repo}/git/ref/{branch_ref_path(default_branch)}")
            except GitHubError as error:
                if error.status in {404, 409}:
                    return [], []
                raise
            commit_sha = str((ref.get("object") or {}).get("sha") or "")
        tree = self.request("GET", f"/repos/{owner}/{repo}/git/trees/{commit_sha}?recursive=1")
        if tree.get("truncated"):
            return [], ["目录树被截断, 这次没有判断 Skill"]
        entries = [item for item in tree.get("tree", []) if isinstance(item, dict)]
        found = _skill_paths(entries, roots)
        if not found:
            return [], []
        texts, failures = self._blob_texts(owner, repo, commit_sha, [item[2] for item in found])
        dates = self._path_commits(commit_sha, owner, repo, [item[0] for item in found])
        skills: list[SkillInstance] = []
        errors = [f"{path} 的 SKILL.md 读取失败" for path in failures]
        for path, folder, skill_file, tree_sha in found:
            if skill_file in failures:
                continue
            name, version = _frontmatter(texts.get(skill_file, ""))
            commit = dates.get(path) or {}
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
                    commit_sha=commit.get("sha"),
                    committed_at=commit.get("at"),
                )
            )
        return skills, errors

    def _blob_texts(self, owner: str, repo: str, commit_sha: str, paths: list[str]) -> tuple[dict[str, str], set[str]]:
        texts: dict[str, str] = {}
        failed: set[str] = set()
        for offset in range(0, len(paths), 15):
            chunk = paths[offset : offset + 15]
            fields = []
            for index, path in enumerate(chunk):
                fields.append(
                    f"b{index}: object(expression: {gql_str(commit_sha + ':' + path)}) "
                    "{ ... on Blob { text } }"
                )
            query = (
                "query { repository(owner: "
                + gql_str(owner)
                + ", name: "
                + gql_str(repo)
                + ") { "
                + " ".join(fields)
                + " } }"
            )
            data, errors = self.graphql(query)
            error_indexes = _alias_errors(errors, prefix="b")
            repository = data.get("repository") or {}
            for index, path in enumerate(chunk):
                node = repository.get(f"b{index}") or {}
                text = node.get("text") if isinstance(node, dict) else None
                if index in error_indexes or not isinstance(text, str):
                    failed.add(path)
                    continue
                texts[path] = text
        return texts, failed

    def _path_commits(
        self,
        commit_sha: str,
        owner: str,
        repo: str,
        paths: list[str],
    ) -> dict[str, dict[str, str]]:
        found: dict[str, dict[str, str]] = {}
        for offset in range(0, len(paths), 15):
            chunk = paths[offset : offset + 15]
            fields = []
            for index, path in enumerate(chunk):
                fields.append(
                    f"h{index}: history(first: 1, path: {gql_str(path)}) "
                    "{ nodes { oid committedDate } }"
                )
            query = (
                "query { repository(owner: "
                + gql_str(owner)
                + ", name: "
                + gql_str(repo)
                + ") { object(oid: "
                + gql_str(commit_sha)
                + ") { ... on Commit { "
                + " ".join(fields)
                + " } } } }"
            )
            data, _errors = self.graphql(query)
            commit = ((data.get("repository") or {}).get("object") or {}) if isinstance(data, dict) else {}
            for index, path in enumerate(chunk):
                nodes = ((commit.get(f"h{index}") or {}).get("nodes") or []) if isinstance(commit, dict) else []
                if nodes and isinstance(nodes[0], dict):
                    found[path] = {
                        "sha": str(nodes[0].get("oid") or ""),
                        "at": str(nodes[0].get("committedDate") or ""),
                    }
        return found

    def list_skill_files(self, owner: str, repo: str, ref: str, skill_path: str, commit_sha: str | None = None) -> list[GitFile]:
        if not commit_sha:
            ref_payload = self.request("GET", f"/repos/{owner}/{repo}/git/ref/{branch_ref_path(ref)}")
            commit_sha = str((ref_payload.get("object") or {}).get("sha") or "")
        tree = self.request("GET", f"/repos/{owner}/{repo}/git/trees/{commit_sha}?recursive=1")
        if tree.get("truncated"):
            raise GitHubError(0, f"{owner}/{repo} 的目录树被截断")
        files: list[GitFile] = []
        prefix = skill_path.rstrip("/") + "/"
        for item in tree.get("tree", []):
            path = str(item.get("path") or "")
            if item.get("type") != "blob" or not path.startswith(prefix):
                continue
            mode = git_blob_mode(str(item.get("mode") or ""))
            if mode is None:
                continue
            files.append(GitFile(path=path, mode=mode, sha=str(item.get("sha") or "")))
        return files

    def get_blob_bytes(self, owner: str, repo: str, sha: str) -> bytes:
        return self.request(
            "GET",
            f"/repos/{owner}/{repo}/git/blobs/{sha}",
            accept="application/vnd.github.raw",
            raw=True,
        )

    def create_blob(self, owner: str, repo: str, content: bytes) -> str:
        created = self.request(
            "POST",
            f"/repos/{owner}/{repo}/git/blobs",
            json_body={"content": base64.b64encode(content).decode("ascii"), "encoding": "base64"},
        )
        return str(created.get("sha") or "")

    def create_tree(self, owner: str, repo: str, base_tree: str, entries: list[dict[str, Any]]) -> str:
        created = self.request(
            "POST",
            f"/repos/{owner}/{repo}/git/trees",
            json_body={"base_tree": base_tree, "tree": entries},
        )
        return str(created.get("sha") or "")

    def default_commit_tree(self, owner: str, repo: str, default_branch: str) -> tuple[str, str]:
        ref_payload = self.request("GET", f"/repos/{owner}/{repo}/git/ref/{branch_ref_path(default_branch)}")
        commit_sha = str((ref_payload.get("object") or {}).get("sha") or "")
        commit = self.request("GET", f"/repos/{owner}/{repo}/git/commits/{commit_sha}")
        tree_sha = str((commit.get("tree") or {}).get("sha") or "")
        return commit_sha, tree_sha

    def create_commit(self, owner: str, repo: str, message: str, tree_sha: str, parent: str) -> str:
        created = self.request(
            "POST",
            f"/repos/{owner}/{repo}/git/commits",
            json_body={"message": message, "tree": tree_sha, "parents": [parent]},
        )
        return str(created.get("sha") or "")

    def create_branch(self, owner: str, repo: str, branch: str, sha: str) -> None:
        self.request(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            json_body={"ref": f"refs/heads/{branch}", "sha": sha},
        )

    def create_pull(self, owner: str, repo: str, title: str, body: str, head: str, base: str) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json_body={"title": title, "body": body, "head": head, "base": base},
        )

    def merge_pull(self, owner: str, repo: str, number: int, title: str) -> None:
        self.request(
            "PUT",
            f"/repos/{owner}/{repo}/pulls/{number}/merge",
            json_body={"merge_method": "squash", "commit_title": title},
        )

    def delete_branch(self, owner: str, repo: str, branch: str) -> None:
        self.request("DELETE", f"/repos/{owner}/{repo}/git/refs/{branch_ref_path(branch)}")

    def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        return self.request("GET", f"/repos/{owner}/{repo}")

    def get_branch(self, owner: str, repo: str, branch: str) -> dict[str, Any]:
        encoded = urllib.parse.quote(branch, safe="")
        return self.request("GET", f"/repos/{owner}/{repo}/branches/{encoded}")

    def list_open_report_issues(self, owner: str, repo: str) -> list[dict[str, Any]]:
        issues = []
        for issue in self.paginate(
            f"/repos/{owner}/{repo}/issues",
            {"state": "open", "labels": "hygiene-report"},
        ):
            if "pull_request" not in issue:
                issues.append(issue)
        return issues

    def ensure_label(self, owner: str, repo: str, name: str, color: str, description: str) -> None:
        try:
            self.request(
                "POST",
                f"/repos/{owner}/{repo}/labels",
                json_body={"name": name, "color": color, "description": description},
            )
        except GitHubError as error:
            if error.status != 422:
                raise

    def create_issue(self, owner: str, repo: str, title: str, body: str) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/repos/{owner}/{repo}/issues",
            json_body={"title": title, "body": body, "labels": ["hygiene-report"]},
        )

    def update_issue(self, owner: str, repo: str, number: int, *, body: str | None = None, state: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if body is not None:
            payload["body"] = body
        if state is not None:
            payload["state"] = state
        return self.request("PATCH", f"/repos/{owner}/{repo}/issues/{number}", json_body=payload)

    def comment(self, owner: str, repo: str, number: int, body: str) -> None:
        self.request("POST", f"/repos/{owner}/{repo}/issues/{number}/comments", json_body={"body": body})

    def remove_label(self, owner: str, repo: str, number: int, name: str) -> None:
        encoded = urllib.parse.quote(name, safe="")
        try:
            self.request("DELETE", f"/repos/{owner}/{repo}/issues/{number}/labels/{encoded}")
        except GitHubError as error:
            if error.status != 404:
                raise

    def get_issue(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        return self.request("GET", f"/repos/{owner}/{repo}/issues/{number}")


def _alias_errors(errors: list[dict[str, Any]], prefix: str = "c") -> dict[int, str]:
    failed: dict[int, str] = {}
    for error in errors:
        path = error.get("path") or []
        for part in path:
            if isinstance(part, str) and part.startswith(prefix) and part[len(prefix) :].isdigit():
                failed[int(part[len(prefix) :])] = str(error.get("message") or "比较失败")
    return failed


def _skill_paths(entries: list[dict[str, Any]], roots: tuple[str, ...]) -> list[tuple[str, str, str, str]]:
    trees = {str(item.get("path")): str(item.get("sha")) for item in entries if item.get("type") == "tree"}
    found: list[tuple[str, str, str, str]] = []
    for item in entries:
        path = str(item.get("path") or "")
        if item.get("type") != "blob" or not path.endswith("/SKILL.md"):
            continue
        parent, _slash, filename = path.rpartition("/")
        if filename != "SKILL.md" or "/" not in parent:
            continue
        root, _slash, folder = parent.rpartition("/")
        if root not in roots or folder == "" or "/" in folder:
            continue
        tree_sha = trees.get(parent)
        if tree_sha:
            found.append((parent, folder, path, tree_sha))
    return found


def _frontmatter(text: str) -> tuple[str | None, str | None]:
    if not text.startswith("---"):
        return None, None
    end = text.find("\n---", 3)
    if end < 0:
        return None, None
    name: str | None = None
    version: str | None = None
    for line in text[3:end].splitlines():
        if line.startswith("name:"):
            name = _unquote(line.split(":", 1)[1].strip())
        stripped = line.strip()
        if stripped.startswith("version:"):
            version = _unquote(stripped.split(":", 1)[1].strip())
    return name, version


def _unquote(value: str) -> str:
    quote = ""
    kept: list[str] = []
    for char in value:
        if quote:
            kept.append(char)
            if char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
            kept.append(char)
            continue
        if char == "#":
            break
        kept.append(char)
    text = "".join(kept).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text
