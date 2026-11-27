# 部署到 GitHub Actions（从零到跑通）

本文档对应的仓库已经针对 2026 年的 GitHub Actions 环境做过修正：
Chrome 改用 `browser-actions/setup-chrome` 安装（Ubuntu 24.04 已不再提供 apt 版 Chromium），
脚本也修掉了重复点击、`page.close()` 后继续使用页面等 bug。

---

## 0. 你需要准备

- 一个能正常登录的百度贴吧账号
- 一台能运行 Python 的电脑（只用来获取 Cookie，之后不需要一直开着）
- 本仓库在你自己的 GitHub 账号下（Fork 即可）

---

## 1. Fork 并启用 Actions

1. 打开 <https://github.com/nyiu5554/Tieba_auto_sign>，点右上角 **Fork**，Fork 到你自己的账号。
2. 进入你 Fork 后的仓库，点 **Actions** 标签页。
3. 如果是第一次进入，会出现提示，点击 **I understand my workflows, go ahead and enable them**
   （Fork 仓库的定时任务默认是关闭的，必须手动启用一次）。

---

## 2. 本地获取 Cookie

在你自己电脑上：

```bash
# 1) 安装依赖
pip install -r requirements.txt

# 2) 运行取 Cookie 脚本，会弹出浏览器
python 点我生成Cookie.py
```

浏览器打开后，用手机扫码或账号密码登录贴吧。确认已登录（右上角能看到自己的头像/昵称），
回到终端按回车。脚本会在当前目录生成两个文件：

| 文件 | 用途 |
| --- | --- |
| `tieba_cookies.json` | 推荐，包含域名等完整信息 |
| `tieba_cookie.txt` | `BDUSS=...; STOKEN=...` 字符串，更短，直接粘贴也行 |

> ⚠️ 这两个文件等同于账号密码，**不要上传、不要提交、不要发给别人**。仓库里已经用 `.gitignore` 忽略它们。

脚本兼容两种格式，`TIEBA_COOKIES` 填哪一种都可以。

---

## 3. 配置仓库 Secret

在你的仓库中：

**Settings → Secrets and variables → Actions → New repository secret**

### 必填

| Name | Secret |
| --- | --- |
| `TIEBA_COOKIES` | 粘贴 `tieba_cookies.json` 的全部内容，或 `tieba_cookie.txt` 的内容 |

### 可选：微信通知（Server酱）

1. 打开 <https://sct.ftqq.com/>，微信扫码登录，复制 **SendKey**。
2. 再新建一个 Secret：

| Name | Secret |
| --- | --- |
| `SENDKEY` | 你的 Server酱 SendKey |

> 旧教程里的变量名 `SendKey` 也兼容，脚本会依次读取 `SENDKEY`、`SendKey`。

---

## 4. 手动跑一次验证

1. 仓库页面 → **Actions** → 左侧 **Tieba Qiandao** → 右侧 **Run workflow** → **Run workflow**。
2. 刷新页面，点进这次运行，展开 **Run Tieba Qiandao** 查看实时日志。
3. 成功时日志结尾类似：

```
[2026-11-27 02:35:10] 贴吧签到 12 成功 / 30 已签 / 0 失败
[2026-11-27 02:35:10] 总用时 96 秒
```

4. 运行结束后，在本次运行的 **Artifacts** 里可以下载 `tieba-sign-log` 日志文件。

---

## 5. 定时运行

工作流文件：`.github/workflows/tieba.yml`

```yaml
on:
  schedule:
    - cron: "30 18 * * *"   # UTC 18:30 = 北京时间 02:30
```

GitHub 的 cron 使用 **UTC**，想改时间就改这里：

| 北京时间 | 应填 cron |
| --- | --- |
| 00:30 | `30 16 * * *` |
| 02:30 | `30 18 * * *` |
| 08:00 | `0 0 * * *` |
| 12:00 | `0 4 * * *` |
| 22:00 | `0 14 * * *` |

注意事项：

- GitHub 的定时任务**不保证准点**，高峰期可能延迟几分钟到几十分钟。
- 仓库连续 **60 天没有任何提交**时，GitHub 会自动停用定时任务。
  届时到 Actions 页面点一次 **Enable workflow** 即可恢复，或者随便提交一次。
- Fork 仓库的定时任务默认关闭，必须按第 1 步手动启用。

---

## 6. 本地调试（可选）

```bash
# Linux / macOS
export TIEBA_COOKIES="$(cat tieba_cookies.json)"
export SENDKEY="你的SendKey"          # 可选
HEADLESS=0 python run_tieba_qiandao.py # HEADLESS=0 会显示浏览器窗口
```

```powershell
# Windows PowerShell
$env:TIEBA_COOKIES = Get-Content tieba_cookies.json -Raw
$env:HEADLESS = "0"
python run_tieba_qiandao.py
```

可用环境变量：

| 变量 | 说明 |
| --- | --- |
| `TIEBA_COOKIES` | 必填，Cookie JSON 或字符串 |
| `SENDKEY` / `SendKey` | 可选，Server酱推送 |
| `CHROME_PATH` | 可选，指定 Chrome/Chromium 路径 |
| `HEADLESS` | 可选，设为 `0` 显示浏览器窗口，默认无头模式 |

---

## 7. 常见问题

**Q：日志提示「Cookie 无效或已过期」**
A：重新运行 `点我生成Cookie.py`，确认登录后再按回车，然后更新 `TIEBA_COOKIES`。
注意不要复制到多余的换行或引号。

**Q：日志提示「没有读取到任何关注的贴吧」**
A：确认该账号确实关注了贴吧；也可能是 Cookie 不完整，优先使用 `tieba_cookies.json` 的完整内容。

**Q：日志提示「找不到签到按钮」**
A：贴吧页面结构变化，或该吧处于被限制状态。脚本会自动尝试接口兜底；仍失败时把日志发出来便于修正选择器。

**Q：定时任务没有运行**
A：检查 Actions 是否已启用、仓库是否 60 天无提交、cron 是否写成了 UTC 时间。

**Q：通知收不到**
A：确认 `SENDKEY` 配置正确，且 Server酱账号已绑定微信；日志里会打印通知发送结果。

**Q：Actions 里 Chrome 启动失败**
A：本仓库已使用 `browser-actions/setup-chrome` 并在脚本里加了 `--no-sandbox`、`--disable-dev-shm-usage`。
如果仍然失败，检查运行日志中 `CHROME_PATH` 是否为空。

---

## 8. 安全提醒

- Cookie / SendKey 只放在 **GitHub Secrets** 里，不要写进代码或提交历史。
- 如果 Cookie 曾经泄露（例如误提交），立刻重新登录贴吧使旧 Cookie 失效，再重新生成。
- 本项目仅供学习自动化使用，请遵守贴吧使用条款。
