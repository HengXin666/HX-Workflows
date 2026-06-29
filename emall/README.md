# emall

共享邮件发送组件。

当前提供 Telegram 签到 HTML 报告发送：

```bash
uv run python send_tg_report.py ../tg/reports/tg_signins.json
```

需要配置以下 Secrets / 环境变量：

```text
SMTP_HOST
SMTP_PORT
SMTP_USERNAME
SMTP_PASSWORD
SMTP_FROM
SMTP_TO
SMTP_USE_SSL
SMTP_STARTTLS
```

其中必填：

```text
SMTP_HOST
SMTP_USERNAME
SMTP_PASSWORD
SMTP_TO
```
