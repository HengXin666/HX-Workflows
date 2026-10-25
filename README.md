# HX-Workflows

## 子项目

- [tg](./tg/)
- [bilibili-api](./bilibili-api/)
- [emall](./emall/)
- [hygiene](./hygiene/)

## GitHub Actions

- [TG Orchestrator](./.github/workflows/tg-orchestrator.yml)
- [Bilibili Token Refresh](./.github/workflows/bilibili-token-refresh.yml)
- [仓库卫生扫描](./.github/workflows/hygiene-scan.yml)
- [仓库卫生修复](./.github/workflows/hygiene-apply.yml)

## 仓库卫生

[![仓库卫生](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FHengXin666%2FHX-Workflows%2Fmain%2Fhygiene%2Fbadges%2Fsummary.json)](https://github.com/HengXin666/HX-Workflows/issues?q=is%3Aissue+is%3Aopen+label%3Ahygiene-report)
[![可删分支](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FHengXin666%2FHX-Workflows%2Fmain%2Fhygiene%2Fbadges%2Fbranches.json)](https://github.com/HengXin666/HX-Workflows/issues?q=is%3Aissue+is%3Aopen+label%3Ahygiene-report)
[![过期 Skill](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FHengXin666%2FHX-Workflows%2Fmain%2Fhygiene%2Fbadges%2Fskills.json)](https://github.com/HengXin666/HX-Workflows/issues?q=is%3Aissue+is%3Aopen+label%3Ahygiene-report)

[仓库卫生](./hygiene/README.md) 手动扫描已合入的多余分支和过期 Skill. 状态卡片源文件:

- [summary.svg](./hygiene/badges/summary.svg)
- [branches.svg](./hygiene/badges/branches.svg)
- [skills.svg](./hygiene/badges/skills.svg)

## 文档

- [TG 任务编排器](./tg/README.md)
- [tg-signer](./tg/docs/TG_SIGNER.md)
- [本地 tg-signer](./tg/docs/LOCAL_TG_SIGNER.md)
- [本地二维码登录](./tg/docs/LOCAL_QR_LOGIN.md)
- [本地 Web](./tg/docs/LOCAL_WEB.md)
- [Telegram 参数](./tg/docs/TELEGRAM_KEYS.md)
- [TG 配置](./tg/docs/CONFIGURE.md)
- [Bilibili API](./bilibili-api/README.md)
- [邮件发送组件](./emall/README.md)
- [仓库卫生](./hygiene/README.md)

## 配置入口

- [TG 任务配置](./tg/config/tasks.yml)
- [TG 任务配置示例](./tg/config/tasks.example.yml)
- [TG 签到配置](./tg/config/signins.yml)
- [仓库卫生配置](./hygiene/config.yml)

## 许可证

- [LICENSE](./LICENSE)
