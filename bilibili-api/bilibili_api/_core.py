"""
B站 API 基础设施（内部模块）

- 常量：TV APP_KEY/SEC、WBI 混洗表、画质映射
- 签名器：BiliSigner（TV端 appkey+sign）、WBISigner（Web端 WBI）
- HTTP 客户端：带 UA/Referer 的 httpx Client
"""

import hashlib
import time
from typing import Optional
from urllib.parse import urlencode, quote

import httpx

# ============================================================
# B站 API 常量
# ============================================================

TV_APP_KEY = "4409e2ce8ffd12b8"
TV_APP_SEC = "59b43e04ad6965f34319062b478f83dd"

WBI_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

QUALITY_MAP = {
    120: "4K 超清",
    116: "1080P 高码率",
    112: "1080P 高码率",
    80: "1080P",
    64: "720P",
    32: "480P",
    16: "360P",
}

# ============================================================
# HTTP 客户端
# ============================================================

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com/",
}


def make_client(timeout: int = 30) -> httpx.Client:
    """创建已配置好 UA/Referer 的 httpx Client"""
    return httpx.Client(
        timeout=timeout,
        headers=DEFAULT_HEADERS,
        follow_redirects=True,
    )


# ============================================================
# 签名器
# ============================================================

class BiliSigner:
    """B站 TV端 API 签名器（appkey + sign 方式）"""

    def __init__(self, app_key: str = TV_APP_KEY, app_sec: str = TV_APP_SEC):
        self.app_key = app_key
        self.app_sec = app_sec

    def sign(self, params: dict) -> dict:
        params = {**params, "appkey": self.app_key, "ts": int(time.time())}
        sorted_params = sorted(params.items())
        query = urlencode(sorted_params, quote_via=quote)
        sign_str = query + self.app_sec
        params["sign"] = hashlib.md5(sign_str.encode()).hexdigest()
        return params


class WBISigner:
    """B站 Web端 WBI 签名器"""

    def __init__(self):
        self.img_key = ""
        self.sub_key = ""

    def get_mixin_key(self, orig: str) -> str:
        s = []
        for i in WBI_MIXIN_KEY_ENC_TAB:
            if i < len(orig):
                s.append(orig[i])
        return "".join(s)[:32]

    def set_keys(self, img_key: str, sub_key: str):
        self.img_key = img_key
        self.sub_key = sub_key

    def sign_params(self, params: dict, ts: Optional[int] = None) -> dict:
        if not self.img_key or not self.sub_key:
            return params
        if ts is None:
            ts = int(time.time())
        params = {**params, "wts": ts}

        def sanitize(s: str) -> str:
            for ch in ["!", "'", "(", ")", "*"]:
                s = s.replace(ch, "")
            return s

        cleaned = {}
        for k, v in params.items():
            cleaned[k] = sanitize(v) if isinstance(v, str) else v

        sorted_items = sorted(cleaned.items())
        query = urlencode(sorted_items, quote_via=quote)
        mixin_key = self.get_mixin_key(self.img_key + self.sub_key)
        w_rid = hashlib.md5((query + mixin_key).encode()).hexdigest()
        params["w_rid"] = w_rid
        params["wts"] = ts
        return params
