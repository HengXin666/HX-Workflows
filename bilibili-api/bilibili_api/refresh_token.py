"""
CI: B站 Token 每日保活

从 __HX-Data__ 读取 → 调用 B站 OAuth 刷新 → 写回 __HX-Data__

用法:
  uv run python bilibili-api/refresh_token.py

环境变量:
  GITHUB_TOKEN: GitHub PAT（HX-Git-DB 自动读取）
"""

import sys
from datetime import datetime, timezone

from bilibili_api.bili_token import BiliTokenManager


def main():
    mgr = BiliTokenManager()

    # 加载 token
    try:
        t = mgr.get_token()
    except RuntimeError as e:
        print(f"❌ 读取 Token 失败: {e}")
        sys.exit(1)

    print(f"👤 {t['uname']} (UID: {t['mid']})")
    print(f"⏳ 刷新前剩余: {t['expires_in'] / 86400:.1f} 天")

    # 强制刷新
    try:
        t = mgr.refresh()
    except RuntimeError as e:
        print(f"❌ 刷新失败: {e}")
        print("⚠️ 保留现有 token（可能仍有效）")
        sys.exit(0)

    print(f"✅ 刷新成功！剩余: {t['expires_in'] / 86400:.1f} 天")


if __name__ == "__main__":
    main()
