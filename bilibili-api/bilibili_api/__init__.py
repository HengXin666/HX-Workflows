"""
bilibili-api — B站 OAuth Token 管理与 API 自动化

模块:
  bili_token — Token 管理（HX-Git-DB 持久化，自动刷新）
  api        — B站 API 封装（视频信息、播放地址、评论等）
  video      — 视频下载（1080P DASH 格式）
  cover      — 高清封面下载
  comment    — 评论获取（热门/Top N）
"""

from .bili_token import BiliTokenManager
from .api import BiliAPI
from .video import download_video
from .cover import download_cover, get_cover_url
from .comment import get_top_comments, get_comment_replies

__all__ = [
    "BiliTokenManager",
    "BiliAPI",
    "download_video",
    "download_cover",
    "get_cover_url",
    "get_top_comments",
    "get_comment_replies",
]
