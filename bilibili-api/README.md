# bilibili-api — 本地初始化指南

## 概述

B站 OAuth Token 存储在 `__HX-Data__` 仓库的 `bilibili-token` 分支中, 通过 HX-Git-DB 读写。
GitHub Actions 每天自动刷新, 其他服务随时读取。

## 前置条件

- Python 3.12+
- `uv` 已安装
- 安装了 B站 App 的手机(用于扫码)
- GitHub Personal Access Token(需要有 `__HX-Data__` 的 `contents: write` 权限)

## 一键初始化

```bash
cd bilibili-api
uv sync
uv run python upload_token.py
```

脚本会自动:
1. 获取 TV 端二维码 → 终端显示
2. 你用 B站 App 扫码确认
3. 保存 token 到本地 `~/.bilibili_downloader/cookie.json`
4. 通过 HX-Git-DB 上传到 `__HX-Data__` 的 `bilibili-token` 分支

一气呵成, 无需其他仓库配合。

## 配置 GitHub Actions Secret

在运行工作流的仓库的 **Settings → Secrets and variables → Actions** 中添加:

| Secret 名 | 值 | 说明 |
|-----------|-----|------|
| `HX_DATA_PAT` | `github_pat_xxxx` | 有 `__HX-Data__` 写权限的 PAT |

## 验证

```bash
uv run python -c "
from token import BiliTokenManager
mgr = BiliTokenManager()
t = mgr.get_token()
print(f'✅ {t[\"uname\"]} (UID: {t[\"mid\"]}) — 剩余 {t[\"expires_in\"]/86400:.1f} 天')
"
```

## 模块速览

```python
from bilibili_api.token import BiliTokenManager
from bilibili_api.api import BiliAPI
from bilibili_api.video import download_video
from bilibili_api.cover import download_cover
from bilibili_api.comment import get_top_comments

# Token: 自动从 HX-Git-DB 读取, 不足 7 天自动刷新
mgr = BiliTokenManager()
api = BiliAPI(mgr)

# 封面
download_cover("BV1xx411c7mD", token_mgr=mgr, quality="high")

# 1080P 视频
download_video("BV1xx411c7mD", token_mgr=mgr, quality=80)

# Top 10 评论
comments = get_top_comments("BV1xx411c7mD", count=10, token_mgr=mgr)
for c in comments:
    print(f"[{c['like']}👍] {c['uname']}: {c['content']}")
```

## 目录结构

```
bilibili-api/
├── __init__.py        # 公开 API
├── pyproject.toml     # uv 依赖
├── _core.py           # 基础设施(签名/HTTP)
├── token.py           # Token 管理(HX-Git-DB)
├── api.py             # B站 API 封装
├── video.py           # 视频下载
├── cover.py           # 封面下载
├── comment.py         # 评论
├── refresh_token.py   # CI 每日刷新
└── upload_token.py    # 一键初始化(扫码+上传)
```

## Token 生命周期

```
  upload_token.py                 refresh_token.py
  (一键扫码+上传)                  (CI 每日保活)
       │                               │
       ▼                               ▼
  ┌─────────────────────────────────────────┐
  │          HX-Git-DB (bilibili-token)     │
  └─────────────────────────────────────────┘
       │               │               │
       ▼               ▼               ▼
    视频下载         封面下载         评论获取
```
