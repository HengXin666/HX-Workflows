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
tg/tasks.yml    # 启用哪些任务、运行哪个 job
tg/signins.yml  # 和谁对话、发什么、点什么、转发什么
```

GitHub Actions 已经写死为：**每 24 小时运行一次，北京时间 00:15**。

定时触发时，workflow 会直接运行 `tg/tasks.yml` 里所有 `enabled: true` 的任务。真正的签到对象、动作、过滤规则由 `tg/signins.yml` 控制。

这样机器人账号、目标 bot、关键词、转发规则失效时，你只需要编辑 YAML，不需要改 Python 脚本。

## 文件说明

```text
.github/workflows/tg-orchestrator.yml  # GitHub Actions 入口
tg/pyproject.toml                      # uv 项目配置
tg/.python-version                     # Python 版本
tg/runner.py                           # 通用任务编排器
tg/tasks.yml                           # 当前启用的任务配置
tg/signins.yml                         # TG 签到明文配置
tg/tasks.example.yml                   # 更多任务配置示例
tg/CONFIGURE.md                        # 签到和转发配置教程
tg/TELEGRAM_KEYS.md                    # TG 参数获取教程
tg/scripts/example_task.py             # 示例/冒烟测试任务
tg/scripts/sign_from_config.py         # 从 signins.yml 执行签到
tg/scripts/gen_session.py              # 本地生成 session string
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

4. 编辑 `tg/tasks.yml`，启用对应任务：

```yaml
- id: tg-sign-my-sign
  enabled: true
  schedule:
    - daily:00:15
  command: uv run python scripts/sign_from_config.py run my-sign
```

注意：定时触发时会运行所有 `enabled: true` 的任务，所以 `schedule` 更多是给手动 `due/run-due` 查看使用。

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
