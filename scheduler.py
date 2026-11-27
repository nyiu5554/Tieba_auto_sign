#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""容器内定时调度：按 RUN_AT（北京时间 HH:MM）每天执行一次签到脚本。

环境变量:
    RUN_AT        每天执行时间，默认 02:30
    RUN_ON_START  设为 1 时容器启动后立即执行一次，默认 0
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
RUN_AT = (os.environ.get("RUN_AT") or "02:30").strip()
RUN_ON_START = (os.environ.get("RUN_ON_START") or "0").strip() == "1"

_stopping = False


def log(msg: str) -> None:
    stamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


def parse_run_at() -> tuple[int, int]:
    try:
        hour, minute = (int(x) for x in RUN_AT.split(":", 1))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (ValueError, TypeError):
        pass
    log(f"RUN_AT={RUN_AT!r} 格式不正确，回退到 02:30")
    return 2, 30


def next_run(now: datetime, hour: int, minute: int) -> datetime:
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def run_once() -> int:
    log("开始执行签到")
    code = subprocess.call([sys.executable, "-u", "run_tieba_qiandao.py"])
    log(f"签到执行结束，退出码 {code}")
    return code


def _handle_stop(signum, frame) -> None:  # noqa: ARG001
    global _stopping
    _stopping = True
    log(f"收到信号 {signum}，准备退出")


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    hour, minute = parse_run_at()
    log(f"调度器启动，每天 {hour:02d}:{minute:02d}（北京时间）执行，RUN_ON_START={int(RUN_ON_START)}")

    if RUN_ON_START:
        run_once()

    while not _stopping:
        now = datetime.now(TZ)
        target = next_run(now, hour, minute)
        wait = (target - now).total_seconds()
        log(f"下次执行时间：{target:%Y-%m-%d %H:%M}（约 {wait / 3600:.1f} 小时后）")

        # 分段睡眠，便于及时响应 docker stop
        while wait > 0 and not _stopping:
            time.sleep(min(wait, 30))
            wait -= 30

        if _stopping:
            break
        run_once()

    log("调度器已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
