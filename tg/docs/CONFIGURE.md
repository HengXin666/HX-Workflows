# TG 签到配置指南

本项目现在把配置分成两层：

```text
tg/config/tasks.yml    # 负责“什么时候运行哪个 signins job”
tg/config/signins.yml  # 负责“和谁对话、发什么、点什么、转发什么”
```

敏感参数放 GitHub Secrets，获取方式请看：[`TELEGRAM_KEYS.md`](./TELEGRAM_KEYS.md)。

---

## 1. 哪些内容写进明文配置

可以写进 `tg/config/signins.yml` 的内容：

```text
peer                 # 和谁对话，例如 @example_bot
actions              # 发送消息、等待、点击按钮
collect              # 读取最近几条返回消息
forward.mode         # notify 或 user_forward
forward.when         # 什么时候转发/通知
forward.include      # 命中哪些内容才转发
forward.exclude      # 排除哪些内容
```

不要写进 `tg/config/signins.yml` 的内容：

```text
TG_API_ID
TG_API_HASH
TG_SESSION_STRINGS
TG_FORWARD_BOT_TOKEN
验证码
手机号
```

---

## 2. 最小签到配置

例如：每天让所有账号给 `@example_bot` 发送 `签到`。

### 第一步：配置 `tg/config/signins.yml`

```yaml
jobs:
  - id: my-sign
    enabled: true
    peer: "@example_bot"
    accounts_secret: TG_SESSION_STRINGS
    actions:
      - type: send
        text: "签到"
      - type: wait
        seconds: 5
    collect:
      last_messages: 3
    forward:
      enabled: true
      mode: notify
      when:
        - failure
        - matched
      include:
        contains:
          - "签到成功"
          - "已签到"
          - "积分"
        regex: []
      exclude:
        contains:
          - "广告"
        regex: []
```

含义：

```text
每个账号 -> 找到 @example_bot -> 发送 “签到” -> 等 5 秒 -> 读取最近 3 条消息 -> 根据过滤规则决定是否通知/转发
```

### 第二步：配置 `tg/config/tasks.yml`

```yaml
- id: tg-sign-my-sign
  enabled: true
  schedule:
    - daily:08:30
  command: uv run python scripts/sign_from_config.py --config config/signins.yml run my-sign
  timeout_minutes: 20
```

含义：

```text
每天 Asia/Tokyo 08:30 执行 config/signins.yml 里的 my-sign
```

---

## 3. 多账号怎么配置

多账号不写在 YAML 里，而是写在 GitHub Secret：

```text
TG_SESSION_STRINGS
```

一行一个账号：

```text
session_string_for_account_1
session_string_for_account_2
session_string_for_account_3
```

`config/signins.yml` 中使用：

```yaml
accounts_secret: TG_SESSION_STRINGS
```

脚本会自动逐行执行：

```text
账号 1 -> TG_SESSION_STRINGS 第 1 行
账号 2 -> TG_SESSION_STRINGS 第 2 行
账号 3 -> TG_SESSION_STRINGS 第 3 行
```

某个临时账号失效时，只需要在 GitHub Secret `TG_SESSION_STRINGS` 中删除或替换对应那一行。

---

## 4. “和谁对话进行签到”怎么写

核心字段是：

```yaml
peer: "@example_bot"
```

常见写法：

| 目标 | 写法 | 说明 |
|---|---|---|
| 公开 bot | `@example_bot` | 最推荐 |
| 公开群 | `@example_group` | 账号必须已加入 |
| 公开频道 | `@example_channel` | 账号必须可访问 |
| 私有群/频道 | `-1001234567890` | 使用数字 chat id |

---

## 5. 签到动作怎么编排

### 发送消息

```yaml
actions:
  - type: send
    text: "签到"
```

### 发送 `/checkin`

```yaml
actions:
  - type: send
    text: "/checkin"
```

### 先 `/start` 再签到

```yaml
actions:
  - type: send
    text: "/start"
  - type: wait
    seconds: 3
  - type: send
    text: "签到"
```

### 先 `/start` 再点击按钮

```yaml
actions:
  - type: send
    text: "/start"
  - type: wait
    seconds: 3
  - type: click
    text: "签到"
    search_limit: 5
```

`search_limit` 表示向前查找最近几条消息里的按钮。

---

## 6. 转发/通知不是全量，而是可配置过滤

转发配置在 `forward` 下。

### 只在失败或命中关键词时通知

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
      - "已签到"
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
返回消息包含 签到成功 / 已签到 / 积分 -> 通知
返回消息包含 广告 -> 不通知
其他普通消息 -> 不通知
```

### 使用正则匹配

```yaml
forward:
  include:
    contains: []
    regex:
      - "签到.*成功"
      - "积分[：: ]+\\d+"
  exclude:
    contains: []
    regex:
      - "广告|推广"
```

注意 YAML 字符串里的反斜杠需要转义，例如 `\\d+`。

---

## 7. 两种转发模式

### `notify`：发送摘要通知

```yaml
forward:
  enabled: true
  mode: notify
  when:
    - failure
    - matched
```

需要 GitHub Secrets：

```text
TG_FORWARD_BOT_TOKEN
TG_FORWARD_CHAT_ID
```

这个模式不会真正 forward 原消息，而是用你的通知 bot 发一条摘要。

适合：

```text
- 只想知道签到是否成功
- 想记录失败账号
- 不想把原消息全部搬运出去
```

### `user_forward`：真正转发匹配的原消息

```yaml
forward:
  enabled: true
  mode: user_forward
  to_peer: "@your_log_channel"
  when:
    - matched
    - failure
```

这个模式会使用当前登录的用户账号，把命中过滤规则的原消息转发到 `to_peer`。

适合：

```text
- 需要保留原始消息来源
- 需要把签到结果转发到自己的日志频道
```

注意：当前用户账号必须有权限访问 `to_peer`，并且能向它发消息/转发消息。

---

## 8. 多个机器人怎么配置

例如你有三个签到对象：

```text
@bot_a  每天 08:30 发送 签到
@bot_b  每天 08:40 发送 /checkin
@bot_c  每天 08:50 点击 签到 按钮
```

`tg/config/signins.yml`：

```yaml
jobs:
  - id: sign-bot-a
    enabled: true
    peer: "@bot_a"
    accounts_secret: TG_SESSION_STRINGS
    actions:
      - type: send
        text: "签到"
      - type: wait
        seconds: 5
    collect:
      last_messages: 3
    forward:
      enabled: true
      when: [failure, matched]
      include:
        contains: ["签到成功", "积分"]
        regex: []
      exclude:
        contains: []
        regex: []

  - id: sign-bot-b
    enabled: true
    peer: "@bot_b"
    accounts_secret: TG_SESSION_STRINGS
    actions:
      - type: send
        text: "/checkin"
      - type: wait
        seconds: 5
    collect:
      last_messages: 3
    forward:
      enabled: true
      when: [failure, matched]
      include:
        contains: ["success", "checked"]
        regex: []
      exclude:
        contains: []
        regex: []

  - id: sign-bot-c
    enabled: true
    peer: "@bot_c"
    accounts_secret: TG_SESSION_STRINGS
    actions:
      - type: send
        text: "/start"
      - type: wait
        seconds: 3
      - type: click
        text: "签到"
        search_limit: 5
      - type: wait
        seconds: 5
    collect:
      last_messages: 5
    forward:
      enabled: true
      when: [failure, matched]
      include:
        contains: ["领取成功", "已签到"]
        regex: []
      exclude:
        contains: []
        regex: []
```

`tg/config/tasks.yml`：

```yaml
- id: tg-sign-bot-a
  enabled: true
  schedule:
    - daily:08:30
  command: uv run python scripts/sign_from_config.py --config config/signins.yml run sign-bot-a

- id: tg-sign-bot-b
  enabled: true
  schedule:
    - daily:08:40
  command: uv run python scripts/sign_from_config.py --config config/signins.yml run sign-bot-b

- id: tg-sign-bot-c
  enabled: true
  schedule:
    - daily:08:50
  command: uv run python scripts/sign_from_config.py --config config/signins.yml run sign-bot-c
```

---

## 9. 手动测试

进入 GitHub：

```text
Actions -> TG Orchestrator -> Run workflow
```

查看任务：

```text
mode = list
```

运行指定任务：

```text
mode = run
task_id = tg-sign-demo-send
```

也可以先在本地测试：

```bash
cd tg
uv sync
uv run python scripts/sign_from_config.py list
uv run python scripts/sign_from_config.py --config config/signins.yml run demo-send-sign
```

---

## 10. 安全提醒

请只自动化你自己的账号，以及你有权限使用的 bot、群组或频道。

不要使用本项目进行骚扰、绕过风控、未授权抓取/转发私密内容，或违反目标 bot/群组/频道规则的行为。
