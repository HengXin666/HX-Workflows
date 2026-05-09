"""
B站 Token 管理 — 从 HX-Git-DB 读取，支持自动刷新

核心入口:
  mgr = BiliTokenManager()
  token = mgr.get_token()        # 从 HX-Git-DB 获取有效 token
  token = mgr.refresh()           # 强制刷新并写回 HX-Git-DB
  info  = mgr.check_status()      # 检查 token 有效期

Token 数据存储在 __HX-Data__ 的 bilibili-token 分支中，格式：
  {
    "access_token": "...",
    "refresh_token": "...",
    "mid": 12345,
    "uname": "...",
    "expires_in": 7776000,
    "saved_at": 1712345678
  }
"""

import json
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from ._core import BiliSigner, make_client

DATA_REPO = "https://github.com/HengXin666/__HX-Data__.git"
DATA_BRANCH = "bilibili-token"
COOKIE_KEY = "cookie.json"


class BiliTokenManager:
    """
    B站 Token 管理器

    从 HX-Git-DB 读取 OAuth token，提供自动刷新和状态检查。

    用法:
        mgr = BiliTokenManager()

        # 获取 token（自动检查有效期，不足7天自动刷新）
        t = mgr.get_token()
        print(t["access_token"])

        # 强制刷新
        mgr.refresh()

        # 检查状态
        info = mgr.check_status()
    """

    def __init__(self, *, repo: str = DATA_REPO, branch: str = DATA_BRANCH,
                 auto_refresh_days: int = 7, token: Optional[str] = None):
        self._repo = repo
        self._branch = branch
        self._auto_refresh_days = auto_refresh_days
        self._token = token  # GitHub PAT（None = 自动从 GITHUB_TOKEN 环境变量读取）

        self._data: dict = {}
        self._client: Optional[httpx.Client] = None
        self._signer = BiliSigner()

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = make_client()
        return self._client

    # ========== 公开 API ==========

    def get_token(self) -> dict:
        """从 HX-Git-DB 获取 token，自动检查有效期并刷新"""
        if not self._data:
            self._load_from_db()

        if not self._data.get("access_token"):
            raise RuntimeError("HX-Git-DB 中无有效 token，请先完成首次扫码登录")

        # 检查是否需要刷新
        if self._should_refresh():
            self.refresh()

        return {
            "access_token": self._data.get("access_token"),
            "refresh_token": self._data.get("refresh_token"),
            "mid": self._data.get("mid"),
            "uname": self._data.get("uname"),
            "expires_in": self._data.get("expires_in"),
        }

    def refresh(self) -> dict:
        """强制刷新 token 并写回 HX-Git-DB，返回新的 token"""
        if not self._data.get("refresh_token"):
            self._load_from_db()
        if not self._data.get("refresh_token"):
            raise RuntimeError("没有 refresh_token，请重新登录")

        resp = self.client.post(
            "https://passport.bilibili.com/x/passport-login/oauth2/refresh_token",
            data=self._signer.sign({
                "access_token": self._data["access_token"],
                "refresh_token": self._data["refresh_token"],
            }),
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"刷新 Token 失败: {data.get('message')}")

        resp_data = data["data"]
        # B站 OAuth2 刷新返回嵌套 {token_info: {access_token, ...}} 结构
        if "token_info" in resp_data:
            ti = resp_data["token_info"]
            self._data = {
                "access_token": ti["access_token"],
                "refresh_token": ti["refresh_token"],
                "mid": ti.get("mid", self._data.get("mid")),
                "uname": self._data.get("uname"),
                "expires_in": self._normalize_expires(ti.get("expires_in", 0)),
            }
        else:
            self._data = resp_data
        self._data["saved_at"] = int(time.time())
        self._save_to_db()
        return self.get_token()

    @staticmethod
    def _normalize_expires(expires_in: int) -> int:
        """标准化 expires_in 为剩余秒数。
        B站不同接口返回格式不同：有的返回 UTC 时间戳 (> 10^9)，
        有的返回剩余秒数。统一转为剩余秒数。"""
        if expires_in > 1_000_000_000:
            return max(0, expires_in - int(time.time()))
        return expires_in

    def check_status(self) -> dict:
        """检查 token 在 B站 服务端的状态"""
        self.get_token()  # 确保已加载
        resp = self.client.get(
            "https://passport.bilibili.com/x/passport-login/oauth2/info",
            params=self._signer.sign({"access_token": self._data["access_token"]}),
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"检查 Token 状态失败: {data.get('message')}")
        return data["data"]

    # ========== 内部方法 ==========

    def _load_from_db(self):
        """从 HX-Git-DB 读取 token"""
        from hx_git_db import make_database

        db = make_database(self._repo, self._branch, only=True, token=self._token)
        try:
            with db.open(COOKIE_KEY) as f:
                self._data = f.read_json() or {}
        finally:
            try:
                db.cleanup()
            except PermissionError:
                pass  # Windows .git 文件锁定，不影响数据读取

        # 兼容旧代码写入的损坏格式（仅有嵌套 token_info，无顶层 access_token）
        if not self._data.get("access_token") and "token_info" in self._data:
            ti = self._data["token_info"]
            self._data = {
                "access_token": ti.get("access_token", ""),
                "refresh_token": ti.get("refresh_token", ""),
                "mid": ti.get("mid", self._data.get("mid")),
                "uname": self._data.get("uname"),
                "expires_in": self._normalize_expires(ti.get("expires_in", 0)),
            }

    def _save_to_db(self):
        """写回 HX-Git-DB"""
        from hx_git_db import make_database

        db = make_database(self._repo, self._branch, only=True, token=self._token)
        try:
            with db.open(COOKIE_KEY) as f:
                f.write_json(self._data)
            db.push()
        finally:
            try:
                db.cleanup()
            except PermissionError:
                pass  # Windows .git 文件锁定，不影响数据写入

    def _should_refresh(self) -> bool:
        """检查 token 是否需要刷新"""
        expires_in = self._data.get("expires_in", 0)
        if expires_in <= 0:
            return True
        # 不足 auto_refresh_days 天就刷新
        return (expires_in / 86400) < self._auto_refresh_days

    # ========== 便利属性 ==========

    @property
    def mid(self) -> int:
        return self.get_token().get("mid", 0)

    @property
    def uname(self) -> str:
        return self.get_token().get("uname", "")

    @property
    def access_token(self) -> str:
        return self.get_token().get("access_token", "")

    def close(self):
        if self._client:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
