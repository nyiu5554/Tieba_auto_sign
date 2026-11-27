#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""贴吧自动签到（GitHub Actions / 本地通用）

从环境变量 TIEBA_COOKIES 读取 Cookie，自动遍历「我关注的吧」逐个签到，
可选通过 Server酱 把结果推送到微信。

环境变量:
    TIEBA_COOKIES  必填。Cookie JSON 数组，或 "BDUSS=xxx; STOKEN=yyy" 形式的字符串
    SENDKEY        可选。Server酱 SendKey（兼容旧变量名 SendKey）
    CHROME_PATH    可选。Chrome/Chromium 可执行文件路径
    HEADLESS       可选。设为 0 时显示浏览器窗口，便于本地调试
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote

import requests
from DrissionPage import ChromiumOptions, ChromiumPage

BASE_URL = "https://tieba.baidu.com"
TZ = timezone(timedelta(hours=8))
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tieba_sign.log")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 浏览器里直接调用签到接口的兜底方案（按钮点不到时使用）
API_SIGN_JS = """
var kw = arguments[0], tbs = arguments[1];
return fetch('/sign/add', {
    method: 'POST',
    credentials: 'include',
    headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'},
    body: new URLSearchParams({ie: 'utf-8', kw: kw, tbs: tbs}).toString()
}).then(function (r) { return r.text(); });
"""

log_lines: list[str] = []


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def log(msg: str = "") -> None:
    stamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)
    log_lines.append(f"[{stamp}] {msg}")


def save_log() -> None:
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(log_lines) + "\n")
    except OSError as exc:  # 只影响日志文件，不影响签到
        print(f"写入日志失败: {exc}", flush=True)


def nap(low: float = 0.8, high: float = 1.8) -> None:
    """随机等待，避免请求过于密集。"""
    time.sleep(random.uniform(low, high))


# --------------------------------------------------------------------------- #
# Cookie / 浏览器
# --------------------------------------------------------------------------- #
def read_cookie() -> list[dict]:
    """读取并规范化 Cookie，支持 JSON 数组、JSON 对象和 Cookie 字符串。"""
    raw = (os.environ.get("TIEBA_COOKIES") or "").strip()
    if not raw:
        log("未配置 TIEBA_COOKIES，请参考教程配置 Cookie")
        return []

    try:
        if raw.startswith("["):
            data = json.loads(raw)
        elif raw.startswith("{"):
            data = json.loads(raw)
            data = data.get("cookies", data)
            if isinstance(data, dict):
                data = [{"name": k, "value": v} for k, v in data.items()]
        else:
            items = []
            for part in raw.split(";"):
                if "=" not in part:
                    continue
                name, _, value = part.partition("=")
                if name.strip():
                    items.append({"name": name.strip(), "value": value.strip()})
            data = items
    except (ValueError, AttributeError) as exc:
        log(f"TIEBA_COOKIES 解析失败（应为 JSON 或 a=b; c=d 格式）: {exc}")
        return []

    cookies = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if not name or value is None:
            continue
        cookie = {"name": name, "value": str(value)}
        if item.get("domain"):
            cookie["domain"] = item["domain"]
        if item.get("path"):
            cookie["path"] = item["path"]
        expires = item.get("expires", item.get("expirationDate"))
        if expires:
            cookie["expires"] = expires
        cookies.append(cookie)

    if not cookies:
        log("Cookie 内容为空，请重新获取")
    return cookies


def find_browser() -> str:
    """按优先级寻找可用的 Chrome/Chromium。"""
    explicit = (os.environ.get("CHROME_PATH") or "").strip()
    if explicit and os.path.exists(explicit):
        return explicit
    for name in (
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
        "chrome",
        "chrome-headless-shell",
    ):
        path = shutil.which(name)
        if path:
            return path
    return ""


def build_page() -> ChromiumPage:
    co = ChromiumOptions()
    if os.environ.get("HEADLESS", "1") != "0":
        co.headless(True)
    # GitHub Actions 的容器里必须关闭沙箱，否则浏览器无法启动
    for arg in (
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--window-size=1280,900",
        "--lang=zh-CN",
    ):
        co.set_argument(arg)
    co.set_user_agent(UA)
    co.set_timeouts(base=10, page_load=30, script=20)

    browser = find_browser()
    if browser:
        co.set_browser_path(browser)
        log(f"使用浏览器: {browser}")
    else:
        log("未找到 Chrome/Chromium，将使用 DrissionPage 自动查找")
    return ChromiumPage(co)


def fetch_text(page: ChromiumPage, path: str) -> str:
    """在页面上下文里 fetch 同源接口，自动带上 Cookie。"""
    try:
        result = page.run_js(
            "return fetch(arguments[0], {credentials: 'include'})"
            ".then(function (r) { return r.text(); });",
            path,
        )
        return (result or "").strip()
    except Exception:
        return ""


def check_login(page: ChromiumPage) -> bool:
    """通过用户信息接口 + 关注列表页判断 Cookie 是否有效。"""
    page.get(f"{BASE_URL}/i/i/forum")
    page.wait.doc_loaded(timeout=20)
    try:
        page.wait.eles_loaded("css:#like_pagelet", timeout=8)
    except Exception:
        pass

    url = page.url or ""
    if "error.html" in url or "passport.baidu.com" in url:
        log(f"    页面被重定向到: {url}")
        return False

    # 未登录时该接口返回 null，登录后返回用户信息
    text = fetch_text(page, "/f/user/json_userinfo")
    if text == "null":
        return False
    if text:
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            if data.get("data") or data.get("no") in (0, "0"):
                return True
            if data.get("no") not in (None, 0, "0"):
                return False

    return "like_pagelet" in (page.html or "")


# --------------------------------------------------------------------------- #
# 关注的贴吧列表
# --------------------------------------------------------------------------- #
def collect_forums(page: ChromiumPage) -> list[str]:
    forums: list[str] = []
    seen: set[str] = set()
    for pn in range(1, 101):  # 最多 100 页，足够覆盖关注上限
        page.get(f"{BASE_URL}/i/i/forum?pn={pn}")
        page.wait.doc_loaded(timeout=20)
        try:
            page.wait.eles_loaded('css:#like_pagelet a[href*="kw="]', timeout=10)
        except Exception:
            pass
        links = page.eles('css:#like_pagelet a[href*="kw="]', timeout=5)
        if links:
            kws = []
            for link in links:
                match = re.search(r"[?&]kw=([^&]+)", link.attr("href") or "")
                if match:
                    kws.append(unquote(match.group(1)).strip())
        else:
            # 兜底：页面结构变化时直接从 HTML 里提取 /f?kw= 链接
            html = page.html or ""
            kws = [unquote(k).strip() for k in re.findall(r"/f\?kw=([^\"&]+)", html)]

        added = 0
        for kw in kws:
            if not kw or kw in seen:
                continue
            seen.add(kw)
            forums.append(kw)
            added += 1
        log(f"第 {pn} 页：新增 {added} 个吧，累计 {len(forums)} 个")
        if added == 0:
            break
        nap()
    return forums


# --------------------------------------------------------------------------- #
# 签到
# --------------------------------------------------------------------------- #
def _text(page: ChromiumPage, xpath: str) -> str:
    try:
        ele = page.ele(f"xpath:{xpath}", timeout=2)
        return (ele.text or "").strip() if ele else ""
    except Exception:
        return ""


def get_level_exp(page: ChromiumPage) -> tuple[str, str]:
    """获取当前吧的等级和经验，找不到时返回「未知」。"""
    base = '//*[@id="pagelet_aside/pagelet/my_tieba"]/div/div[1]/div[3]'
    level = _text(page, f"{base}/div[1]/a/div[2]")
    exp = _text(page, f"{base}/div[2]/a/div[2]/span[1]")
    if not level:
        level = _text(page, '//*[@id="pagelet_aside"]//*[contains(@class,"user_level")]')
    if not exp:
        exp = _text(page, '//*[@id="pagelet_aside"]//*[contains(@class,"user_exp")]')
    return level or "未知", exp or "未知"


def is_signed(page: ChromiumPage) -> bool:
    """通过签到区域文字判断今天是否已签到。"""
    try:
        ele = page.ele("css:#signstar_wrapper", timeout=3)
    except Exception:
        ele = None
    if not ele:
        return False
    text = (ele.text or "").replace("\n", "").replace(" ", "")
    return "已签到" in text or text.startswith("连续")


def click_sign(page: ChromiumPage) -> bool:
    for index, locator in enumerate(
        (
            "css:a.j_signbtn",
            "css:.j_signbtn",
            'xpath://a[contains(@class,"sign_btn_bright")]',
            "css:#signstar_wrapper a",
        )
    ):
        try:
            btn = page.ele(locator, timeout=2 if index == 0 else 1)
        except Exception:
            btn = None
        if not btn:
            continue
        try:
            if not btn.states.is_displayed:
                continue
            btn.click()
            return True
        except Exception as exc:
            log(f"    点击签到按钮失败（{locator}）: {exc}")
    return False


def get_tbs(page: ChromiumPage) -> str:
    """在当前页面上下文里取 tbs，避免直接访问接口被重定向。"""
    match = re.search(r'"tbs"\s*:\s*"([^"]+)"', fetch_text(page, "/dc/common/tbs"))
    return match.group(1) if match else ""


def api_sign(page: ChromiumPage, kw: str, tbs: str) -> tuple[str, str]:
    """在页面上下文里调用签到接口，作为按钮点击失败时的兜底。"""
    if not tbs:
        return "error", "缺少 tbs"
    try:
        result = page.run_js(API_SIGN_JS, kw, tbs)
        data = json.loads(result)
    except Exception as exc:
        return "error", f"接口签到异常: {exc}"

    no = data.get("no", -1)
    error = (data.get("error") or "").strip()
    if no == 0:
        return "success", "接口签到成功"
    if no == 1101 or "已经签到" in error:
        return "already", "今天已签到"
    return "error", f"接口返回 no={no} {error}".strip()


def sign_forum(page: ChromiumPage, kw: str, tbs: str) -> tuple[str, str]:
    """返回 (状态, 描述)，状态为 success / already / error。"""
    page.get(f"{BASE_URL}/f?kw={quote(kw)}")
    page.wait.doc_loaded(timeout=25)
    try:
        page.wait.eles_loaded('css:#signstar_wrapper', timeout=10)
    except Exception:
        pass

    if is_signed(page):
        level, exp = get_level_exp(page)
        return "already", f"已签到（等级 {level}，经验 {exp}）"

    clicked = click_sign(page)
    if clicked:
        nap(1.0, 2.0)
        try:
            page.refresh()
            page.wait.doc_loaded(timeout=20)
        except Exception:
            pass
        if is_signed(page):
            level, exp = get_level_exp(page)
            return "success", f"签到成功（等级 {level}，经验 {exp}）"

    status, detail = api_sign(page, kw, tbs)
    if status == "success":
        nap()
        try:
            page.refresh()
            page.wait.doc_loaded(timeout=20)
        except Exception:
            pass
        level, exp = get_level_exp(page)
        return "success", f"签到成功（等级 {level}，经验 {exp}）"
    if status == "already":
        return "already", "今天已签到"
    if is_signed(page):  # 点击后其实已经成功，只是没等到页面刷新
        return "already", "已签到"

    if clicked:
        return "error", f"点击签到后未生效，{detail}"
    return "error", f"找不到签到按钮，{detail}"


# --------------------------------------------------------------------------- #
# 通知
# --------------------------------------------------------------------------- #
def send_notification(title: str, content: str) -> None:
    send_key = (
        os.environ.get("SENDKEY")
        or os.environ.get("SendKey")
        or os.environ.get("SERVERCHAN_SENDKEY")
        or ""
    ).strip()
    if not send_key:
        log("未配置 Server酱 SendKey，跳过通知")
        return

    # Server酱 Turbo 的新域名，旧域名作为兜底
    endpoints = (
        f"https://sctapi.ftqq.com/{send_key}.send",
        f"https://sc.ftqq.com/{send_key}.send",
    )
    payload = {"title": title[:32], "text": title[:32], "desp": content}
    for api in endpoints:
        try:
            resp = requests.post(api, data=payload, timeout=30)
            body = (resp.text or "").replace(" ", "")
            if resp.status_code == 200 and ('"code":0' in body or '"errno":0' in body):
                log("Server酱通知发送成功")
                return
            log(f"通知失败 HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as exc:
            log(f"通知发送异常: {exc}")


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def build_summary(results: list[tuple[str, str, str]], elapsed: float) -> tuple[str, str]:
    success = [r for r in results if r[1] == "success"]
    already = [r for r in results if r[1] == "already"]
    failed = [r for r in results if r[1] == "error"]

    title = f"贴吧签到 {len(success)} 成功 / {len(already)} 已签 / {len(failed)} 失败"
    lines = [
        f"**{datetime.now(TZ):%Y-%m-%d %H:%M}** 共 {len(results)} 个吧，用时 {elapsed:.0f} 秒",
        "",
        f"- ✅ 签到成功：{len(success)}",
        f"- ☑️ 今日已签：{len(already)}",
        f"- ❌ 失败：{len(failed)}",
        "",
    ]
    if failed:
        lines.append("**失败列表**")
        lines.extend(f"- {kw}：{msg}" for kw, _, msg in failed)
        lines.append("")
    lines.append("**明细**")
    icon = {"success": "✅", "already": "☑️", "error": "❌"}
    lines.extend(f"{icon.get(status, '•')} {kw}：{msg}" for kw, status, msg in results)

    content = "\n".join(lines)
    if len(content) > 30000:  # Server酱 内容上限保护
        content = content[:30000] + "\n\n……内容过长已截断"
    return title, content


def main() -> int:
    started = time.time()
    log("=" * 60)
    log("贴吧自动签到开始")

    cookies = read_cookie()
    if not cookies:
        save_log()
        return 1

    page = None
    results: list[tuple[str, str, str]] = []
    try:
        page = build_page()
        # 先打开贴吧域名再注入 Cookie，字符串形式的 Cookie 才能落到正确域名下
        try:
            page.get(BASE_URL)
            page.wait.doc_loaded(timeout=20)
        except Exception as exc:
            log(f"打开贴吧首页失败（继续尝试）: {exc}")
        page.set.cookies(cookies)

        if not check_login(page):
            log(f"Cookie 无效或已过期（当前页面: {page.url}），请重新获取后再试")
            send_notification(
                "贴吧签到失败",
                "Cookie 无效或已过期，请重新运行「点我生成Cookie.py」获取 Cookie，"
                "并更新仓库 Secret 中的 TIEBA_COOKIES。",
            )
            return 1
        log("登录成功")

        forums = collect_forums(page)
        if not forums:
            log("没有读取到任何关注的贴吧，请确认账号已关注贴吧")
            send_notification("贴吧签到失败", "没有读取到关注的贴吧，请确认 Cookie 对应账号已关注贴吧。")
            return 1
        log(f"共发现 {len(forums)} 个关注的吧")

        tbs = get_tbs(page)
        if tbs:
            log("已获取 tbs，可启用接口兜底")
        else:
            log("未获取到 tbs，仅使用页面点击签到")

        for index, kw in enumerate(forums, 1):
            log(f"[{index}/{len(forums)}] {kw}吧 签到中...")
            try:
                status, message = sign_forum(page, kw, tbs)
            except Exception as exc:
                status, message = "error", f"异常: {exc}"
            log(f"    {message}")
            results.append((kw, status, message))
            nap(0.6, 1.5)

        elapsed = time.time() - started
        title, content = build_summary(results, elapsed)
        success = sum(1 for r in results if r[1] in ("success", "already"))
        log("-" * 60)
        log(title)
        log(f"总用时 {elapsed:.0f} 秒")
        send_notification(title, content)
        # 全部失败时返回非 0，方便在 Actions 页面看到红色失败标记
        return 0 if success else 1

    except Exception as exc:
        log(f"程序运行异常: {exc}")
        send_notification("贴吧签到异常", f"程序运行异常：{exc}")
        return 1
    finally:
        if page is not None:
            try:
                page.quit()
            except Exception:
                pass
        save_log()


if __name__ == "__main__":
    sys.exit(main())
