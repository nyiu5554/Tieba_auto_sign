#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地获取贴吧 Cookie

运行后会打开贴吧页面，请用手机扫码登录（或账号密码登录），
登录成功后回到终端按回车，脚本会在当前目录生成：
    tieba_cookies.json  —— 完整 Cookie（JSON）
    tieba_cookie.txt    —— name=value; ... 形式的字符串（更方便粘贴）
把任意一个文件的内容填入仓库 Secret：TIEBA_COOKIES
"""

from __future__ import annotations

import json
import os

from DrissionPage import ChromiumPage


def save_cookie(page: ChromiumPage) -> None:
    page.get("https://tieba.baidu.com/")
    input("请在弹出的浏览器里完成登录，确认已登录后按回车继续...")

    # all_info=True 能拿到 domain/path 等信息，粘贴到 Actions 里更可靠
    cookies = page.cookies(all_info=True)
    keep = {"name", "value", "domain", "path", "expires", "expirationDate"}
    cookies = [
        {k: v for k, v in cookie.items() if k in keep}
        for cookie in cookies
        if cookie.get("name")
    ]
    if not cookies:
        print("没有获取到 Cookie，请确认已经登录成功。")
        return

    with open("tieba_cookies.json", "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)

    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    with open("tieba_cookie.txt", "w", encoding="utf-8") as f:
        f.write(cookie_str)

    print(f"\n共获取 {len(cookies)} 条 Cookie")
    print("已生成 tieba_cookies.json（推荐）和 tieba_cookie.txt")
    print("把其中任意一个文件的全部内容，粘贴到仓库 Secret：TIEBA_COOKIES")
    print("Secret 路径：Settings -> Secrets and variables -> Actions -> New repository secret")


if __name__ == "__main__":
    browser = ChromiumPage()
    try:
        save_cookie(browser)
    finally:
        try:
            browser.quit()
        except Exception:
            pass
