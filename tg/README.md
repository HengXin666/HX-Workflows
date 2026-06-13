# TG 任务编排器

这个目录存放一个基于 `uv` 管理的 Python 任务运行器，用于在 GitHub Actions 中编排 Telegram 相关的定时任务。

文档入口：

```text
CONFIGURE.md       # 如何写签到配置、转发过滤、多账号任务
TELEGRAM_KEYS.md   # 如何获取 api_id/api_hash/session/bot token/chat id
signins.yml        # 明文可编辑的签到配置
```

## 设计思路

配置分成两层：

```text
tg/tasks.yml    # 什么时候运行哪个 job
tg/signins.yml  # 和谁对话、发什么、点什么、转发什么
```

GitHub Actions 只负责每 15 分钟唤醒一次 runner。真正的任务时间由 `tg/tasks.yml` 判断；真正的签到对象、动作、过滤规则由 `tg/signins.yml` 控制。

这样机器人账号、目标 bot、关键词、转发规则失效时，你只需要编辑 YAML，不需要改 Python 脚本。

## 文件说明

```text
.github/workflows/tg-orchestrator.yml  # GitHub Actions 入口
tg/pyproject.toml                      # uv 项目配置
tg/.python-version                     # Python 版本
tg/runner.py                           # 通用任务编排器
tg/tasks.yml                           # 当前生效的定时任务配置
tg/signins.yml                         # TG 签到明文配置
tg/tasks.example.yml                   # 更多任务配置示例
tg/CONFIGURE.md                        # 签到和转发配置教程
tg/TELEGRAM_KEYS.md                    # TG 参数获取教程
tg/scripts/example_task.py             # 示例/冒烟测试任务
tg/scripts/sign_from_config.py         # 从 signins.yml 执行签到
tg/scripts/gen_session.py              # 本地生成 session string
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
tg-sign-demo-send
tg-sign-demo-click
```

## 最小使用流程

1. 按 `TELEGRAM_KEYS.md` 获取并配置 GitHub Secrets：

```text
TG_API_ID
TG_API_HASH
TG_SESSION_STRINGS
```

2. 如果要使用通知模式，再配置：

```text
TG_FORWARD_BOT_TOKEN
TG_FORWARD_CHAT_ID
```

3. 编辑 `tg/signins.yml`：

```yaml
- id: my-sign
  enabled: true
  peer: "@example_bot"
  accounts_secret: TG_SESSION_STRINGS
  actions:
    - type: send
      text: "签到"
    - type: wait
      seconds: 5
  forward:
    enabled: true
    mode: notify
    when: [failure, matched]
    include:
      contains: ["签到成功", "已签到", "积分"]
      regex: []
    exclude:
      contains: ["广告"]
      regex: []
```

4. 编辑 `tg/tasks.yml`，让 workflow 定时执行这个 job：

```yaml
- id: tg-sign-my-sign
  enabled: true
  schedule:
    - daily:08:30
  command: uv run python scripts/sign_from_config.py run my-sign
```

## 多账号模式

多账号通过 GitHub Actions Secret `TG_SESSION_STRINGS` 配置。

填写方式是：**一行一个 Telegram session**。

```text
session_string_for_account_1
session_string_for_account_2
session_string_for_account_3
```

然后在 `tg/signins.yml` 里写：

```yaml
accounts_secret: TG_SESSION_STRINGS
```

运行时，每一行 session 都会被当作一个账号依次执行。

## 选择性转发

不是所有消息都会转发。

你可以在 `tg/signins.yml` 里配置：

```yaml
forward:
  enabled: true
  mode: notify
  when:
    - failure
    - matched
  include:
    contains:
      - "签到成功"
      - "积分"
    regex: []
  exclude:
    contains:
      - "广告"
    regex: []
```

含义：

```text
失败 -> 通知
返回消息包含“签到成功”或“积分” -> 通知
返回消息包含“广告” -> 不通知
其他普通消息 -> 不通知
```

## Secrets

建议在 GitHub 仓库中配置这些 Secrets：

```text
TG_API_ID               # Telegram API id
TG_API_HASH             # Telegram API hash
TG_SESSION_STRINGS      # Telegram session，多账号时一行一个
TG_PROXY                # 可选代理，例如 socks5://user:pass@host:port
TG_FORWARD_BOT_TOKEN    # 可选，用于发送通知摘要的 bot token
TG_FORWARD_CHAT_ID      # 可选，通知目标 chat id
```

配置位置：

```text
Settings -> Secrets and variables -> Actions -> New repository secret
```

不要把这些内容提交到仓库：

```text
Telegram session
api_hash
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
uv run python scripts/sign_from_config.py list
```

## 安全提醒

请只自动化你自己的账号，以及你有权限使用的 bot、群组或频道。

不要使用本项目进行骚扰、绕过风控、未授权抓取/转发私密内容，或违反目标 bot/群组/频道规则的行为。
