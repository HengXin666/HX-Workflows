"""Target platform adapters: GitLab, Gitee, Codeberg / Forgejo / Gitea and CNB."""

from __future__ import annotations

from typing import Any, Protocol

from mirror.config import TargetConfig
from mirror.http_client import HttpError, JsonClient, quote
from mirror.plan import RemoteRepo


class Target(Protocol):
    config: TargetConfig

    def prepare(self) -> None: ...
    def get(self, name: str) -> RemoteRepo | None: ...
    def create(self, name: str, private: bool, description: str) -> None: ...
    def set_private(self, name: str, private: bool) -> bool: ...
    def before_push(self, name: str) -> None: ...
    def set_default_branch(self, name: str, branch: str) -> bool: ...
    def push_url(self, name: str) -> str: ...
    def credentials(self) -> tuple[str, str]: ...


class GitLabTarget:
    def __init__(self, config: TargetConfig, token: str, client: JsonClient | None = None) -> None:
        self.config = config
        self._token = token
        self._client = client or JsonClient(config.api_url, headers={"PRIVATE-TOKEN": token})
        self._namespace_id: int | None = None

    def _project(self, name: str) -> str:
        return quote(f"{self.config.owner}/{name}")

    def prepare(self) -> None:
        self._client.request("GET", "/user")
        namespace = self._client.request("GET", f"/namespaces/{quote(self.config.owner)}")
        if not isinstance(namespace, dict) or not namespace.get("id"):
            raise HttpError(404, f"GitLab 找不到命名空间 {self.config.owner}")
        self._namespace_id = int(namespace["id"])

    def get(self, name: str) -> RemoteRepo | None:
        project = self._client.get_or_none(f"/projects/{self._project(name)}")
        if not isinstance(project, dict):
            return None
        return RemoteRepo(
            name=name,
            private=project.get("visibility") != "public",
            default_branch=str(project.get("default_branch") or ""),
        )

    def create(self, name: str, private: bool, description: str) -> None:
        body: dict[str, Any] = {
            "name": name,
            "path": name,
            "visibility": "private" if private else "public",
            "description": description[:2000],
            "initialize_with_readme": False,
        }
        if self._namespace_id is not None:
            body["namespace_id"] = self._namespace_id
        self._client.request("POST", "/projects", body=body)

    def set_private(self, name: str, private: bool) -> bool:
        self._client.request("PUT", f"/projects/{self._project(name)}", body={"visibility": "private" if private else "public"})
        return True

    def before_push(self, name: str) -> None:
        """Mirror pushes rewrite history and tags, so protected refs must allow force push."""
        project = self._project(name)
        for branch in self._client.paginate(f"/projects/{project}/protected_branches"):
            if branch.get("allow_force_push"):
                continue
            self._client.request(
                "PATCH",
                f"/projects/{project}/protected_branches/{quote(str(branch.get('name')))}",
                body={"allow_force_push": True},
            )
        for tag in self._client.paginate(f"/projects/{project}/protected_tags"):
            self._client.request("DELETE", f"/projects/{project}/protected_tags/{quote(str(tag.get('name')))}")

    def set_default_branch(self, name: str, branch: str) -> bool:
        self._client.request("PUT", f"/projects/{self._project(name)}", body={"default_branch": branch})
        return True

    def push_url(self, name: str) -> str:
        return f"{self.config.base_url}/{self.config.owner}/{name}.git"

    def credentials(self) -> tuple[str, str]:
        return "oauth2", self._token


class GiteeTarget:
    def __init__(self, config: TargetConfig, token: str, client: JsonClient | None = None) -> None:
        self.config = config
        self._token = token
        self._client = client or JsonClient(config.api_url, query={"access_token": token})
        self._login = config.username

    def _path(self, name: str) -> str:
        return f"/repos/{quote(self.config.owner)}/{quote(name)}"

    def prepare(self) -> None:
        user = self._client.request("GET", "/user")
        self._login = self.config.username or str((user or {}).get("login") or "")
        if not self._login:
            raise HttpError(0, "Gitee 令牌没有返回用户名")

    def get(self, name: str) -> RemoteRepo | None:
        repo = self._client.get_or_none(self._path(name))
        if not isinstance(repo, dict):
            return None
        return RemoteRepo(name=name, private=bool(repo.get("private")), default_branch=str(repo.get("default_branch") or ""))

    def create(self, name: str, private: bool, description: str) -> None:
        body = {"name": name, "path": name, "private": private, "description": description[:200], "auto_init": False}
        if self._login.lower() == self.config.owner.lower():
            self._client.request("POST", "/user/repos", body=body)
        else:
            self._client.request("POST", f"/orgs/{quote(self.config.owner)}/repos", body=body)

    def set_private(self, name: str, private: bool) -> bool:
        self._client.request("PATCH", self._path(name), body={"name": name, "private": private})
        return True

    def before_push(self, name: str) -> None:
        return None

    def set_default_branch(self, name: str, branch: str) -> bool:
        self._client.request("PATCH", self._path(name), body={"name": name, "default_branch": branch})
        return True

    def push_url(self, name: str) -> str:
        return f"{self.config.base_url}/{self.config.owner}/{name}.git"

    def credentials(self) -> tuple[str, str]:
        return self._login, self._token


class ForgejoTarget:
    """Codeberg, self-hosted Forgejo and Gitea share the same API."""

    def __init__(self, config: TargetConfig, token: str, client: JsonClient | None = None) -> None:
        self.config = config
        self._token = token
        self._client = client or JsonClient(config.api_url, headers={"Authorization": f"token {token}"})
        self._login = config.username

    def _path(self, name: str) -> str:
        return f"/repos/{quote(self.config.owner)}/{quote(name)}"

    def prepare(self) -> None:
        user = self._client.request("GET", "/user")
        self._login = self.config.username or str((user or {}).get("login") or "")
        if not self._login:
            raise HttpError(0, f"{self.config.provider} 令牌没有返回用户名")

    def get(self, name: str) -> RemoteRepo | None:
        repo = self._client.get_or_none(self._path(name))
        if not isinstance(repo, dict):
            return None
        return RemoteRepo(name=name, private=bool(repo.get("private")), default_branch=str(repo.get("default_branch") or ""))

    def create(self, name: str, private: bool, description: str) -> None:
        body = {"name": name, "private": private, "description": description[:2000], "auto_init": False}
        if self._login.lower() == self.config.owner.lower():
            self._client.request("POST", "/user/repos", body=body)
        else:
            self._client.request("POST", f"/orgs/{quote(self.config.owner)}/repos", body=body)

    def set_private(self, name: str, private: bool) -> bool:
        self._client.request("PATCH", self._path(name), body={"private": private})
        return True

    def before_push(self, name: str) -> None:
        return None

    def set_default_branch(self, name: str, branch: str) -> bool:
        self._client.request("PATCH", self._path(name), body={"default_branch": branch})
        return True

    def push_url(self, name: str) -> str:
        return f"{self.config.base_url}/{self.config.owner}/{name}.git"

    def credentials(self) -> tuple[str, str]:
        return self._login, self._token


class CnbTarget:
    """CNB repositories live under an organization (group) path. Its OpenAPI cannot change visibility after creation."""

    def __init__(self, config: TargetConfig, token: str, client: JsonClient | None = None) -> None:
        self.config = config
        self._token = token
        self._client = client or JsonClient(
            config.api_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.cnb.api+json"},
        )

    def _slug(self, name: str) -> str:
        return "/".join(quote(part) for part in f"{self.config.owner}/{name}".split("/"))

    def prepare(self) -> None:
        self._client.request("GET", "/user")

    def get(self, name: str) -> RemoteRepo | None:
        repo = self._client.get_or_none("/" + self._slug(name))
        if not isinstance(repo, dict):
            return None
        level = str(repo.get("visibility_level") or "")
        return RemoteRepo(name=name, private=None if not level else level.lower() != "public")

    def create(self, name: str, private: bool, description: str) -> None:
        owner = "/".join(quote(part) for part in self.config.owner.split("/"))
        body = {"name": name, "description": description[:350], "visibility": "private" if private else "public"}
        self._client.request("POST", f"/{owner}/-/repos", body=body)

    def set_private(self, name: str, private: bool) -> bool:
        return False

    def before_push(self, name: str) -> None:
        return None

    def set_default_branch(self, name: str, branch: str) -> bool:
        return False

    def push_url(self, name: str) -> str:
        return f"{self.config.base_url}/{self.config.owner}/{name}.git"

    def credentials(self) -> tuple[str, str]:
        return "cnb", self._token


def build_target(config: TargetConfig, token: str) -> Target:
    if config.provider == "gitlab":
        return GitLabTarget(config, token)
    if config.provider == "gitee":
        return GiteeTarget(config, token)
    if config.provider in {"codeberg", "forgejo", "gitea"}:
        return ForgejoTarget(config, token)
    if config.provider == "cnb":
        return CnbTarget(config, token)
    raise ValueError(f"不支持的平台 {config.provider}")
