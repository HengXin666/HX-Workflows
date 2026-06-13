# TG 自动任务配置指南

本文档说明如何配置：

1. 多个 Telegram 账号
2. 每个任务和谁对话进行签到
3. 如何在 `tg/tasks.yml` 中编排多个签到任务
4. 哪些内容应该放 GitHub Secrets，哪些内容可以写进 YAML

> 不要把 Telegram session、验证码、手机号、Bot Token 提交到仓库。所有敏感内容都放 GitHub Actions Secrets。

---

## 0. 先理解三个概念

### 操作账号

操作账号指的是：**哪个 Telegram 用户账号去执行签到**。

在本项目里，操作账号通过 GitHub Secret `TG_SESSION_STRINGS` 提供。

```text
TG_SESSION_STRINGS
```

它是一个多行 Secret：

```text
session_string_for_account_1
session_string_for_account_2
session_string_for_account_3
```

runner 会逐行读取，每一行当成一个账号，并自动注入：

```text
TG_SESSION_STRING=<当前账号的 session string>
TG_ACCOUNT_INDEX=1/2/3...
```

---

### 签到对象

签到对象指的是：**这个账号要去和谁对话**。

常见形式：

| 类型 | 示例 | 说明 |
|---|---|---|
| Bot 用户名 | `@example_bot` | 最常见，给某个 bot 发 `/checkin`、`签到` 等 |
| 群组用户名 | `@example_group` | 账号必须已经加入该群 |
| 频道用户名 | `@example_channel` | 只适合需要读取/转发频道消息的场景 |
| 数字 chat id | `-1001234567890` | 私有群/频道常用，需要你自己获取 |

在配置里建议命名为：

```yaml
TARGET_PEER: "@example_bot"
```

---

### 签到动作

签到动作指的是：**对签到对象做什么**。

常见动作：

| 动作 | 示例 |
|---|---|
| 发消息 | `签到`、`/checkin`、`/start` |
| 点击按钮 | 点击文本为 `签到` 的 inline button / keyboard button |
| 转发消息 | 把目标聊天里的新消息转发到另一个聊天 |
| 签到后通知 | 把运行结果发给你的通知 bot 或群 |

在配置里建议命名为：

```yaml
SIGN_MESSAGE: "签到"
CLICK_BUTTON_TEXT: "签到"
FORWARD_TO: "@your_log_channel"
```

---

## 1. GitHub Secrets 怎么配置

进入你的仓库：

```text
Settings -> Secrets and variables -> Actions -> New repository secret
```

建议先创建这些 Secrets：

| Secret 名称 | 是否必需 | 用途 |
|---|---:|---|
| `TG_SESSION_STRINGS` | 是 | 多账号 session，一行一个账号 |
| `TG_PROXY` | 否 | 代理，例如 `socks5://user:pass@host:port` |
| `TG_FORWARD_BOT_TOKEN` | 否 | 用 Bot API 发送通知时使用 |
| `TG_FORWARD_CHAT_ID` | 否 | 通知发送到哪个聊天 |

### 多账号 Secret 示例

`TG_SESSION_STRINGS` 的值应该是这样：

```text
1Axxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
1Bxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
1Cxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

注意：

- 一行一个账号
- 不要加逗号
- 不要加引号
- 不要把它写进 `tasks.yml`
- 不要发给 ChatGPT、别人或公开日志

---

## 2. `tasks.yml` 中如何启用多账号

单账号任务可以直接运行：

```yaml
- id: sign-example-single
  enabled: true
  schedule:
    - daily:08:30
  command: uv run python scripts/example_task.py
```

多账号任务需要加：

```yaml
foreach_secret_lines: TG_SESSION_STRINGS
```

完整示例：

```yaml
- id: sign-example-multi
  enabled: true
  schedule:
    - daily:08:30
  foreach_secret_lines: TG_SESSION_STRINGS
  env:
    TARGET_PEER: "@example_bot"
    SIGN_MESSAGE: "签到"
  command: uv run python scripts/example_task.py --account "$TG_ACCOUNT_INDEX"
```

运行时会变成：

```text
账号 1 -> TG_SESSION_STRING=第 1 行 session -> 对 @example_bot 发 签到
账号 2 -> TG_SESSION_STRING=第 2 行 session -> 对 @example_bot 发 签到
账号 3 -> TG_SESSION_STRING=第 3 行 session -> 对 @example_bot 发 签到
```

---

## 3. 如何配置“和谁对话进行签到”

核心字段是 `TARGET_PEER`。

### 场景 A：和某个 bot 对话签到

例如你要让所有账号每天 08:30 给 `@example_bot` 发 `签到`：

```yaml
- id: sign-example-bot
  enabled: true
  schedule:
    - daily:08:30
  foreach_secret_lines: TG_SESSION_STRINGS
  env:
    TARGET_PEER: "@example_bot"
    SIGN_MESSAGE: "签到"
  command: uv run python scripts/sign_message.py
```

含义：

```text
每个账号 -> 打开 @example_bot -> 发送 签到
```

---

### 场景 B：发送 `/checkin`

```yaml
- id: sign-checkin-command
  enabled: true
  schedule:
    - daily:08:35
  foreach_secret_lines: TG_SESSION_STRINGS
  env:
    TARGET_PEER: "@example_bot"
    SIGN_MESSAGE: "/checkin"
  command: uv run python scripts/sign_message.py
```

---

### 场景 C：先发 `/start`，再发 `签到`

可以拆成两个任务，并用 `needs` 编排顺序：

```yaml
- id: sign-start
  enabled: true
  schedule:
    - daily:08:30
  foreach_secret_lines: TG_SESSION_STRINGS
  env:
    TARGET_PEER: "@example_bot"
    SIGN_MESSAGE: "/start"
  command: uv run python scripts/sign_message.py

- id: sign-after-start
  enabled: true
  needs:
    - sign-start
  schedule:
    - daily:08:31
  foreach_secret_lines: TG_SESSION_STRINGS
  env:
    TARGET_PEER: "@example_bot"
    SIGN_MESSAGE: "签到"
  command: uv run python scripts/sign_message.py
```

---

### 场景 D：点击按钮签到

如果目标 bot 不是靠文字命令，而是需要点击按钮，可以这样描述：

```yaml
- id: sign-click-button
  enabled: true
  schedule:
    - daily:08:30
  foreach_secret_lines: TG_SESSION_STRINGS
  env:
    TARGET_PEER: "@example_bot"
    BOOT_MESSAGE: "/start"
    CLICK_BUTTON_TEXT: "签到"
  command: uv run python scripts/sign_click_button.py
```

含义：

```text
每个账号 -> 打开 @example_bot -> 发送 /start -> 查找按钮 签到 -> 点击
```

---

### 场景 E：把签到结果转发到另一个聊天

如果脚本支持转发，可以加：

```yaml
- id: sign-and-forward
  enabled: true
  schedule:
    - daily:08:30
  foreach_secret_lines: TG_SESSION_STRINGS
  env:
    TARGET_PEER: "@example_bot"
    SIGN_MESSAGE: "签到"
    FORWARD_TO: "@your_log_channel"
  command: uv run python scripts/sign_message.py --forward
```

含义：

```text
每个账号 -> 签到 -> 读取返回消息 -> 转发到 @your_log_channel
```

---

## 4. 多个不同签到对象怎么写

如果你有多个 bot，例如：

```text
@example_bot_a 每天 08:30 发送 签到
@example_bot_b 每天 08:40 发送 /checkin
@example_bot_c 每天 09:00 点击按钮 签到
```

可以这样写：

```yaml
tasks:
  - id: sign-bot-a
    enabled: true
    schedule:
      - daily:08:30
    foreach_secret_lines: TG_SESSION_STRINGS
    env:
      TARGET_PEER: "@example_bot_a"
      SIGN_MESSAGE: "签到"
    command: uv run python scripts/sign_message.py

  - id: sign-bot-b
    enabled: true
    schedule:
      - daily:08:40
    foreach_secret_lines: TG_SESSION_STRINGS
    env:
      TARGET_PEER: "@example_bot_b"
      SIGN_MESSAGE: "/checkin"
    command: uv run python scripts/sign_message.py

  - id: sign-bot-c
    enabled: true
    schedule:
      - daily:09:00
    foreach_secret_lines: TG_SESSION_STRINGS
    env:
      TARGET_PEER: "@example_bot_c"
      BOOT_MESSAGE: "/start"
      CLICK_BUTTON_TEXT: "签到"
    command: uv run python scripts/sign_click_button.py
```

---

## 5. 每个账号签到不同对象怎么写

如果账号 A、B、C 要签到同一个对象，用 `TG_SESSION_STRINGS` 就够了。

如果每个账号要签到不同对象，建议新建多个 Secret：

```text
TG_SESSION_STRINGS_BOT_A
TG_SESSION_STRINGS_BOT_B
TG_SESSION_STRINGS_BOT_C
```

然后配置：

```yaml
- id: sign-bot-a
  enabled: true
  schedule:
    - daily:08:30
  foreach_secret_lines: TG_SESSION_STRINGS_BOT_A
  env:
    TARGET_PEER: "@example_bot_a"
    SIGN_MESSAGE: "签到"
  command: uv run python scripts/sign_message.py

- id: sign-bot-b
  enabled: true
  schedule:
    - daily:08:40
  foreach_secret_lines: TG_SESSION_STRINGS_BOT_B
  env:
    TARGET_PEER: "@example_bot_b"
    SIGN_MESSAGE: "/checkin"
  command: uv run python scripts/sign_message.py
```

---

## 6. 手动测试

进入 GitHub：

```text
Actions -> TG Orchestrator -> Run workflow
```

### 查看任务列表

```text
mode = list
```

### 查看当前应该运行哪些任务

```text
mode = due
```

### 运行当前到期任务

```text
mode = run-due
```

### 运行指定任务

```text
mode = run
task_id = tg-example
```

如果你新建了任务，例如 `sign-example-bot`，就填：

```text
task_id = sign-example-bot
```

---

## 7. 目前仓库状态

当前仓库已经有编排器和示例任务：

```text
tg/runner.py
tg/tasks.yml
tg/scripts/example_task.py
```

但是 `example_task.py` 只是 smoke test，不会真的连接 Telegram。

真正执行 Telegram 签到时，需要再添加实际脚本，例如：

```text
tg/scripts/sign_message.py
tg/scripts/sign_click_button.py
```

这些脚本应该读取环境变量：

```text
TG_SESSION_STRING
TG_PROXY
TARGET_PEER
SIGN_MESSAGE
CLICK_BUTTON_TEXT
FORWARD_TO
```

---

## 8. 推荐最终结构

```text
tg/
  pyproject.toml
  runner.py
  tasks.yml
  CONFIGURE.md
  scripts/
    example_task.py
    sign_message.py
    sign_click_button.py
```

---

## 9. 安全提醒

请只自动化你自己的账号和你有权限使用的 bot、群组或频道。

不要用这个项目做：

- 批量骚扰
- 绕过平台风控
- 未授权抓取/转发私密内容
- 违反目标 bot、群组、频道规则的行为

如果某个 bot 明确禁止自动化，请不要对它使用自动签到。
