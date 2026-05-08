"""
B站 API 封装

提供视频信息、播放地址、用户信息等核心 API，
自动处理 WBI 签名和 Token 失效重试。

用法:
    from bilibili_api.token import BiliTokenManager
    from bilibili_api.api import BiliAPI

    token_mgr = BiliTokenManager()
    api = BiliAPI(token_mgr)

    info = api.get_video_info("BV1xx411c7mD")
    play = api.get_play_url(info["aid"], info["cid"])
    user = api.get_user_info()
"""

import time
from pathlib import Path
from typing import Optional

import httpx

from _core import BiliSigner, WBISigner, make_client
from bili_token import BiliTokenManager


class BiliAPI:
    """
    B站 API 客户端

    封装视频信息、播放地址、用户信息、收藏夹等 API，
    自动处理 WBI 签名和 Token 失效（-101 错误码）重试。
    """

    def __init__(self, token_mgr: BiliTokenManager):
        self._token = token_mgr
        self._signer = BiliSigner()
        self._wbi = WBISigner()
        self._client: Optional[httpx.Client] = None
        self._wbi_initialized = False

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = make_client()
        return self._client

    # ========== 用户信息 ==========

    def get_user_info(self) -> dict:
        """获取当前用户信息，同时初始化 WBI 签名密钥"""
        token = self._token.get_token()
        params = self._signer.sign({"access_key": token["access_token"]})
        resp = self.client.get("https://api.bilibili.com/x/web-interface/nav", params=params)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取用户信息失败: {data.get('message')}")

        user_data = data["data"]

        # 初始化 WBI 密钥（后续视频/评论等 API 需要）
        wbi_img = user_data.get("wbi_img", {})
        img_url = wbi_img.get("img_url", "")
        sub_url = wbi_img.get("sub_url", "")
        if img_url and sub_url:
            img_key = Path(img_url).stem.split(".")[0]
            sub_key = Path(sub_url).stem.split(".")[0]
            self._wbi.set_keys(img_key, sub_key)
            self._wbi_initialized = True

        return user_data

    # ========== 视频信息 ==========

    def get_video_info(self, bvid: str) -> dict:
        """获取视频详细信息（含 aid, cid, title, pic 等）"""
        self._ensure_wbi()
        params = self._wbi.sign_params({"bvid": bvid})
        resp = self._request_with_retry("GET",
            "https://api.bilibili.com/x/web-interface/view", params=params)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取视频信息失败: {data.get('message')}")
        return data["data"]

    def get_play_url(self, aid: int, cid: int, qn: int = 80) -> dict:
        """获取视频播放 URL（DASH 格式），qn 默认 80 = 1080P"""
        self._ensure_wbi()
        params = self._wbi.sign_params({
            "avid": aid,
            "cid": cid,
            "fnval": 16 | 128,
            "fourk": 1,
            "qn": qn,
        }, time.time())
        resp = self._request_with_retry("GET",
            "https://api.bilibili.com/x/player/playurl", params=params)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取播放URL失败: {data.get('message')}")
        return data["data"]

    def get_video_pages(self, bvid: str) -> list:
        """获取视频分P列表 [{"cid": ..., "page": ..., "part": ...}, ...]"""
        info = self.get_video_info(bvid)
        return info.get("pages", [])

    # ========== 评论 ==========

    def get_comments(self, oid: int, page: int = 1, sort: int = 1, ps: int = 20) -> dict:
        """
        获取视频评论

        Args:
            oid: 视频 aid
            page: 页码
            sort: 0=按时间, 1=按热度
            ps: 每页条数
        """
        self._ensure_wbi()
        params = self._wbi.sign_params({
            "oid": oid,
            "type": 1,
            "mode": 3,
            "pagination_str": f'{{"offset":"{{\\"type\\":1,\\"direction\\":1,\\"data\\":{{}}}}\\","page":{page}}}',
            "sort": sort,
            "ps": ps,
        })
        resp = self._request_with_retry("GET",
            "https://api.bilibili.com/x/v2/reply/wbi/main", params=params)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取评论失败: {data.get('message')}")
        return data["data"]

    def get_top_comments(self, oid: int, count: int = 10) -> list:
        """获取 Top N 热门评论"""
        result = []
        data = self.get_comments(oid, page=1, sort=1, ps=min(count, 20))
        replies = data.get("replies") or []
        for r in replies[:count]:
            result.append({
                "rpid": r.get("rpid"),
                "mid": r.get("mid"),
                "uname": r.get("member", {}).get("uname", ""),
                "avatar": r.get("member", {}).get("avatar", ""),
                "content": r.get("content", {}).get("message", ""),
                "like": r.get("like", 0),
                "ctime": r.get("ctime", 0),
                "replies_count": r.get("rcount", 0),
            })
        return result

    # ========== 收藏夹 ==========

    def get_fav_list(self, up_mid: Optional[int] = None) -> list:
        """获取收藏夹列表"""
        mid = up_mid or self._token.mid
        params = {"up_mid": mid}
        resp = self._request_with_retry("GET",
            "https://api.bilibili.com/x/v3/fav/folder/created/list-all", params=params)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取收藏夹失败: {data.get('message')}")
        return data["data"].get("list", [])

    def get_fav_media_list(self, media_id: int, page: int = 1, ps: int = 40) -> dict:
        """获取收藏夹内容"""
        resp = self._request_with_retry("GET",
            "https://api.bilibili.com/x/v3/fav/resource/list",
            params={"media_id": media_id, "order": "mtime", "pn": page, "ps": ps, "type": 0, "tid": 0})
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取收藏夹内容失败: {data.get('message')}")
        return data["data"]

    # ========== 内部方法 ==========

    def _ensure_wbi(self):
        """确保 WBI 签名已初始化"""
        if not self._wbi_initialized:
            self.get_user_info()

    def _request_with_retry(self, method: str, url: str, **kwargs) -> httpx.Response:
        """带 Token 失效重试的请求"""
        token = self._token.get_token()
        params = kwargs.get("params", {})
        if isinstance(params, dict) and "access_key" not in params:
            params["access_key"] = token["access_token"]
            kwargs["params"] = self._signer.sign(params)

        resp = self.client.request(method, url, **kwargs)

        try:
            data = resp.json()
        except Exception:
            return resp

        if isinstance(data, dict) and data.get("code") == -101:
            # Token 失效，刷新后重试一次
            self._token.refresh()
            token = self._token.get_token()
            params = kwargs.get("params", {})
            if isinstance(params, dict):
                params["access_key"] = token["access_token"]
            kwargs["params"] = params
            resp = self.client.request(method, url, **kwargs)

        return resp

    def close(self):
        if self._client:
            self._client.close()
        self._token.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
