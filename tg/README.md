# TG 任务编排器

这个目录存放一个基于 `uv` 管理的 Python 任务运行器，用于在 GitHub Actions 中编排 Telegram 相关的定时任务。

更多多账号、签到对象、签到动作配置说明，请看：[`CONFIGURE.md`](./CONFIGURE.md)。

## 设计思路

GitHub Actions 只负责“定时唤醒”。

真正的任务编排逻辑放在：

```text
tg/tasks.yml
```

这样以后你想新增、禁用、调整顺序、增加依赖任务时，大多数情况下只需要改 `tg/tasks.yml`，不需要频繁修改 `.github/workflows/tg-orchestrator.yml`。

## 文件说明

```text
.github/workflows/tg-orchestrator.yml  # GitHub Actions 入口
tg/pyproject.toml                      # uv 项目配置
tg/.python-version                     # Python 版本
tg/runner.py                           # 任务编排器
tg/tasks.yml                           # 当前生效的任务配置
tg/tasks.example.yml                   # 更多配置示例
tg/CONFIGURE.md                        # 多账号和签到配置教程
tg/scripts/example_task.py             # 示例/冒烟测试任务
```

## 支持的定时表达式

```yaml
schedule:
  - every:30       # 每 30 分钟运行一次
  - hourly:05      # 每小时第 05 分钟运行一次
  - daily:08:30    # 每天 08:30 运行一次
  - cron:08:30     # daily:08:30 的别名
```

默认时区是：

```text
Asia/Tokyo
```

注意：GitHub Actions 的 `schedule` 使用 UTC cron，本项目的 workflow 每 15 分钟唤醒一次；具体哪个任务要不要执行，由 `tg/runner.py` 根据 `tg/tasks.yml` 和 `Asia/Tokyo` 时间判断。

## 手动运行

进入 GitHub 仓库页面：

```text
Actions -> TG Orchestrator -> Run workflow
```

可选模式：

```text
list     查看任务列表
due      查看当前到期的任务
run-due  运行当前到期的任务
run-all  运行所有已启用任务
run      运行指定任务
```

当 `mode=run` 时，需要填写 `task_id`，例如：

```text
tg-example
```

## 多账号模式

多账号通过 GitHub Actions Secret `TG_SESSION_STRINGS` 配置。

填写方式是：**一行一个 Telegram session**。

```text
session_string_for_account_1
session_string_for_account_2
session_string_for_account_3
```

然后在 `tg/tasks.yml` 里启用：

```yaml
- id: tg-example-multi-account
  enabled: true
  schedule:
    - daily:08:35
  foreach_secret_lines: TG_SESSION_STRINGS
  command: uv run python scripts/example_task.py --account "$TG_ACCOUNT_INDEX"
```

运行时，每一行 session 都会被当作一个账号。runner 会自动注入：

```text
TG_SESSION_STRING=<当前账号的 session string>
TG_ACCOUNT_INDEX=1,2,3...
```

也就是说，同一个任务会按账号数量执行多次。

## Secrets

建议在 GitHub 仓库中配置这些 Secrets：

```text
TG_SESSION_STRINGS       # Telegram session，多账号时一行一个
TG_PROXY                 # 可选代理，例如 socks5://user:pass@host:port
TG_FORWARD_BOT_TOKEN     # 可选，用于发送通知/转发结果的 bot token
TG_FORWARD_CHAT_ID       # 可选，通知/转发目标 chat id
```

配置位置：

```text
Settings -> Secrets and variables -> Actions -> New repository secret
```

不要把这些内容提交到仓库：

```text
Telegram session
Bot token
手机号
验证码
私有群/频道敏感 chat id
```

## 本地测试

```bash
cd tg
uv sync
uv run python runner.py list
uv run python runner.py run tg-example
```

## 当前状态

当前仓库已经包含任务编排器和一个示例任务。

`tg/scripts/example_task.py` 只是冒烟测试脚本，用来验证 GitHub Actions、uv、Secrets 注入、多账号循环是否正常。

真正连接 Telegram 进行签到时，需要继续新增实际脚本，例如：

```text
tg/scripts/sign_message.py       # 给目标 bot/群发送签到消息
tg/scripts/sign_click_button.py  # 点击目标 bot 的签到按钮
```

这些脚本应该从环境变量读取配置：

```text
TG_SESSION_STRING
TG_PROXY
TARGET_PEER
SIGN_MESSAGE
CLICK_BUTTON_TEXT
FORWARD_TO
```

## 安全提醒

请只自动化你自己的账号，以及你有权限使用的 bot、群组或频道。

不要使用本项目进行骚扰、绕过风控、未授权抓取/转发私密内容，或违反目标 bot/群组/频道规则的行为。
