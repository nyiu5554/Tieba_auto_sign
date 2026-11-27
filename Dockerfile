FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8 \
    PYTHONIOENCODING=utf-8 \
    TZ=Asia/Shanghai \
    TIEBA_LOG_FILE=/app/logs/tieba_sign.log

# 纯接口签到，不需要 Chromium；ca-certificates 用于 HTTPS
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY run_tieba_qiandao.py scheduler.py ./

RUN mkdir -p /app/logs

ENTRYPOINT ["python", "-u", "scheduler.py"]
