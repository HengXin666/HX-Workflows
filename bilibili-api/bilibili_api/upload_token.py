"""
B站 Token 一键初始化

扫码登录 + 上传 __HX-Data__ 一气呵成。

用法:
  uv run python upload_token.py
"""

import json
import sys
import threading
import time
from pathlib import Path

from bilibili_api._core import BiliSigner, make_client

COOKIE_FILE = Path.home() / ".bilibili_downloader" / "cookie.json"
DATA_REPO = "https://github.com/HengXin666/__HX-Data__.git"
DATA_BRANCH = "bilibili-token"


def main():
    signer = BiliSigner()
    client = make_client()

    # ====== 第 1 步：获取 TV 端二维码 ======
    print("📺 正在获取 B站 TV 端二维码...")
    resp = client.post(
        "https://passport.bilibili.com/x/passport-tv-login/qrcode/auth_code",
        data=signer.sign({"local_id": 0}),
    )
    data = resp.json()
    if data.get("code") != 0:
        print(f"❌ 获取二维码失败: {data.get('message')}")
        sys.exit(1)

    qr_data = data["data"]
    auth_code = qr_data["auth_code"]
    qr_url = qr_data["url"]

    # 显示二维码
    _show_qrcode(qr_url)

    print("📱 请打开 B站 App → 我的 → 扫一扫，扫描上方二维码")
    print("   等待扫码确认中...")

    # ====== 第 2 步：轮询扫码结果 ======
    stop_event = threading.Event()
    result = {}

    def poll():
        while not stop_event.is_set():
            try:
                r = client.post(
                    "https://passport.bilibili.com/x/passport-tv-login/qrcode/poll",
                    data=signer.sign({"auth_code": auth_code, "local_id": 0}),
                )
                d = r.json()
                code = d.get("code")
                if code == 0:
                    result["data"] = d["data"]
                    stop_event.set()
                    return
                elif code == 86038:
                    print("\n❌ 二维码已过期，请重新运行")
                    stop_event.set()
                    return
                elif code == 86039:
                    pass  # 等待扫码
                else:
                    print(f"\n❌ 验证失败: {d.get('message')}")
                    stop_event.set()
                    return
            except Exception as e:
                print(f"\n❌ 网络错误: {e}")
                stop_event.set()
                return

            stop_event.wait(3)

    poll_thread = threading.Thread(target=poll, daemon=True)
    poll_thread.start()

    try:
        while poll_thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n⚠️ 用户取消")
        stop_event.set()
        sys.exit(1)

    if "data" not in result:
        print("❌ 登录未完成")
        sys.exit(1)

    token_data = result["data"]
    print(f"\n✅ 登录成功！")
    print(f"   👤 {token_data.get('uname', '?')} (UID: {token_data.get('mid', '?')})")

    # ====== 第 3 步：保存本地 ======
    token_data["saved_at"] = int(time.time())
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(COOKIE_FILE, "w") as f:
        json.dump(token_data, f, indent=2)
    print(f"💾 已保存到 {COOKIE_FILE}")

    # ====== 第 4 步：上传到 __HX-Data__ ======
    print(f"📤 上传到 {DATA_REPO} ({DATA_BRANCH})...")
    from hx_git_db import make_database

    db = make_database(DATA_REPO, DATA_BRANCH, only=True)
    try:
        db.pull()
        with db.open("cookie.json") as f:
            f.write_json(token_data)
        db.push()
    finally:
        try:
            db.cleanup()
        except PermissionError:
            pass  # Windows 下 .git 对象可能被锁定，不影响上传结果

    print("✅ 一键初始化完成！GitHub Actions 将每日自动刷新此 token。")


def _show_qrcode(url: str):
    """终端显示二维码"""
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except ImportError:
        print(f"\n🔗 请使用 B站 App 扫描以下链接:\n   {url}\n")


if __name__ == "__main__":
    main()
