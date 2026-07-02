# 本地扫码登录多账号（可选）

`tg-signer login` 当前会提示 `Enter phone number or bot token:`，不提供二维码登录。

如果你想扫码，本项目提供 `scripts/qr_login.py`：它默认复用 `tg-signer` 内置的 Telegram API 参数，所以通常不需要你申请 `TG_API_ID` / `TG_API_HASH`。

## 1. 扫码生成多账号 session

```bash
cd components/HX-Workflows/tg
uv sync
uv run python scripts/qr_login.py --count 10
```

每个账号按提示扫码：

```text
Telegram 手机端 -> Settings -> Devices -> Link Desktop Device
```

生成结果默认写入：

```text
tg/sessions/tg_session_strings.txt
```

`sessions/` 已在 `.gitignore` 中，不要提交，也不要发送给别人。

只测试一个账号：

```bash
uv run python scripts/qr_login.py --count 1
```

持续录入账号，直到输入 `q`：

```bash
TG_PROXY="http://127.0.0.1:2334" uv run python scripts/enroll_accounts.py
```

如果你确实有自己的 API 参数，也可以显式指定：

```bash
uv run python scripts/qr_login.py --count 1 --api-id 123456 --api-hash "xxxx"
```

## 2. 本地运行签到

先在 `config/signins.yml` 中配置真实任务，例如把 `peer` 改成目标 bot，设置 `actions`，并把对应 job 的 `enabled` 改成 `true`。

本地执行：

```bash
TG_SESSION_STRINGS="$(cat sessions/tg_session_strings.txt)" \
uv run python scripts/sign_from_config.py --config config/signins.yml run demo-send-sign
```

如果只想检查配置列表：

```bash
TG_SESSION_STRINGS="$(cat sessions/tg_session_strings.txt)" \
uv run python scripts/sign_from_config.py list
```

## 3. 升级到工作流

把 `sessions/tg_session_strings.txt` 的内容逐行放到 GitHub Secret：

```text
TG_SESSION_STRINGS
```

`TG_API_ID` / `TG_API_HASH` 可以不配置；脚本会默认使用和 `tg-signer` 相同的内置参数。

然后在 `config/tasks.yml` 里启用对应任务：

```yaml
- id: tg-sign-my-sign
  enabled: true
  schedule:
    - daily:00:15
  command: uv run python scripts/sign_from_config.py --config config/signins.yml run my-sign
  timeout_minutes: 20
```

GitHub Actions 当前会在北京时间每天 `00:15` 运行所有 `enabled: true` 的任务。
