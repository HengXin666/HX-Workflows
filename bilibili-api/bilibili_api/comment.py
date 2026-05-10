"""
B站 视频评论

获取视频热门评论/Top N 评论。

用法:
    from bilibili_api.token import BiliTokenManager
    from bilibili_api.comment import get_top_comments, get_comment_replies

    token_mgr = BiliTokenManager()
    comments = get_top_comments("BV1xx411c7mD", count=10, token_mgr=token_mgr)
    for c in comments:
        print(f"[{c['like']}👍] {c['uname']}: {c['content']}")
"""

from typing import Optional

from .api import BiliAPI
from .bili_token import BiliTokenManager


def get_top_comments(
    bvid: str,
    count: int = 10,
    *,
    token_mgr: Optional[BiliTokenManager] = None,
    sort: int = 2,
) -> list[dict]:
    """
    获取视频 Top N 评论

    Args:
        bvid: BV 号
        count: 获取条数
        token_mgr: Token 管理器
        sort: 排序方式 — 0=按时间, 2=按热度（默认）

    Returns:
        [
          {
            "rpid": 评论ID,
            "mid": 用户UID,
            "uname": 用户名,
            "avatar": 头像URL,
            "content": 评论内容,
            "like": 点赞数,
            "ctime": 发布时间戳,
            "replies_count": 回复数,
          },
          ...
        ]
    """
    if token_mgr is None:
        token_mgr = BiliTokenManager()

    api = BiliAPI(token_mgr)
    info = api.get_video_info(bvid)
    aid = info.get("aid", 0)

    return api.get_top_comments(aid, count=count, sort=sort)


def get_comment_replies(
    bvid: str,
    root_rpid: int,
    count: int = 20,
    *,
    token_mgr: Optional[BiliTokenManager] = None,
) -> list[dict]:
    """
    获取某条评论的回复列表

    Args:
        bvid: BV 号
        root_rpid: 根评论 ID
        count: 获取条数
        token_mgr: Token 管理器

    Returns:
        回复评论列表
    """
    if token_mgr is None:
        token_mgr = BiliTokenManager()

    api = BiliAPI(token_mgr)
    info = api.get_video_info(bvid)
    aid = info.get("aid", 0)

    # 需要通过主评论接口获取子回复
    data = api.get_comments(aid, page=1, sort=2, ps=min(count, 20))
    replies = data.get("replies") or []

    for r in replies:
        if r.get("rpid") == root_rpid:
            sub = r.get("replies") or []
            result = []
            for s in sub[:count]:
                result.append({
                    "rpid": s.get("rpid"),
                    "mid": s.get("mid"),
                    "uname": s.get("member", {}).get("uname", ""),
                    "content": s.get("content", {}).get("message", ""),
                    "like": s.get("like", 0),
                })
            return result

    return []
