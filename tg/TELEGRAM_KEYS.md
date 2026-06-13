# Telegram 参数获取指南

本文档说明本项目会用到哪些 Telegram 参数，以及如何获取它们。

## 1. 参数分为两类

### 敏感参数：放 GitHub Secrets

这些不要提交到仓库：

```text
TG_API_ID
TG_API_HASH
TG_SESSION_STRINGS
TG_FORWARD_BOT_TOKEN
TG_FORWARD_CHAT_ID
TG_PROXY
```

配置位置：

```text
GitHub 仓库 -> Settings -> Secrets and variables -> Actions -> New repository secret
```

### 明文配置：放 `tg/signins.yml`

这些可以提交到仓库，方便编辑：

```text
peer                 # 和谁对话，例如 @example_bot
actions              # 发什么消息、点什么按钮
forward.when         # 什么情况下转发/通知
forward.include      # 命中哪些文本才转发
forward.exclude      # 排除哪些文本
schedule             # 写在 tg/tasks.yml 里
```

---

## 2. 获取 `TG_API_ID` 和 `TG_API_HASH`

`TG_API_ID` 和 `TG_API_HASH` 是用户账号登录 Telegram API 所需的客户端参数。

步骤：

1. 先用官方 Telegram 客户端注册/登录你的账号。
2. 打开：`https://my.telegram.org`
3. 使用 Telegram 手机号登录。
4. 进入：`API development tools`
5. 创建一个 application。
6. 复制页面里的 `api_id` 和 `api_hash`。
7. 分别存入 GitHub Secrets：

```text
TG_API_ID=<你的 api_id>
TG_API_HASH=<你的 api_hash>
```

注意：

- `api_hash` 和密码一样敏感，不要发给别人。
- 不要把 `api_id/api_hash` 写进 `signins.yml`。
- 不要用 API 做刷量、骚扰、垃圾消息或绕过风控。

---

## 3. 无法创建 `api_id/api_hash` 怎么办

当前项目的“用户账号自动签到”基于 Telethon / MTProto。这个路线无法完全绕过 `api_id/api_hash`。

如果 `my.telegram.org` 无法创建 application，可以先按下面顺序排查：

1. 确认这个手机号已经能在官方 Telegram 客户端正常登录和收发消息。
2. 换浏览器或无痕模式，清理 `my.telegram.org` 的 cookie 后重试。
3. 不要使用数据中心代理或频繁切换 IP 的代理；换成更稳定的住宅网络/手机网络重试。
4. application 表单尽量只填简单英文和数字，例如：

```text
App title: hxworkflow
Short name: hxworkflow
Platform: Desktop
URL: 留空
Description: personal automation
```

5. 如果账号是刚注册的，等待一段时间后再试。
6. 如果页面已经存在一个 application，直接使用已有的 `api_id/api_hash`；Telegram 官方限制每个号码只能关联一个 `api_id`。
7. 如果多个临时账号都要跑，不需要每个账号都创建 API。`api_id/api_hash` 是应用参数，可以配合同一个脚本登录多个手机号；多账号只需要多个 session string。

### 可以不用 `api_id/api_hash` 吗？

分情况：

| 场景 | 是否可以不用 |
|---|---:|
| 用户账号给 bot/群发消息签到 | 不可以 |
| 用户账号点击 bot 按钮签到 | 不可以 |
| 用户账号读取返回消息并转发 | 不可以 |
| 只用通知 bot 给自己发摘要 | 可以，但这不能替代用户账号签到 |

原因：Bot API 只能控制你创建的 bot，不能替代你的用户账号去和其他 bot 对话签到；而 Telethon 登录用户账号需要 `api_id/api_hash`。

### 不推荐的做法

不要把网上别人公开的 `api_id/api_hash` 填进来。公开 API 参数可能被限制、风控或导致账号异常。

---

## 4. 获取 `TG_SESSION_STRINGS`

`TG_SESSION_STRINGS` 是真正代表“用户账号登录态”的参数。

本项目提供了本地生成脚本：

```text
tg/scripts/gen_session.py
```

### 本地生成单个账号 session

在你的电脑上运行：

```bash
cd tg
uv sync
uv run python scripts/gen_session.py
```

脚本会要求输入：

```text
TG_API_ID
TG_API_HASH
手机号
Telegram 登录验证码
二步验证密码（如果你的账号开启了）
```

运行成功后，会输出一长串 session string。

把它复制到 GitHub Secret：

```text
TG_SESSION_STRINGS
```

### 多账号写法

多账号时，一行一个 session：

```text
session_string_for_account_1
session_string_for_account_2
session_string_for_account_3
```

注意：

- 不要加逗号。
- 不要加引号。
- 不要提交到仓库。
- 不要发给 ChatGPT 或别人。
- 某个临时账号失效时，删除对应那一行即可。

---

## 5. 获取 `TG_FORWARD_BOT_TOKEN`

如果你使用 `forward.mode: notify`，脚本会用 Telegram Bot API 给你发送一条摘要通知。

Bot Token 获取方式：

1. 在 Telegram 搜索并打开 `@BotFather`。
2. 发送 `/newbot`。
3. 按提示设置 bot 名称和 username。
4. BotFather 会返回一个 token。
5. 把 token 存入 GitHub Secret：

```text
TG_FORWARD_BOT_TOKEN=<BotFather 给你的 token>
```

注意：

- Bot token 等同于 bot 密码。
- 如果泄露，请在 `@BotFather` 里 revoke / regenerate。

---

## 6. 获取 `TG_FORWARD_CHAT_ID`

`TG_FORWARD_CHAT_ID` 表示通知要发到哪里。

### 私聊通知

1. 打开你刚创建的通知 bot。
2. 给它发送任意消息，例如 `test`。
3. 在浏览器打开：

```text
https://api.telegram.org/bot<你的 bot token>/getUpdates
```

4. 找到返回 JSON 里的：

```json
"chat": {
  "id": 123456789
}
```

5. 把这个数字存入 GitHub Secret：

```text
TG_FORWARD_CHAT_ID=123456789
```

### 群组通知

1. 把通知 bot 拉进群。
2. 在群里发一条测试消息。
3. 打开：

```text
https://api.telegram.org/bot<你的 bot token>/getUpdates
```

4. 找到群消息里的 `chat.id`。
5. 群组 chat id 通常是负数，例如：

```text
-123456789
```

### 频道通知

1. 把通知 bot 加为频道管理员。
2. 在频道发一条测试消息。
3. 用 `getUpdates` 找到 `chat.id`。
4. 频道/超级群 id 常见形式是：

```text
-1001234567890
```

---

## 7. 获取 `peer`：和谁对话进行签到

`peer` 写在 `tg/signins.yml` 中，例如：

```yaml
peer: "@example_bot"
```

常见写法：

| 对象 | 写法 | 说明 |
|---|---|---|
| 公开 bot | `@example_bot` | 最推荐 |
| 公开群组 | `@example_group` | 账号必须已加入群组 |
| 公开频道 | `@example_channel` | 账号必须能访问频道 |
| 私有群/频道 | `-1001234567890` | 需要你自己拿到 chat id |

建议优先使用 `@username`，最简单、最稳定。

---

## 8. 配置代理 `TG_PROXY`

如果 GitHub Actions 访问 Telegram 不稳定，可以配置代理 Secret：

```text
TG_PROXY=socks5://user:pass@host:port
```

也可以不带用户名密码：

```text
TG_PROXY=socks5://host:port
```

支持：

```text
socks5://
socks4://
http://
```

---

## 9. 最小可用配置清单

至少需要：

```text
TG_API_ID
TG_API_HASH
TG_SESSION_STRINGS
```

如果使用 `forward.mode: notify`，还需要：

```text
TG_FORWARD_BOT_TOKEN
TG_FORWARD_CHAT_ID
```

如果使用 `forward.mode: user_forward`，则不需要 bot token，但当前用户账号必须能访问 `forward.to_peer`。
