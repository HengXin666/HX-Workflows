"""GitHub source adapter: list repositories of a user or an organization, private ones included."""

from __future__ import annotations

from typing import Any

from mirror.config import SourceConfig
from mirror.http_client import JsonClient, quote
from mirror.plan import SourceRepo


def github_client(source: SourceConfig, token: str) -> JsonClient:
    return JsonClient(
        source.api_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def _repo(item: dict[str, Any]) -> SourceRepo:
    return SourceRepo(
        name=str(item.get("name") or ""),
        private=bool(item.get("private")),
        fork=bool(item.get("fork")),
        archived=bool(item.get("archived")),
        empty=int(item.get("size") or 0) == 0,
        description=str(item.get("description") or ""),
        default_branch=str(item.get("default_branch") or ""),
    )


class GitHubSource:
    def __init__(self, source: SourceConfig, token: str, client: JsonClient | None = None) -> None:
        self._source = source
        self._token = token
        self._client = client or github_client(source, token)

    def list_repos(self) -> list[SourceRepo]:
        me = self._client.request("GET", "/user")
        login = str((me or {}).get("login") or "")
        owner = self._source.owner
        if login.lower() == owner.lower():
            items = self._client.paginate("/user/repos", {"affiliation": "owner", "visibility": "all"})
        else:
            items = self._client.paginate(f"/orgs/{quote(owner)}/repos", {"type": "all"})
        repos = [_repo(item) for item in items if str((item.get("owner") or {}).get("login", "")).lower() == owner.lower()]
        return sorted((repo for repo in repos if repo.name), key=lambda repo: repo.name.lower())

    def clone_url(self, name: str) -> str:
        return f"{self._source.base_url}/{self._source.owner}/{name}.git"

    def credentials(self) -> tuple[str, str]:
        return "x-access-token", self._token
