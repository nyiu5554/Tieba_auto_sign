#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""贴吧自动签到（GitHub Actions / Docker / 本地通用）

直接调用贴吧接口签到，不依赖浏览器：
  1. 读取 Cookie，校验登录
  2. 分页拉取「我关注的吧」列表
  3. 优先调用客户端签名接口（HTTPS，规避网页验证码），失败时回退网页接口
  4. 可选通过 Server酱把结果推送到微信

环境变量:
    TIEBA_COOKIES       必填。Cookie JSON 数组，或 "BDUSS=xxx; STOKEN=yyy" 形式的字符串
    TIEBA_COOKIES_FILE  可选。Cookie JSON 文件路径（TIEBA_COOKIES 为空时生效）
    SENDKEY             可选。Server酱 SendKey（兼容旧变量名 SendKey）
    TIEBA_LOG_FILE      可选。日志文件路径，默认脚本同目录 tieba_sign.log
    TIEBA_ACCOUNT       可选。账号备注名（多账号时用于区分日志和通知），如「账号1」
    TIEBA_DELAY         可选。每个吧签到之间的等待秒数，默认 0.6
    TIEBA_LIMIT         可选。只处理前 N 个吧，用于调试，默认 0（全部）
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import random
import re
import ssl
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote

import requests

HOST = "tieba.baidu.com"
TZ = timezone(timedelta(hours=8))
LOG_FILE = os.environ.get("TIEBA_LOG_FILE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "tieba_sign.log"
)
UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
)
DELAY = 0.6

log_lines: list[str] = []
ACCOUNT = (os.environ.get("TIEBA_ACCOUNT") or "").strip()


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def log(msg: str = "") -> None:
    stamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    prefix = f"[{ACCOUNT}] " if ACCOUNT else ""
    print(f"[{stamp}] {prefix}{msg}", flush=True)
    log_lines.append(f"[{stamp}] {prefix}{msg}")


def save_log() -> None:
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(log_lines) + "\n")
    except OSError as exc:  # 只影响日志文件，不影响签到
        print(f"写入日志失败: {exc}", flush=True)


def nap(low: float = 0.4, high: float = 0.9) -> None:
    """随机等待，避免请求过于密集。"""
    time.sleep(random.uniform(low, high))


# --------------------------------------------------------------------------- #
# Cookie / HTTP
# --------------------------------------------------------------------------- #
def read_cookie() -> list[dict]:
    """读取并规范化 Cookie，支持 JSON 数组、JSON 对象和 Cookie 字符串。"""
    raw = (os.environ.get("TIEBA_COOKIES") or "").strip()
    if not raw:
        cookie_file = (os.environ.get("TIEBA_COOKIES_FILE") or "").strip()
        if cookie_file:
            try:
                with open(cookie_file, "r", encoding="utf-8") as f:
                    raw = f.read().strip()
            except OSError as exc:
                log(f"读取 TIEBA_COOKIES_FILE 失败: {exc}")
                return []
    if not raw:
        log("未配置 TIEBA_COOKIES / TIEBA_COOKIES_FILE，请参考教程配置 Cookie")
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
        cookies.append({"name": str(name), "value": str(value)})

    if not cookies:
        log("Cookie 内容为空，请重新获取")
    return cookies


def cookie_header(cookies: list[dict]) -> str:
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


def http_request(
    cookies: list[dict],
    method: str,
    path: str,
    *,
    host: str = HOST,
    use_https: bool = True,
    body: str | None = None,
    timeout: int = 20,
    retries: int = 2,
) -> tuple[int, bytes]:
    """访问贴吧接口。tbs / sign 接口会 301 跳到 http，因此支持切换协议。"""
    headers = {
        "Host": host,
        "User-Agent": UA,
        "Referer": "https://tieba.baidu.com/",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "close",
        "Cookie": cookie_header(cookies),
    }
    if method == "POST":
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["X-Requested-With"] = "XMLHttpRequest"

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        conn = None
        try:
            if use_https:
                conn = http.client.HTTPSConnection(
                    host, 443, timeout=timeout, context=ssl.create_default_context()
                )
            else:
                conn = http.client.HTTPConnection(host, 80, timeout=timeout)
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, data
        except (OSError, http.client.HTTPException) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1 + attempt)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    raise last_error  # type: ignore[misc]


def fetch_login(cookies: list[dict]) -> tuple[bool, str]:
    """校验登录并获取 tbs。移动端首页接口 + /dc/common/tbs 双通道确认。"""
    tbs = ""
    logged_in = False

    try:
        status, data = http_request(cookies, "GET", "/mo/q/newmoindex")
        obj = json.loads(data)
        if status == 200 and obj.get("no") == 0:
            info = obj.get("data") or {}
            if info.get("uid"):
                logged_in = True
                tbs = info.get("tbs") or info.get("itb_tbs") or ""
    except (ValueError, OSError, http.client.HTTPException) as exc:
        log(f"读取用户信息失败: {exc}")

    # /dc/common/tbs 是判断登录状态最可靠的接口
    try:
        status, data = http_request(cookies, "GET", "/dc/common/tbs", use_https=False)
        obj = json.loads(data)
        if obj.get("is_login") == 1:
            logged_in = True
            tbs = obj.get("tbs") or tbs
    except (ValueError, OSError, http.client.HTTPException) as exc:
        log(f"获取 tbs 失败: {exc}")

    return logged_in, tbs


# --------------------------------------------------------------------------- #
# 关注的贴吧列表
# --------------------------------------------------------------------------- #
def collect_forums(cookies: list[dict]) -> list[str]:
    forums: list[str] = []
    seen: set[str] = set()
    empty_pages = 0
    for pn in range(1, 31):  # 每页 20 个，最多 600 个吧
        try:
            status, data = http_request(
                cookies, "GET", f"/f/like/mylike?pn={pn}", timeout=25
            )
        except (OSError, http.client.HTTPException) as exc:
            log(f"第 {pn} 页请求失败: {exc}")
            empty_pages += 1
            if empty_pages >= 2:
                break
            continue
        if status != 200:
            log(f"第 {pn} 页返回 HTTP {status}")
            empty_pages += 1
            if empty_pages >= 2:
                break
            continue

        # 列表页是 GBK 编码，链接里的 kw 也是 GBK 百分号编码
        html = data.decode("gbk", "ignore")
        added = 0
        for raw in re.findall(r'/f\?kw=([^"&]+)', html):
            kw = unquote(raw, encoding="gbk", errors="replace").strip()
            if not kw or kw in seen:
                continue
            seen.add(kw)
            forums.append(kw)
            added += 1
        log(f"第 {pn} 页：新增 {added} 个吧，累计 {len(forums)} 个")
        if added == 0:
            empty_pages += 1
            if empty_pages >= 2:
                break
        else:
            empty_pages = 0
        nap()

    return forums


# --------------------------------------------------------------------------- #
# 签到
# --------------------------------------------------------------------------- #
def client_sign(cookies: list[dict], kw: str, tbs: str) -> tuple[str, str]:
    """客户端签名接口（HTTPS），不会触发网页版验证码。"""
    sign = hashlib.md5(
        f"kw={kw}tbs={tbs}tiebaclient!!!".encode("utf-8")
    ).hexdigest()
    body = (
        f"kw={quote(kw, safe='')}"
        f"&tbs={quote(tbs, safe='')}"
        f"&sign={sign}"
    )
    try:
        status, data = http_request(
            cookies,
            "POST",
            "/c/c/forum/sign",
            host="c.tieba.baidu.com",
            body=body,
            timeout=25,
        )
    except (OSError, http.client.HTTPException) as exc:
        return "error", f"客户端接口请求失败: {exc}"

    if status != 200:
        return "error", f"客户端接口 HTTP {status}"

    try:
        obj = json.loads(data)
    except ValueError:
        return "error", "客户端接口返回了非 JSON 内容"

    if obj.get("user_info"):
        return "success", "签到成功"

    code = str(obj.get("error_code") or "")
    message = (obj.get("error_msg") or "").strip()
    if code in ("0", "") and not message:
        return "success", "签到成功"
    if code == "160002" or "已经签" in message or "已签" in message:
        return "already", "今天已签到"
    if code == "340006":
        return "error", "贴吧目录异常或未加入该吧"
    if code == "2150040":
        return "error", "需要验证码（客户端接口）"
    return "error", f"{code} {message}".strip()


def web_sign(cookies: list[dict], kw: str, tbs: str) -> tuple[str, str]:
    """网页版签到接口，作为客户端接口失败时的兜底。"""
    body = f"ie=utf-8&kw={quote(kw, safe='')}&tbs={quote(tbs, safe='')}"
    try:
        status, data = http_request(
            cookies, "POST", "/sign/add", use_https=False, body=body, timeout=25
        )
    except (OSError, http.client.HTTPException) as exc:
        return "error", f"请求失败: {exc}"

    if status != 200:
        return "error", f"HTTP {status}"

    try:
        obj = json.loads(data)
    except ValueError:
        return "error", "接口返回了非 JSON 内容，可能被风控"

    no = obj.get("no")
    error = (obj.get("error") or "").strip()

    if no == 0:
        return "success", "签到成功"
    if no == 1101 or "已经签" in error or "已签" in error:
        return "already", "今天已签到"
    if no == 1011:
        return "error", "未加入该吧或等级不足"
    if "tbs" in error.lower():
        return "error", f"tbs 失效：{error}"
    if no == 2150040 or "vcode" in error.lower():
        return "error", "需要验证码"
    return "error", f"no={no} {error}".strip()


def sign_forum(cookies: list[dict], kw: str, tbs: str) -> tuple[str, str]:
    """优先走客户端接口，失败时再用网页接口兜底。"""
    status, message = client_sign(cookies, kw, tbs)
    if status != "error":
        return status, message

    # 未加入 / 需要验证码时网页接口也救不了，直接返回
    if "未加入" in message or "验证码" in message:
        return status, message

    web_status, web_message = web_sign(cookies, kw, tbs)
    if web_status != "error":
        return web_status, web_message
    return "error", f"{message}；网页接口：{web_message}"


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
    if ACCOUNT:
        title = f"{ACCOUNT} {title}"
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
    global DELAY
    started = time.time()
    log("=" * 60)
    log("贴吧自动签到开始")

    try:
        DELAY = max(0.0, float(os.environ.get("TIEBA_DELAY") or DELAY))
    except ValueError:
        pass

    cookies = read_cookie()
    if not cookies:
        save_log()
        return 1

    try:
        logged_in, tbs = fetch_login(cookies)
        if not logged_in:
            log("Cookie 无效或已过期，请重新运行「点我生成Cookie.py」获取 Cookie")
            send_notification(
                "贴吧签到失败",
                "Cookie 无效或已过期，请重新运行「点我生成Cookie.py」获取 Cookie，"
                "并更新 TIEBA_COOKIES / secrets/tieba_cookies.json。",
            )
            return 1
        log("登录成功" + ("，已获取 tbs" if tbs else "，但未获取到 tbs"))

        forums = collect_forums(cookies)
        if not forums:
            log("没有读取到任何关注的贴吧，请确认账号已关注贴吧")
            send_notification("贴吧签到失败", "没有读取到关注的贴吧，请确认 Cookie 对应账号已关注贴吧。")
            return 1

        try:
            limit = int(os.environ.get("TIEBA_LIMIT") or 0)
        except ValueError:
            limit = 0
        if limit > 0:
            forums = forums[:limit]
            log(f"TIEBA_LIMIT={limit}，本次只处理前 {len(forums)} 个吧")
        log(f"共发现 {len(forums)} 个关注的吧")

        results: list[tuple[str, str, str]] = []
        for index, kw in enumerate(forums, 1):
            log(f"[{index}/{len(forums)}] {kw}吧 签到中...")
            status, message = sign_forum(cookies, kw, tbs)
            if status == "error" and ("tbs 失效" in message or "被风控" in message):
                # tbs 过期时刷新一次再试
                _, new_tbs = fetch_login(cookies)
                if new_tbs and new_tbs != tbs:
                    tbs = new_tbs
                    nap(1.0, 2.0)
                    status, message = sign_forum(cookies, kw, tbs)
            log(f"    {message}")
            results.append((kw, status, message))
            if index < len(forums) and DELAY:
                time.sleep(random.uniform(DELAY * 0.6, DELAY * 1.4))

        elapsed = time.time() - started
        title, content = build_summary(results, elapsed)
        ok = sum(1 for r in results if r[1] in ("success", "already"))
        log("-" * 60)
        log(title)
        log(f"总用时 {elapsed:.0f} 秒")
        send_notification(title, content)
        # 全部失败时返回非 0，方便在 Actions 页面看到红色失败标记
        return 0 if ok else 1

    except Exception as exc:
        log(f"程序运行异常: {exc}")
        send_notification("贴吧签到异常", f"程序运行异常：{exc}")
        return 1
    finally:
        save_log()


if __name__ == "__main__":
    sys.exit(main())
