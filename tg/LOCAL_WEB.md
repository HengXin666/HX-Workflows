# TG 本地 Web 配置器

本目录的 `web/` 是一个仅本地部署的 React + TypeScript 配置器，用于可视化生成 Telegram 多账号工作流配置。

支持内容：

- 区分主账号和小号。
- 扫码后读取账号名称和头像，并显示在 Web 页面。
- 主页可配置 Telegram 代理，默认 `http://127.0.0.1:2334`。
- 二维码登录在 Web 弹窗中显示。
- 主账号用于群聊或频道发邀请链接、任务入口等内容。
- 小号池用于打开 bot 邀请入口、发送签到文本、点击按钮、等待回执。
- 使用 React Flow 编排所有小号都会执行的任务图。
- 可视化生成 `signins.yml`、`tasks.yml` 和主账号本地命令。
- 通过本地 API 扫码导入账号，凭证写入 `tg/sessions/`，该目录已被 git 忽略。

## 启动

一键启动本地 API 和 Web 页面：

```bash
cd components/HX-Workflows/tg
./run_web.sh
```

浏览器打开：

```text
http://127.0.0.1:5173
```

可选端口配置：

```bash
TG_WEB_PORT=5174 TG_WEB_API_PORT=8766 ./run_web.sh
```

也可以手动分开启动。

终端 1：启动本地 API。

```bash
uv run python scripts/local_web_api.py
```

终端 2：启动 Web 页面。

```bash
cd web
npm install
npm run dev
```

## 账号导入

Web 页面分为三个页面：

```text
主页配置     配置主账号、小号池，扫码导入账号并显示头像和名称
编排流       使用 React Flow 编排小号统一执行的任务图
本地执行     选择保存的工作流，在本地用小号池执行
```

Web 页面中的“扫码导入”会调用：

```text
http://127.0.0.1:8765
```

导入后的 session string 写入：

```text
tg/sessions/tg_session_strings.txt
```

这个文件会作为本地小号账号池使用，每一行是一个小号 session string。

主账号扫码导入会写入：

```text
tg/sessions/tg_main_session_string.txt
```

账号元数据写入：

```text
tg/sessions/tg_session_strings.accounts.json
tg/sessions/tg_main_session_string.accounts.json
```

保存的 Web 工作流写入：

```text
tg/sessions/web_workflows.json
```

如果账号开启了二步验证，当前 Web API 会提示改用命令行脚本：

```bash
uv run python scripts/enroll_accounts.py
```

## 使用生成结果

页面“一键生成”区域会输出：

```text
signins.yml
tasks.yml
主账号命令
```

复制对应内容到 `tg/signins.yml` 和 `tg/tasks.yml` 后即可用现有 runner 执行。

本地运行小号签到时，页面会生成下面这种命令：

```bash
TG_SESSION_STRINGS="$(cat sessions/tg_session_strings.txt)" uv run python scripts/sign_from_config.py run-enabled --mail
```

敏感凭证不要提交到仓库。
