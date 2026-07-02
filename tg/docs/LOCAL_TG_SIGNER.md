# 本地测试一个账号

适用场景：你没有 `TG_API_ID` / `TG_API_HASH`，希望先用项目已有的 `tg-signer` 路径测试一个 Telegram 账号。

如果你需要扫码登录，不要用 `tg-signer login`，请看 [`LOCAL_QR_LOGIN.md`](./LOCAL_QR_LOGIN.md)。

## 1. 登录一个账号

```bash
cd components/HX-Workflows/tg
uv sync
uv run tg-signer -a account_1 login
```

`tg-signer` 会提示手机号、验证码、二步验证密码。登录成功后会生成：

```text
sessions/account_1.session
.signer/account_1.session_string
```

这两个都是敏感登录态，不要提交。

## 2. 配置一个签到任务

交互式配置：

```bash
uv run tg-signer -a account_1 run my_sign
```

按提示填写目标 bot、发送文本、按钮、运行时间等。

如果只想先测试账号能不能发消息，可以用：

```bash
uv run tg-signer -a account_1 send-text "@SpamBot" "/start"
```

## 3. 运行一次测试

```bash
uv run tg-signer -a account_1 run-once my_sign
```

如果这个能跑通，说明单账号登录和签到配置都没问题。

## 4. 扩展到多个账号

逐个登录：

```bash
uv run tg-signer -a account_1 login
uv run tg-signer -a account_2 login
uv run tg-signer -a account_3 login
```

用同一套配置运行多个账号：

```bash
uv run tg-signer multi-run -a account_1 -a account_2 -a account_3 my_sign
```

## 5. 后续升级到工作流

本地登录后，`tg-signer` 会在 `.signer/` 下保存每个账号的 session string，例如：

```text
.signer/account_1.session_string
.signer/account_2.session_string
```

把这些文件内容逐行放入 GitHub Secret：

```text
TG_SESSION_STRINGS
```

之后 GitHub Actions 可以用 `TG_SESSION_STRING` / `TG_SESSION_STRINGS` 在临时环境中运行，不依赖本地 `.session` 文件。
