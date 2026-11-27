#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""贴吧 Cookie 生成器（多账号循环版）

用法：
    python 点我生成Cookie.py

    运行后**立刻打开浏览器并进入贴吧页面**，你在浏览器里登录，
    回到终端按回车就保存这一份 Cookie；保存后自动清掉登录态、
    重新回到登录页，等下一个账号。输入 x 退出。

每份 Cookie 直接按 **Cookie 里的账号标识（TIEBAUID）** 命名，
保存在脚本同目录的 tieba_cookies/ 文件夹：
    tieba_cookies/tieba_cookie_7a2a671a436fb21be7f481a3.json
    tieba_cookies/tieba_cookie_8609f3e2a85a7b8fcc1175e5.json

账号身份完全来自 Cookie，不联网、不猜昵称：
    TIEBAUID  → 账号 ID（首选，同一账号永远一样）
    STOKEN    → 取不到 TIEBAUID 时，用它的哈希值兜底
同一个账号重新登录会覆盖自己的文件，不同账号各自一个文件。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

try:
    from DrissionPage import ChromiumPage
except ImportError:
    print("没有安装 DrissionPage，请先运行：pip install DrissionPage")
    sys.exit(1)

URL = "https://tieba.baidu.com/"
SAVE_DIR = Path(__file__).resolve().parent / "tieba_cookies"
NAME = "tieba_cookie"
KEEP_FIELDS = {"name", "value", "domain", "path", "expires", "expirationDate"}
BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def safe_name(text: str) -> str:
    """把标识转成安全的文件名（不影响正常 hex 值）。"""
    text = BAD_CHARS.sub("_", (text or "").strip())
    text = re.sub(r"\s+", "_", text).strip("._")
    return text[:40]


def open_tieba(page: ChromiumPage, clear: bool = False) -> None:
    """打开贴吧页面；clear=True 时先清掉登录态，准备登录下一个账号。"""
    if clear:
        try:
            page.run_cdp("Network.clearBrowserCookies")
        except Exception:
            try:
                page.set.cookies.clear()
            except Exception:
                pass
    page.get(URL)


def grab(page: ChromiumPage) -> list[dict] | None:
    """读取 Cookie；没有 BDUSS 视为没登录成功。"""
    cookies = [
        {k: v for k, v in c.items() if k in KEEP_FIELDS}
        for c in page.cookies(all_info=True)
        if c.get("name")
    ]
    if not any(c["name"] == "BDUSS" for c in cookies):
        return None
    return cookies


def account_key(cookies: list[dict]) -> str:
    """账号标识完全从 Cookie 里取：TIEBAUID 优先，其次 STOKEN/BDUSS 哈希。"""
    values = {c["name"]: c["value"] for c in cookies}
    uid = safe_name(values.get("TIEBAUID", ""))
    if uid:
        return uid
    for field in ("STOKEN", "BDUSS"):
        token = (values.get(field) or "").strip()
        if token:
            return "h" + hashlib.sha256(token.encode()).hexdigest()[:20]
    return ""


def main() -> int:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    page = ChromiumPage()
    open_tieba(page)  # 脚本一启动就打开浏览器
    print(f"浏览器已打开：{URL}")
    print(f"Cookie 保存目录：{SAVE_DIR}")
    print("在浏览器里登录贴吧 → 回到这里按回车保存 → 输入 x 退出\n")
    saved: list[Path] = []
    try:
        while True:
            try:
                cmd = input("登录完成后按回车保存，输入 x 退出：").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if cmd in ("x", "q", "exit", "quit"):
                break

            cookies = grab(page)
            if not cookies:
                print("⚠️  没读到 BDUSS，可能还没登录成功，登录后可以再按回车。\n")
                continue

            key = account_key(cookies)
            if not key:
                print("⚠️  Cookie 里既没有 TIEBAUID 也没有 STOKEN，无法识别账号，这一份不保存。\n")
                continue

            path = SAVE_DIR / f"{NAME}_{key}.json"
            existed = path.exists()
            path.write_text(
                json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            saved.append(path)
            action = "已更新" if existed else "已保存"
            print(f"✅ {action}：{path.name}（账号标识 {key}，{len(cookies)} 条 Cookie）")
            print("   已清空登录状态，可以登录下一个账号了\n")
            open_tieba(page, clear=True)
    finally:
        try:
            page.quit()
        except Exception:
            pass

    if saved:
        print("\n本次共生成：")
        for p in dict.fromkeys(saved):
            print(f"  {p}")
        print("\n填入仓库 Secret 时，直接把对应文件的内容粘贴进去：")
        print("  账号1 → Settings → Secrets → TIEBA_COOKIES")
        print("  账号2 → Settings → Secrets → TIEBA_COOKIES_2")
    print("已退出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
