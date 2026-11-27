# 用 Docker 部署（服务器 / NAS / 软路由）

本方案把签到脚本跑在容器里，纯接口签到（无需浏览器），按北京时间每天定时执行一次，
日志写入 `./logs/tieba_sign.log`，同时 `docker logs` 也能看到实时输出。

适合有 7×24 小时在线 Docker 主机的场景（本项目已在 iStoreOS / x86_64 上验证）。
镜像基于 `python:3.12-slim`，体积约 60 MB；200 多个吧通常 3-5 分钟跑完。

---

## 1. 文件说明

| 文件 | 作用 |
| --- | --- |
| `Dockerfile` | Python 3.12 运行环境（纯接口签到，无浏览器） |
| `docker-compose.yml` | 容器编排：定时调度、日志、时区 |
| `scheduler.py` | 每天按 `RUN_AT` 执行 `run_tieba_qiandao.py` |
| `.env.example` | 配置模板，复制为 `.env` 使用 |
| `secrets/` | 存放 `tieba_cookies.json`（已被 git 忽略） |
| `logs/` | 运行日志（已被 git 忽略） |

---

## 2. 准备 Cookie

Cookie 的获取方式见 [DEPLOY.md](DEPLOY.md) 第 2 节，本地运行 `点我生成Cookie.py` 扫码后得到
`tieba_cookies.json`。

拿到后二选一配置：

**方式 A：直接写进 `.env`（适合 `BDUSS=...; STOKEN=...` 字符串）**

```bash
cp .env.example .env
vi .env
# TIEBA_COOKIES=BDUSS=xxxx; STOKEN=yyyy
```

**方式 B：挂载 JSON 文件（推荐，避免转义问题）**

```bash
mkdir -p secrets
cp tieba_cookies.json secrets/tieba_cookies.json
vi .env
# TIEBA_COOKIES_FILE=/app/secrets/tieba_cookies.json
```

可选：在 `.env` 里填 `SENDKEY=` 开启 Server酱微信通知。

> `.env`、`secrets/`、`logs/` 都已在 `.gitignore` 与 `.dockerignore` 中排除，不会被打包进镜像。

---

## 3. 启动

```bash
# 构建并后台启动
docker compose up -d --build

# 查看容器状态
docker compose ps

# 查看实时日志（应看到“下次执行时间：...”）
docker compose logs -f
```

首次部署建议在 `.env` 中设置 `RUN_ON_START=1`，让容器启动后立刻跑一次验证；
确认成功后改回 `0`，之后只按 `RUN_AT` 定时执行：

```bash
docker compose up -d   # 修改 .env 后重建容器
```

---

## 4. 手动执行一次

```bash
# 不常驻，直接跑一次签到（使用同样的 .env）
docker compose run --rm --entrypoint python tieba-sign -u run_tieba_qiandao.py
```

或者在容器内执行：

```bash
docker exec -it tieba-auto-sign python -u run_tieba_qiandao.py
```

---

## 5. 定时与日志

- 时区固定为 `Asia/Shanghai`，`RUN_AT=02:30` 即北京时间每天 02:30。
- 宿主机时区不影响容器内时间。
- 日志文件：`./logs/tieba_sign.log`（每次运行覆盖，保留最近一次完整结果）。
- 容器日志由 Docker 限制为 10 MB × 3 个文件，不会无限增长。
- 修改 `RUN_AT` 后执行 `docker compose up -d` 生效。

---

## 6. 更新与卸载

```bash
# 拉取新代码后重新构建
docker compose up -d --build

# 停止
docker compose down

# 停止并删除镜像
docker compose down --rmi local
```

---

## 7. 常见问题

**Q：日志提示「未配置 TIEBA_COOKIES / TIEBA_COOKIES_FILE」**
A：`.env` 没填 Cookie，或 `TIEBA_COOKIES_FILE` 指向的文件没挂载进容器。检查 `docker compose config` 输出中的环境变量。

**Q：日志提示「Cookie 无效或已过期」**
A：重新扫码生成 Cookie 并更新 `.env` / `secrets/tieba_cookies.json`，然后 `docker compose up -d`。

**Q：容器启动后很快退出 / 日志报错**
A：先看 `docker compose logs`。常见原因是 `.env` 格式错误或 `secrets/` 目录权限不对（建议 `chmod 700 secrets && chmod 600 secrets/tieba_cookies.json`）。

**Q：签到时间不准**
A：容器内按 `Asia/Shanghai` 计算，与本机时区无关；`docker compose logs` 会打印下次执行时间。

**Q：想一次跑多个账号**
A：当前脚本一次只处理一份 Cookie。多账号可复制一份 compose 服务（改 `container_name`、`TIEBA_COOKIES`）或用多个项目目录。
