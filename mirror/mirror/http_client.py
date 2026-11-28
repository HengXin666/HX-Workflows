"""Small JSON-over-HTTP adapter shared by every platform. Error messages never contain tokens or query strings."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

RETRY_STATUS = {429, 500, 502, 503, 504}
USER_AGENT = "hx-workflows-mirror"


class HttpError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


class JsonClient:
    def __init__(
        self,
        base_url: str,
        headers: dict[str, str] | None = None,
        query: dict[str, str] | None = None,
        timeout: int = 60,
        retries: int = 3,
        sleep: float = 1.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._headers = dict(headers or {})
        self._query = dict(query or {})
        self._timeout = timeout
        self._retries = retries
        self._sleep = sleep

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        query = {**self._query, **(params or {})}
        url = self._base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        payload = None if body is None else json.dumps(body).encode("utf-8")
        safe = method in {"GET", "HEAD"}
        attempts = self._retries + 1 if safe else 1
        for attempt in range(attempts):
            request = urllib.request.Request(url, data=payload, method=method)
            request.add_header("User-Agent", USER_AGENT)
            for key, value in self._headers.items():
                request.add_header(key, value)
            if payload is not None and "Content-Type" not in self._headers:
                request.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    raw = response.read()
                    if not raw:
                        return None
                    try:
                        return json.loads(raw.decode("utf-8"))
                    except ValueError:
                        return None
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")[:300]
                if error.code in RETRY_STATUS and attempt + 1 < attempts:
                    time.sleep(self._sleep * 2**attempt)
                    continue
                raise HttpError(error.code, f"{method} {path} 返回 {error.code}: {detail}") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt + 1 < attempts:
                    time.sleep(self._sleep * 2**attempt)
                    continue
                raise HttpError(0, f"{method} {path} 网络失败") from None
        raise HttpError(0, f"{method} {path} 没有返回")

    def get_or_none(self, path: str, params: dict[str, str] | None = None) -> Any:
        try:
            return self.request("GET", path, params=params)
        except HttpError as error:
            if error.status == 404:
                return None
            raise

    def paginate(self, path: str, params: dict[str, str] | None = None, per_page: int = 100) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            query = {**(params or {}), "per_page": str(per_page), "page": str(page)}
            batch = self.request("GET", path, params=query)
            if not isinstance(batch, list) or not batch:
                return items
            items.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < per_page:
                return items
            page += 1
