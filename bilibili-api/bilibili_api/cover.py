"""
B站 高清封面下载

B站视频封面支持多种分辨率，通过修改 URL 参数获取高清版本。

用法:
    from bilibili_api.token import BiliTokenManager
    from bilibili_api.cover import download_cover

    token_mgr = BiliTokenManager()
    path = download_cover("BV1xx411c7mD", token_mgr=token_mgr)
"""

from pathlib import Path
from typing import Optional

import httpx

from ._core import make_client
from .api import BiliAPI
from .bili_token import BiliTokenManager

# 封面清晰度等级和对应 URL 参数
COVER_QUALITY = {
    "raw": "",           # 原始上传图
    "high": "@1000w",    # ~1000px 宽
    "medium": "@650w",   # ~650px 宽
    "thumb": "@200w",    # ~200px 缩略图
}


def download_cover(
    bvid: str,
    *,
    token_mgr: Optional[BiliTokenManager] = None,
    quality: str = "high",
    output_dir: str = "./covers",
    filename: Optional[str] = None,
) -> Path:
    """
    下载 B站 视频高清封面

    Args:
        bvid: BV 号
        token_mgr: Token 管理器（None 则自动创建）
        quality: 清晰度 — "raw", "high", "medium", "thumb"
        output_dir: 输出目录
        filename: 文件名（不含扩展名，None = 使用视频标题）

    Returns:
        封面文件路径 (.jpg)

    Raises:
        RuntimeError: 获取封面信息或下载失败
    """
    if token_mgr is None:
        token_mgr = BiliTokenManager()

    api = BiliAPI(token_mgr)

    # 获取视频信息（含封面 URL）
    info = api.get_video_info(bvid)
    pic_url = info.get("pic", "")

    if not pic_url:
        # 某些视频封面在 owner_ext 中
        pic_url = info.get("owner_ext", {}).get("official_verify", {}).get("pic", "")

    if not pic_url:
        raise RuntimeError(f"未找到 {bvid} 的封面图片")

    # 构造高清 URL
    quality_suffix = COVER_QUALITY.get(quality, COVER_QUALITY["high"])
    if quality_suffix:
        # B站封面 URL 格式: https://i0.hdslb.com/bfs/archive/xxx.jpg
        # 高清: https://i0.hdslb.com/bfs/archive/xxx.jpg@1000w
        hq_url = pic_url + quality_suffix
    else:
        hq_url = pic_url

    # 准备输出
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_filename(filename or info.get("title", bvid))
    out_path = out_dir / f"{safe_name}_cover.jpg"

    print(f"🖼️  下载封面: {safe_name} ({quality})")

    client = make_client()
    try:
        resp = client.get(hq_url, headers={"Referer": f"https://www.bilibili.com/video/{bvid}"})
        if resp.status_code != 200:
            raise RuntimeError(f"封面下载失败: HTTP {resp.status_code}")

        with open(out_path, "wb") as f:
            f.write(resp.content)

        print(f"✅ 封面已保存: {out_path}")
        return out_path
    finally:
        client.close()


def get_cover_url(bvid: str, *,
                  token_mgr: Optional[BiliTokenManager] = None,
                  quality: str = "high") -> str:
    """
    获取视频封面的高清 URL（不下载）

    Args:
        bvid: BV 号
        token_mgr: Token 管理器
        quality: 清晰度

    Returns:
        高清封面 URL
    """
    if token_mgr is None:
        token_mgr = BiliTokenManager()

    api = BiliAPI(token_mgr)
    info = api.get_video_info(bvid)
    pic_url = info.get("pic", "")

    if not pic_url:
        raise RuntimeError(f"未找到 {bvid} 的封面图片")

    quality_suffix = COVER_QUALITY.get(quality, COVER_QUALITY["high"])
    return pic_url + quality_suffix if quality_suffix else pic_url


def _sanitize_filename(name: str) -> str:
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name[:200]
