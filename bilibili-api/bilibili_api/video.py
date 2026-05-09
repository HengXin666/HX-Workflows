"""
B站 1080P 视频下载

从 B站 DASH 格式下载视频流+音频流，用 ffmpeg 合并。

用法:
    from bilibili_api.token import BiliTokenManager
    from bilibili_api.video import download_video

    token_mgr = BiliTokenManager()
    path = download_video("BV1xx411c7mD", token_mgr=token_mgr, output_dir="./videos")
"""

import concurrent.futures
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx

from ._core import QUALITY_MAP, make_client
from .api import BiliAPI
from .bili_token import BiliTokenManager


def download_video(
    bvid: str,
    *,
    token_mgr: Optional[BiliTokenManager] = None,
    quality: int = 80,
    output_dir: str = "./videos",
    filename: Optional[str] = None,
    page_index: Optional[int] = None,
    max_retries: int = 3,
) -> Path:
    """
    下载 B站 视频（1080P 默认）

    Args:
        bvid: BV 号
        token_mgr: Token 管理器（None 则自动创建）
        quality: 画质代码，80=1080P, 116=1080P高码率, 120=4K
        output_dir: 输出目录
        filename: 输出文件名（不含扩展名，None 则自动生成）
        page_index: 指定分P（None = 下载第一个分P）
        max_retries: 最大重试次数

    Returns:
        输出文件路径

    Raises:
        RuntimeError: 下载或合并失败
    """
    if token_mgr is None:
        token_mgr = BiliTokenManager()

    api = BiliAPI(token_mgr)
    client = _make_download_client()

    # 获取视频信息
    info = api.get_video_info(bvid)
    pages = info.get("pages", [])
    aid = info.get("aid", 0)
    title = info.get("title", bvid)

    if not pages:
        raise RuntimeError("无可用分P")

    # 选择分P
    if page_index is not None:
        pages = [p for p in pages if p.get("page") == page_index]
        if not pages:
            raise RuntimeError(f"未找到分P {page_index}")

    page = pages[0]
    cid = page.get("cid", 0)
    pn = page.get("page", 1)

    # 获取播放地址
    play_info = api.get_play_url(aid, cid, quality)
    dash = play_info.get("dash", {})
    videos = dash.get("video", [])
    audios = dash.get("audio", [])

    if not videos or not audios:
        raise RuntimeError("无可用视频/音频流")

    # 选最高画质
    video_stream = max(videos, key=lambda v: v.get("bandwidth", 0))
    audio_stream = max(audios, key=lambda a: a.get("bandwidth", 0))

    video_url = video_stream.get("baseUrl") or video_stream.get("base_url", "")
    audio_url = audio_stream.get("baseUrl") or audio_stream.get("base_url", "")
    video_backup = (video_stream.get("backupUrl") or video_stream.get("backup_url") or [None])[0]
    audio_backup = (audio_stream.get("backupUrl") or audio_stream.get("backup_url") or [None])[0]

    if not video_url or not audio_url:
        raise RuntimeError("视频或音频 URL 为空")

    # 准备输出路径
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_title = _sanitize_filename(filename or f"[{QUALITY_MAP.get(quality, '')}]{title}")
    video_tmp = out_dir / f"{safe_title}.video.tmp"
    audio_tmp = out_dir / f"{safe_title}.audio.tmp"
    out_file = out_dir / f"{safe_title}.mp4"

    print(f"⬇️  下载: {title} (P{pn}, {QUALITY_MAP.get(quality, '?')})")

    # 并发下载视频+音频
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        v_future = executor.submit(_download_segment, client, video_url, video_backup, video_tmp, "视频", max_retries)
        a_future = executor.submit(_download_segment, client, audio_url, audio_backup, audio_tmp, "音频", max_retries)
        v_ok = v_future.result()
        a_ok = a_future.result()

    if not v_ok or not a_ok:
        _cleanup(video_tmp, audio_tmp)
        raise RuntimeError("视频或音频下载失败")

    # ffmpeg 合并
    cmd = ["ffmpeg", "-i", str(video_tmp), "-i", str(audio_tmp),
           "-c:v", "copy", "-c:a", "copy", "-y", str(out_file)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    _cleanup(video_tmp, audio_tmp)

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 合并失败:\n{result.stderr}")

    print(f"✅ 下载完成: {out_file}")
    return out_file


def _make_download_client() -> httpx.Client:
    return httpx.Client(
        timeout=60,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com/",
        },
        follow_redirects=True,
    )


def _download_segment(client: httpx.Client, url: str, backup: str,
                      path: Path, label: str, max_retries: int) -> bool:
    """下载单个媒体段（视频/音频），支持自动选择可用 URL 和重试"""
    urls = [u for u in [url, backup] if u]
    last_error = None

    for attempt in range(max_retries):
        for try_url in urls:
            try:
                with client.stream("GET", try_url) as resp:
                    if resp.status_code >= 400:
                        continue
                    with open(path, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                            f.write(chunk)
                    return True
            except Exception as e:
                last_error = e
                continue
        time.sleep(1 * (attempt + 1))

    print(f"❌ {label}下载失败: {last_error}")
    return False


def _sanitize_filename(name: str) -> str:
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name[:200]


def _cleanup(*paths: Path):
    for p in paths:
        if p.exists():
            p.unlink()
