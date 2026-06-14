# 使用 tg-signer 登录与运行

本项目优先使用 `tg-signer` 作为 Telegram 用户账号自动化工具。

和直接写 Telethon 脚本不同，`tg-signer login` 会自己处理登录流程。通常不需要你手动去 `my.telegram.org` 创建 `api_id/api_hash`。

## 1. 核心结论

```text
tg-signer login -> 本地登录账号 -> 保存 session 凭证 -> GitHub Actions 使用该凭证定时运行
```

你需要关心的是：

```text
TG_SESSION_STRING 或 .session 文件
```

而不是：

```text
TG_API_ID
TG_API_HASH
```

## 2. 本地登录

在你自己的电脑上运行：

```bash
cd tg
uv sync
uv run tg-signer login
```

它会提示你输入：

```text
手机号
Telegram 验证码
二步验证密码（如果开启了）
```

登录后，`tg-signer` 会获取最近聊天列表。你需要确认想签到的 bot / 群 / 频道出现在列表中。

## 3. 凭证保存在哪里

`tg-signer` 默认会把 session 保存成文件。

常见形式：

```text
my_account.session
```

如果你使用 `-a` 指定账号名：

```bash
uv run tg-signer -a account_a login
```

则会生成类似：

```text
account_a.session
```

注意：

```text
.session 文件就是登录态，等同于账号凭证。
不要提交到 GitHub。
不要发给别人。
不要发给 ChatGPT。
```

## 4. GitHub Actions 怎么使用凭证

GitHub Actions 是临时环境，不能依赖本地 `.session` 文件长期存在。

推荐两种方式。

### 方式 A：使用 `TG_SESSION_STRING`

`tg-signer` 支持通过环境变量读取 session string：

```text
TG_SESSION_STRING
```

适合放进 GitHub Secrets。

多账号时，可以使用：

```text
TG_SESSION_STRINGS
```

一行一个账号：

```text
session_string_for_account_1
session_string_for_account_2
session_string_for_account_3
```

### 方式 B：把 `.session` 文件 base64 后放进 Secret

如果你只有 `.session` 文件，没有 session string，可以本地转换成 base64：

```bash
base64 -w 0 account_a.session
```

macOS 如果没有 `-w`：

```bash
base64 account_a.session | tr -d '\n'
```

然后保存到 GitHub Secret，例如：

```text
TG_SESSION_FILE_BASE64
```

workflow 运行时再还原：

```bash
printf '%s' "$TG_SESSION_FILE_BASE64" | base64 -d > tg/account_a.session
```

这种方式可行，但不如 `TG_SESSION_STRING` 方便多账号管理。

## 5. 多账号建议

多账号建议使用账号名区分：

```bash
uv run tg-signer -a account_a login
uv run tg-signer -a account_b login
uv run tg-signer -a account_c login
```

GitHub Actions 中，多账号可以使用 `TG_SESSION_STRINGS`：

```text
account_a_session_string
account_b_session_string
account_c_session_string
```

或者使用多个 base64 Secret：

```text
TG_SESSION_ACCOUNT_A_BASE64
TG_SESSION_ACCOUNT_B_BASE64
TG_SESSION_ACCOUNT_C_BASE64
```

## 6. 配置签到任务

本地先交互式配置：

```bash
uv run tg-signer run my_sign
```

按照提示填写：

```text
Chat ID 或 @username
发送什么文本
点击哪个按钮
是否删除消息
每日签到时间
```

配置完成后，可以运行一次：

```bash
uv run tg-signer run-once my_sign
```

也可以正式运行：

```bash
uv run tg-signer run my_sign
```

## 7. GitHub Actions 中运行 tg-signer

如果使用 `TG_SESSION_STRING`：

```bash
TG_SESSION_STRING="$SESSION" uv run tg-signer --in-memory -a account_1 -w .signer run-once my_sign
```

如果使用 `.session` 文件：

```bash
uv run tg-signer -a account_a -w .signer run-once my_sign
```

## 8. 需要提交哪些文件

可以提交：

```text
tg/pyproject.toml
tg/tasks.yml
tg/TG_SIGNER.md
tg/.signer/ 里的非敏感配置文件
```

不要提交：

```text
*.session
*.session-journal
TG_SESSION_STRING
手机号
验证码
```

## 9. 和 BotFather token 的区别

`@BotFather` 给的是 bot token。

它可以用于：

```text
- 发通知
- 发运行摘要
- 控制你自己创建的 bot
```

它不能用于：

```text
- 让你的用户账号去其他 bot 签到
- 让你的用户账号点击按钮
- 替代 tg-signer login 得到的 session
```

用户账号签到需要的是用户账号 session，而不是 BotFather token。
