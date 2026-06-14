# TG 任务编排器

这个目录存放一个基于 `uv` 管理的任务运行器，用于在 GitHub Actions 中编排 Telegram 相关的定时任务。

当前推荐主路径：**使用 `tg-signer` 登录和执行签到**。

文档入口：

```text
TG_SIGNER.md       # 推荐：tg-signer 登录、凭证保存、多账号、运行方式
CONFIGURE.md       # 自定义 YAML 签到配置、转发过滤、多账号任务
TELEGRAM_KEYS.md   # Telegram 参数说明、BotFather token 和 API ID 区别
signins.yml        # 自定义脚本使用的明文签到配置
```

## 设计思路

配置分成两层：

```text
tg/tasks.yml    # 启用哪些任务、运行哪个 job
tg/signins.yml  # 自定义脚本使用：和谁对话、发什么、点什么、转发什么
```

GitHub Actions 已经写死为：**每 24 小时运行一次，北京时间 00:15**。

定时触发时，workflow 会直接运行 `tg/tasks.yml` 里所有 `enabled: true` 的任务。

如果走 `tg-signer`，签到动作由 `tg-signer` 自己的 `.signer` 配置控制；如果走自定义脚本，签到动作由 `tg/signins.yml` 控制。

## 文件说明

```text
.github/workflows/tg-orchestrator.yml  # GitHub Actions 入口
tg/pyproject.toml                      # uv 项目配置，已安装 tg-signer[yaml]
tg/.python-version                     # Python 版本
tg/runner.py                           # 通用任务编排器
tg/tasks.yml                           # 当前启用的任务配置
tg/TG_SIGNER.md                        # tg-signer 使用说明
tg/signins.yml                         # 自定义脚本使用的 TG 签到明文配置
tg/tasks.example.yml                   # 更多任务配置示例
tg/CONFIGURE.md                        # 自定义脚本签到和转发配置教程
tg/TELEGRAM_KEYS.md                    # TG 参数获取和区别说明
tg/scripts/example_task.py             # 示例/冒烟测试任务
tg/scripts/sign_from_config.py         # 自定义脚本：从 signins.yml 执行签到
tg/scripts/gen_session.py              # 自定义脚本：本地生成 session string
```

## 固定运行时间

当前 workflow 固定为：

```text
北京时间 00:15
每 24 小时运行一次
```

GitHub Actions 的 cron 使用 UTC，所以 workflow 里写的是：

```yaml
- cron: "15 16 * * *"
```

含义是：

```text
UTC 16:15 = 北京时间次日 00:15
```

为了避免 GitHub Actions 延迟几分钟导致内部定时判断错过，定时触发时不会再执行 `run-due`，而是直接执行：

```bash
uv run python runner.py run-all
```

也就是：运行所有 `enabled: true` 的任务。

## 推荐使用流程：tg-signer

1. 本地登录：

```bash
cd tg
uv sync
uv run tg-signer login
```

2. 本地配置签到任务：

```bash
uv run tg-signer run my_sign
```

3. 本地测试运行一次：

```bash
uv run tg-signer run-once my_sign
```

4. 把可提交的 `.signer` 配置提交到仓库，把 session 凭证放到 GitHub Secrets。

详细说明看：[`TG_SIGNER.md`](./TG_SIGNER.md)。

## 手动运行

进入 GitHub 仓库页面：

```text
Actions -> TG Orchestrator -> Run workflow
```

可选模式：

```text
list     查看任务列表
due      查看当前到期的任务
run-due  手动运行当前到期的任务
run-all  手动运行所有已启用任务
run      手动运行指定任务
```

当 `mode=run` 时，需要填写 `task_id`，例如：

```text
tg-example
tg-sign-demo-send
tg-sign-demo-click
```

## 多账号模式

`tg-signer` 支持账号名和 session string。

本项目建议使用 GitHub Secret：

```text
TG_SESSION_STRINGS
```

填写方式是：**一行一个 Telegram session**。

```text
session_string_for_account_1
session_string_for_account_2
session_string_for_account_3
```

不要提交这些内容：

```text
*.session
*.session-journal
TG_SESSION_STRING
手机号
验证码
Bot token
```

## BotFather token 的用途

BotFather token 可以用来发通知摘要，例如：

```text
TG_FORWARD_BOT_TOKEN
TG_FORWARD_CHAT_ID
```

但它不能替代用户账号 session，不能代替用户账号去和其他 bot 签到。

## 本地测试

```bash
cd tg
uv sync
uv run python runner.py list
uv run python runner.py run tg-example
uv run tg-signer --help
```

## 安全提醒

请只自动化你自己的账号，以及你有权限使用的 bot、群组或频道。

不要使用本项目进行骚扰、绕过风控、未授权抓取/转发私密内容，或违反目标 bot/群组/频道规则的行为。
