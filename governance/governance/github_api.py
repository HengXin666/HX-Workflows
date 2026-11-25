"""GitHub REST and GraphQL adapter for governance. Callers pass tokens; this module never logs them."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

from governance.decide import RepoSnapshot
from governance.labels import Label

ACCEPT = "application/vnd.github+json"
API_VERSION = "2022-11-28"
USER_AGENT = "hx-workflows-governance"
REPORT_LABEL = "governance-report"

SETTING_FIELDS = {
    "deleteBranchOnMerge": "delete_branch_on_merge",
    "squashMergeAllowed": "allow_squash_merge",
    "mergeCommitAllowed": "allow_merge_commit",
    "rebaseMergeAllowed": "allow_rebase_merge",
    "squashMergeCommitTitle": "squash_merge_commit_title",
    "squashMergeCommitMessage": "squash_merge_commit_message",
}
BLOB = "... on Blob { text isBinary }"
SNAPSHOT_QUERY = """
query($owner: String!, $name: String!, $setup: String!, $first: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    name isPrivate isFork isArchived isEmpty pushedAt
    deleteBranchOnMerge squashMergeAllowed mergeCommitAllowed rebaseMergeAllowed
    squashMergeCommitTitle squashMergeCommitMessage
    defaultBranchRef {
      name
      target { ... on Commit { oid history(first: $first, after: $after) {
        nodes { message }
        pageInfo { hasNextPage endCursor }
      } } }
    }
    labels(first: 100) { nodes { name color description } pageInfo { hasNextPage } }
    github: object(expression: "HEAD:.github") { ... on Tree { entries {
      name type mode object { BLOB ... on Tree { entries {
        name type mode object { BLOB ... on Tree { entries { name type mode } } }
      } } }
    } } }
    rootPr1: object(expression: "HEAD:pull_request_template.md") { BLOB }
    rootPr2: object(expression: "HEAD:PULL_REQUEST_TEMPLATE.md") { BLOB }
    docsPr1: object(expression: "HEAD:docs/pull_request_template.md") { BLOB }
    docsPr2: object(expression: "HEAD:docs/PULL_REQUEST_TEMPLATE.md") { BLOB }
    pullRequests(states: OPEN, headRefName: $setup, first: 5) { nodes { number url } }
  }
}
""".replace("BLOB", BLOB)
HISTORY_QUERY = """
query($owner: String!, $name: String!, $first: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { target { ... on Commit { history(first: $first, after: $after) {
      nodes { message }
      pageInfo { hasNextPage endCursor }
    } } } }
  }
}
"""


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


class GitHub:
    def __init__(self, token: str, base_url: str = "https://api.github.com") -> None:
        self._token = token
        self._base = base_url.rstrip("/")

    def request(self, method: str, path: str, *, json_body: dict[str, Any] | None = None) -> Any:
        url = path if path.startswith("https://") else self._base + path
        payload = None if json_body is None else json.dumps(json_body).encode("utf-8")
        last_error: GitHubError | None = None
        for attempt in range(3):
            request = urllib.request.Request(url, data=payload, method=method)
            request.add_header("Authorization", f"Bearer {self._token}")
            request.add_header("Accept", ACCEPT)
            request.add_header("User-Agent", USER_AGENT)
            request.add_header("X-GitHub-Api-Version", API_VERSION)
            if payload is not None:
                request.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    body = response.read()
                    return json.loads(body.decode("utf-8")) if body else None
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")[:500]
                last_error = GitHubError(error.code, f"GitHub {method} {path} 返回 {error.code}: {detail}")
                if method == "GET" and error.code in {429, 502, 503} and attempt < 2:
                    time.sleep(2**attempt)
                    continue
                raise last_error from None
            except (urllib.error.URLError, TimeoutError, OSError):
                last_error = GitHubError(0, f"GitHub {method} {path} 网络失败")
                if method == "GET" and attempt < 2:
                    time.sleep(2**attempt)
                    continue
                raise last_error from None
        raise last_error or GitHubError(0, f"GitHub {method} {path} 没有返回")

    def graphql(self, query: str, variables: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        payload = self.request("POST", "/graphql", json_body={"query": query, "variables": variables})
        if not isinstance(payload, dict):
            raise GitHubError(0, "GraphQL 返回无法解析")
        return payload.get("data") or {}, payload.get("errors") or []

    def paginate(self, path: str, params: dict[str, str] | None = None) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            query = dict(params or {})
            query["per_page"] = "100"
            query["page"] = str(page)
            batch = self.request("GET", path + "?" + urllib.parse.urlencode(query))
            if not isinstance(batch, list) or not batch:
                return
            yield from (item for item in batch if isinstance(item, dict))
            if len(batch) < 100:
                return
            page += 1

    # ------------------------------------------------------------------ reading

    def list_owner_repos(self, owner: str) -> list[dict[str, Any]]:
        return [
            repo
            for repo in self.paginate("/user/repos", {"affiliation": "owner", "visibility": "all"})
            if str((repo.get("owner") or {}).get("login") or "") == owner
        ]

    def snapshot(self, payload: dict[str, Any], setup_branch: str, sample: int) -> RepoSnapshot:
        owner = str((payload.get("owner") or {}).get("login") or "")
        name = str(payload.get("name") or "")
        snap = RepoSnapshot(
            owner=owner,
            repo=name,
            default_branch=str(payload.get("default_branch") or "main"),
            private=bool(payload.get("private")),
            fork=bool(payload.get("fork")),
            archived=bool(payload.get("archived")),
            pushed_at=str(payload.get("pushed_at") or "") or None,
        )
        first = min(100, sample)
        data, errors = self.graphql(
            SNAPSHOT_QUERY,
            {"owner": owner, "name": name, "setup": setup_branch, "first": first, "after": None},
        )
        repo = data.get("repository") if isinstance(data, dict) else None
        if not isinstance(repo, dict):
            detail = "; ".join(str(item.get("message")) for item in errors) or "没有数据"
            snap.errors.append(f"读取仓库失败: {detail}")
            return snap
        for item in errors:
            path = [str(part) for part in item.get("path") or []]
            if path[-1:] and path[-1].startswith(("rootPr", "docsPr", "github")):
                continue
            snap.errors.append(f"读取 {'.'.join(path) or '仓库'} 失败: {item.get('message')}")
        snap.empty = bool(repo.get("isEmpty"))
        snap.private = bool(repo.get("isPrivate"))
        snap.archived = bool(repo.get("isArchived"))
        for field_name, key in SETTING_FIELDS.items():
            snap.settings[key] = repo.get(field_name)
        ref = repo.get("defaultBranchRef") or {}
        if ref.get("name"):
            snap.default_branch = str(ref["name"])
        target = ref.get("target") or {}
        snap.head_sha = str(target.get("oid") or "")
        history = target.get("history") or {}
        snap.headers = [_headline(node.get("message")) for node in history.get("nodes") or []]
        info = history.get("pageInfo") or {}
        while info.get("hasNextPage") and len(snap.headers) < sample:
            more, more_errors = self.graphql(
                HISTORY_QUERY,
                {"owner": owner, "name": name, "first": min(100, sample - len(snap.headers)), "after": info.get("endCursor")},
            )
            if more_errors:
                snap.errors.append(f"读取提交历史失败: {more_errors[0].get('message')}")
                break
            page = ((((more.get("repository") or {}).get("defaultBranchRef") or {}).get("target") or {}).get("history")) or {}
            snap.headers += [_headline(node.get("message")) for node in page.get("nodes") or []]
            info = page.get("pageInfo") or {}
        labels = repo.get("labels") or {}
        snap.labels = [
            Label(str(node.get("name") or ""), str(node.get("color") or ""), str(node.get("description") or ""))
            for node in labels.get("nodes") or []
        ]
        if (labels.get("pageInfo") or {}).get("hasNextPage"):
            snap.labels = [
                Label(str(item.get("name") or ""), str(item.get("color") or ""), str(item.get("description") or ""))
                for item in self.paginate(f"/repos/{owner}/{name}/labels")
            ]
        _collect_tree(repo.get("github"), ".github", snap)
        for alias, path in (
            ("rootPr1", "pull_request_template.md"),
            ("rootPr2", "PULL_REQUEST_TEMPLATE.md"),
            ("docsPr1", "docs/pull_request_template.md"),
            ("docsPr2", "docs/PULL_REQUEST_TEMPLATE.md"),
        ):
            node = repo.get(alias)
            if isinstance(node, dict) and "text" in node:
                snap.files[path] = None if node.get("isBinary") else node.get("text")
        pulls = (repo.get("pullRequests") or {}).get("nodes") or []
        snap.setup_pr = pulls[0] if pulls else None
        if not snap.private:
            snap.rulesets = self.rulesets(owner, name, snap.errors)
        return snap

    def rulesets(self, owner: str, repo: str, errors: list[str]) -> list[dict] | None:
        try:
            found = []
            for item in self.paginate(f"/repos/{owner}/{repo}/rulesets", {"includes_parents": "false"}):
                detail = self.request("GET", f"/repos/{owner}/{repo}/rulesets/{item.get('id')}")
                found.append(
                    {
                        "id": detail.get("id"),
                        "name": detail.get("name"),
                        "enforcement": detail.get("enforcement"),
                        "rule_types": [rule.get("type") for rule in detail.get("rules") or []],
                    }
                )
            return found
        except GitHubError as error:
            errors.append(f"读取规则集失败 ({error.status})")
            return None

    def get_issue(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        return self.request("GET", f"/repos/{owner}/{repo}/issues/{number}")

    def list_open_report_issues(self, owner: str, repo: str) -> list[dict[str, Any]]:
        return [
            issue
            for issue in self.paginate(f"/repos/{owner}/{repo}/issues", {"state": "open", "labels": REPORT_LABEL})
            if "pull_request" not in issue
        ]

    # ------------------------------------------------------------------ writing to target repositories

    def patch_repo(self, owner: str, repo: str, body: dict[str, Any]) -> None:
        self.request("PATCH", f"/repos/{owner}/{repo}", json_body=body)

    def create_label(self, owner: str, repo: str, name: str, color: str, description: str) -> None:
        self.request(
            "POST",
            f"/repos/{owner}/{repo}/labels",
            json_body={"name": name, "color": color, "description": description},
        )

    def update_label(self, owner: str, repo: str, name: str, new_name: str, color: str, description: str) -> None:
        self.request(
            "PATCH",
            f"/repos/{owner}/{repo}/labels/{quote(name)}",
            json_body={"new_name": new_name, "color": color, "description": description},
        )

    def ref_sha(self, owner: str, repo: str, branch: str) -> str | None:
        try:
            ref = self.request("GET", f"/repos/{owner}/{repo}/git/ref/heads/{quote(branch)}")
        except GitHubError as error:
            if error.status == 404:
                return None
            raise
        return str((ref.get("object") or {}).get("sha") or "") or None

    def commit_tree(self, owner: str, repo: str, sha: str) -> str:
        commit = self.request("GET", f"/repos/{owner}/{repo}/git/commits/{sha}")
        return str((commit.get("tree") or {}).get("sha") or "")

    def create_tree(self, owner: str, repo: str, base_tree: str, entries: list[dict[str, Any]]) -> str:
        created = self.request(
            "POST", f"/repos/{owner}/{repo}/git/trees", json_body={"base_tree": base_tree, "tree": entries}
        )
        return str(created.get("sha") or "")

    def create_commit(self, owner: str, repo: str, message: str, tree: str, parent: str) -> str:
        created = self.request(
            "POST",
            f"/repos/{owner}/{repo}/git/commits",
            json_body={"message": message, "tree": tree, "parents": [parent]},
        )
        return str(created.get("sha") or "")

    def create_ref(self, owner: str, repo: str, branch: str, sha: str) -> None:
        self.request("POST", f"/repos/{owner}/{repo}/git/refs", json_body={"ref": f"refs/heads/{branch}", "sha": sha})

    def move_ref(self, owner: str, repo: str, branch: str, sha: str, force: bool) -> None:
        self.request(
            "PATCH",
            f"/repos/{owner}/{repo}/git/refs/heads/{quote(branch)}",
            json_body={"sha": sha, "force": force},
        )

    def create_pull(self, owner: str, repo: str, title: str, body: str, head: str, base: str) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json_body={"title": title, "body": body, "head": head, "base": base, "maintainer_can_modify": True},
        )

    def open_pull(self, owner: str, repo: str, head: str) -> dict[str, Any] | None:
        for pull in self.paginate(f"/repos/{owner}/{repo}/pulls", {"state": "open", "head": f"{owner}:{head}"}):
            return pull
        return None

    def create_ruleset(self, owner: str, repo: str, body: dict[str, Any]) -> None:
        self.request("POST", f"/repos/{owner}/{repo}/rulesets", json_body=body)

    def update_ruleset(self, owner: str, repo: str, ruleset_id: int, body: dict[str, Any]) -> None:
        self.request("PUT", f"/repos/{owner}/{repo}/rulesets/{ruleset_id}", json_body=body)

    # ------------------------------------------------------------------ the review issue

    def ensure_label(self, owner: str, repo: str, name: str, color: str, description: str) -> None:
        try:
            self.create_label(owner, repo, name, color, description)
        except GitHubError as error:
            if error.status != 422:
                raise

    def create_issue(self, owner: str, repo: str, title: str, body: str) -> dict[str, Any]:
        return self.request(
            "POST", f"/repos/{owner}/{repo}/issues", json_body={"title": title, "body": body, "labels": [REPORT_LABEL]}
        )

    def update_issue(self, owner: str, repo: str, number: int, *, body: str | None = None, state: str | None = None) -> None:
        payload: dict[str, Any] = {}
        if body is not None:
            payload["body"] = body
        if state is not None:
            payload["state"] = state
        self.request("PATCH", f"/repos/{owner}/{repo}/issues/{number}", json_body=payload)

    def comment(self, owner: str, repo: str, number: int, body: str) -> None:
        self.request("POST", f"/repos/{owner}/{repo}/issues/{number}/comments", json_body={"body": body})

    def remove_label(self, owner: str, repo: str, number: int, name: str) -> None:
        try:
            self.request("DELETE", f"/repos/{owner}/{repo}/issues/{number}/labels/{quote(name)}")
        except GitHubError as error:
            if error.status != 404:
                raise


def _headline(message: object) -> str:
    text = str(message or "")
    return text.split("\n", 1)[0].rstrip("\r").rstrip()


def _mode(value: object) -> str:
    try:
        return format(int(value), "o").rjust(6, "0")
    except (TypeError, ValueError):
        return ""


def _collect_tree(node: object, prefix: str, snap: RepoSnapshot) -> None:
    if not isinstance(node, dict):
        return
    for entry in node.get("entries") or []:
        path = f"{prefix}/{entry.get('name')}"
        kind = entry.get("type")
        obj = entry.get("object") or {}
        if kind == "blob":
            snap.files[path] = None if obj.get("isBinary") else obj.get("text")
            snap.modes[path] = _mode(entry.get("mode"))
        elif kind == "tree":
            if obj.get("entries") is not None:
                _collect_tree(obj, path, snap)
            else:
                snap.files.setdefault(path + "/", None)
