# Agent Note: 镜像同步邮件

Status: implemented

Decision-ID: mirror-email-report

## Code

- `mirror/mirror/notify.py`
- `mirror/tests/test_mirror.py`

## Problem

镜像同步每天在后台跑, 公开仓库的 Summary 和附件里私有仓库名都被替换成哈希, 用户看不出配置了哪些仓库、当天哪些真正同步了、哪里失败、各平台的镜像在哪里

## Decision

每次运行结束后发一封 HTML 邮件到用户自己的邮箱, 内容分四块: 目标平台 (存放的组织 / 用户链接与状态)、失败与提醒、今日同步 (只列真正有变化的仓库)、已配置的仓库 (每个仓库在各平台的镜像链接). 邮件用未脱敏的报告, 因为只发到私人邮箱; 日志只打印收件地址个数

"今日同步" 的判定来自 `git push --porcelain`: 只有新增、更新、强制更新或删除的引用计数, `=` (已是最新) 不算; 新建仓库、改可见性、改默认分支也算有变化

复用 emall 的 `SMTP_*` secrets, 新增可选的 `MIRROR_MAIL_TO` 覆盖收件人; `notify.email` 支持 always / failure / never. 没配 SMTP 只跳过; 配了但发送失败让工作流退出 1. 样式取 HX-UI 2 (`next/src/styles/theme.css`) 亮色令牌, 换成十六进制并内联, 用表格布局兼容邮件客户端

## Alternatives considered

- 什么都不做 / 复用现有: 只看 Actions Summary 零开发量, 但私有仓库名被脱敏, 也没有各平台镜像链接, 满足不了需求
- 直接调用 emall 的 `send_tg_report.py`: 能复用发信代码, 但它的报告结构和样式专为 Telegram 签到设计, 跨子项目导入还会把两个 uv 项目耦合起来, 因此只沿用同名环境变量
- 用第三方 Action (如 dawidd6/action-send-mail) 发信: 不用写 SMTP 代码, 但 HTML 要在工作流里拼, 私有仓库名得写进文件再交给另一个步骤, 增加泄露面, 也无法单测
- 用推送前后 `ls-remote` 对比判断有无更新: 结果一样, 但每个仓库每个平台多一次网络请求, porcelain 输出已经够用

## Consequences

收件邮箱会收到私有仓库名; 改收件人只能改 secret. 每天都发会有一封邮件, 不想要可改 `notify.email: failure`. 邮件客户端不支持的 CSS (阴影、圆角) 会降级为直角无阴影, 不影响内容
