# TG 本地 Web 配置器

本目录的 `web/` 是一个仅本地部署的 React + TypeScript 配置器，用于可视化生成 Telegram 多账号工作流配置。

支持内容：

- 区分主账号和小号。
- 扫码后读取账号名称和头像，并显示在 Web 页面。
- 主页可配置 Telegram 代理，默认 `http://127.0.0.1:2334`。
- 二维码登录在 Web 弹窗中显示。
- 开启 Telegram 两步验证的账号可在扫码弹窗中继续输入云密码，密码只保留在当前登录任务内存中。
- 主账号用于群聊或频道发邀请链接、任务入口等内容。
- 小号池用于打开 bot 邀请入口、发送签到文本、点击按钮、等待回执。
- 使用 React Flow 编排所有小号都会执行的任务图。
- 可视化生成 `config/signins.yml`、`config/tasks.yml` 和主账号本地命令。
- 通过本地 API 扫码导入账号，凭证写入 `tg/sessions/`，该目录已被 git 忽略。
- GitHub 管理页面可通过本机 `gh` 同步凭证、查看并管理远程工作流。

## 启动

一键启动本地 API 和 Web 页面：

```bash
cd components/HX-Workflows/tg
./run_web.sh
```

浏览器打开：

```text
http://127.0.0.1:5173
```

可选端口配置：

```bash
TG_WEB_PORT=5174 TG_WEB_API_PORT=8766 ./run_web.sh
```

也可以手动分开启动。

终端 1：启动本地 API。

```bash
uv run python scripts/local_web_api.py
```

终端 2：启动 Web 页面。

```bash
cd web
npm install
npm run dev
```

## 账号导入

Web 页面包含以下主要页面：

```text
主页配置     配置主账号、小号池，扫码导入账号并显示头像和名称
对话调试     使用已导入账号查看对话并发送测试消息
编排流       使用 React Flow 编排小号统一执行的任务图
本地执行     选择保存的工作流，在本地用小号池执行
GitHub 管理  同步 Secrets，查看、启停或手动触发工作流
机器人编排   管理多个 Bot Token，并组合任意 Telegram Bot API 调用
```

Web 页面中的“扫码导入”会调用：

```text
http://127.0.0.1:8765
```

导入后的 session string 写入：

```text
tg/sessions/tg_session_strings.txt
```

这个文件会作为本地小号账号池使用，每一行是一个小号 session string。

主账号扫码导入会写入：

```text
tg/sessions/tg_main_session_string.txt
```

账号元数据写入：

```text
tg/sessions/tg_session_strings.accounts.json
tg/sessions/tg_main_session_string.accounts.json
```

保存的 Web 工作流写入：

```text
tg/sessions/web_workflows.json
```

如果账号开启了两步验证，扫码后直接在弹窗输入 Telegram 云密码即可继续。密码不会写入磁盘、日志或 GitHub。

命令行导入仍可作为备用方式：

```bash
uv run python scripts/enroll_accounts.py
```

## GitHub 工作流与凭证管理

先在本机安装并登录 GitHub CLI：

```bash
gh auth login
```

打开 Web 左侧“GitHub 管理”，确认目标仓库为 `owner/repo`。页面支持：

- 将小号池同步为 `TG_SESSION_STRINGS`；新增账号后再次同步会覆盖远程同名 Secret。
- 按需同步 `TG_API_ID`、`TG_API_HASH` 和主账号 `TG_MAIN_SESSION_STRING`。
- 查看、启用、停用和手动触发远程工作流。

Secret 值通过标准输入传给 `gh secret set`，不会出现在命令参数、API 响应或前端状态中。远程操作必须由页面显式确认；启动本地 Web 不会自动上传凭证。

### 保活期限

- Telegram session 没有可承诺的固定有效期。退出所有设备、撤销会话、账号安全状态变化或服务端返回 session expired/revoked 后，需要重新登录并同步。
- GitHub 公开仓库连续 60 天没有仓库活动时，计划工作流可能被自动停用。应在 GitHub 管理页确认工作流状态为 `active`；私有仓库不适用这条公开仓库的 60 天规则。

## 机器人编排

“机器人编排”使用 BotFather Token，而不是用户 session。添加机器人时会调用 `getMe` 验证 Token，成功后 Token 写入权限为 `0600` 的 `sessions/telegram_bots.json`，API 和页面只返回掩码。

每个步骤可选择默认机器人或单独指定机器人，并填写任意 Bot API method 与 JSON 参数。常用发送、编辑、转发、投票、群管理和 Webhook 方法提供快速模板；其他方法直接填写 method 即可。使用 `{{step.步骤ID.字段路径}}` 可以把上游返回值传给后续步骤，例如 `{{step.send.message_id}}`。

JSON 模式支持 Telegram `file_id` 或公网 URL 媒体。需要上传本地二进制文件的 multipart 请求不在本地编排器当前支持范围内。

## 持续请求与资源链接去重

在“小号执行流”中按下面的顺序连接节点：

```text
任务定义 -> 打开对话 -> 持续请求 -> 资源池转存（可选） -> 资源链接去重
```

“持续请求”支持两种请求方式：向当前机器人发送文本/命令，或按文字点击机器人按钮。每个选中账号都会独立执行配置的重复次数；总请求数为“账号数 × 重复次数”。本地执行页可明确选择“全部已导入账号”或“仅小号池”。节点接收每轮请求之后的新入站消息、机器人原地编辑的按钮消息以及按钮直接返回的 URL，并从消息正文、文本链接实体和按钮 URL 中提取 HTTP(S) 或 Telegram 链接。

主要边界如下：

- 重复次数为 `1..1000`。
- 固定间隔为 `0.5..3600` 秒，最后一轮结束后不再额外等待。
- 回执等待为 `0..300` 秒，每轮最多读取 `1..100` 条消息。
- 达到失败阈值时当前账号停止该节点；其他账号仍由现有账号执行器继续处理。

本地运行器有意按账号顺序执行，不启用多账号并行。并行请求会提高 Telegram `FloodWait`、机器人侧限流和账号关联封禁风险，无法承诺“不会封号”。本地页面同一时间只允许一个活动任务；重复启动会被 API 拒绝。运行中可点击“终止任务”，后端会终止完整执行器进程组并把状态置为 `cancelled`，页面刷新后也会恢复该任务并继续提供终止入口。终止只能阻止后续轮次，已经发送给 Telegram 的请求无法撤回。

“资源链接去重”默认把跨轮次、跨账号的唯一 URL 写入：

```text
sessions/resource_catalog.sqlite3
sessions/resource_links.json
```

SQLite 保存首次来源和每次命中的账号序号、轮次、消息 ID；JSON 是完整可读清单。本地执行页同时显示本次运行发现的唯一链接。“资源池转存”消费上游回执消息，对图片和视频做媒体指纹去重，并把转发后的资源池消息链接追加到 `resource_records`；需要把群消息链接也纳入目录时，应把资源池连接在“资源链接去重”之前。

### 安全索引资源预览

机器人用预览媒体和“解锁”按钮返回资源时，可连接：

```text
任务定义 -> 打开对话 -> 持续请求 -> 资源预览索引
```

`preview_catalog` 只读取按钮的 callback 元数据，默认只接受以 `unlock:` 开头的值；若显式修改索引前缀，也只读取匹配该前缀的元数据。节点将前缀后的内容作为稳定资源键，用于跨轮次、跨账号去重；它不会点击按钮、不会发送 callback query，也不会执行解锁或消耗积分。按钮文字、摘要、图片/视频类型、媒体消息链接、稳定账号 ID 和首次/重复命中来源仅作为目录元数据保存。

默认目录写入权限为 `0600` 的文件：

```text
sessions/resource_previews.sqlite3
sessions/resource_previews.json
```

每个预览节点都可以配置独立的 JSON 导出路径。资源预览页会列出默认文件、已保存工作流配置的导出文件，以及 `sessions` 下能够识别为资源预览清单的 JSON；页面默认打开上次选择的文件，也可以通过页头的“索引文件”选择器随时切换，不会把界面固定到某一份清单。

资源预览页会使用索引记录的账号、会话和消息 ID 批量读取 Telegram 消息，在每个预览批次内直接渲染来源文本、图片和视频封面。页面只读取进入可视区域附近的批次；图片和视频先使用最长边约 360px 的低清缩略图，同一个 Telegram 媒体 ID 在不同账号、消息和页面之间共用规范化 URL 及内存缓存。点击图片才打开原图，点击视频播放后才请求原视频。对话调试页复用同一套媒体组件，文件显示名称和大小并保留下载入口。原始 Telegram 消息链接仍收纳在折叠区内，作为复制、外部打开和旧索引兼容入口。

“站内定位”使用索引保存的稳定 `account_id`、来源会话和消息 ID 打开对话调试页，并围绕目标消息读取历史和高亮。旧索引没有 `account_id`、账号已移除或消息已删除时不会伪装成精准定位；这类记录只能使用保留的 Telegram 外部链接。重新运行预览索引后，新记录会带上稳定账号 ID 和媒体类型。

不要把 `callback_data` 当作 URL、命令或待执行载荷。JSON 导出仍可能包含私有消息位置和业务资源键，只应保存在受访问控制的本地目录或 CI 缓存中，不应发布为公开 artifact。

“预览入口”、“链接入口”和“媒体入口”默认选择“上游节点输出”，不需要手写内部变量。编译器会沿连线自动选择：解析节点的链接输出、持续请求的资源记录或回执消息，以及资源池转存后生成的群消息链接。只有需要接入自定义 action 变量时才切换到“自定义变量”。当前 `GetLoliAV` 使用消息模式持续发送 `♻️ 随机`，并自动把每轮 `resource_responses` 交给资源预览索引。

资源池可选择“自动创建并同步账号”：指定资源组名称和群主账号后，点击“创建并同步”，所选账号会创建一个超级群并成为群主；系统随后逐个检查全部已导入账号，仅让尚未入群的账号通过私有邀请链接加入。同步在后台运行，失败账号会单独显示并可再次同步。资源组状态保存在权限为 `0600` 的 `sessions/resource_groups.json`，邀请链接同时写入当前资源池节点供本地执行使用。该按钮会产生真实 Telegram 建群/入群操作，普通编译和测试不会触发。

随机返回接口只能在指定次数内逐步扩大覆盖面。除非机器人同时提供资源总数或遍历完成信号，否则“连续请求 + 去重”不能证明已经获得服务端的绝对全部资源。

## 使用生成结果

页面“一键生成”区域会输出：

```text
signins.yml
tasks.yml
GitHub Actions workflow
主账号命令
```

复制对应内容到 `tg/config/signins.yml` 和 `tg/config/tasks.yml` 后即可用现有 runner 执行。

如果要应用到 GitHub Actions：

1. 把 `signins.yml` 内容写入 `components/HX-Workflows/tg/config/signins.yml`。
2. 把 `tasks.yml` 内容写入 `components/HX-Workflows/tg/config/tasks.yml`。
3. 把 `.github/workflows/tg.yml` 内容写入仓库根目录的 `.github/workflows/tg.yml`。
4. 在仓库 Secrets 配置 `TG_SESSION_STRINGS`，以及需要的 `TG_PROXY`、SMTP 邮件变量。

资源状态文件不提交 Git。本地运行会持续复用：

```text
components/HX-Workflows/tg/sessions/resource_pool_seen.sqlite3
components/HX-Workflows/tg/sessions/resource_pool_seen.sqlite3-*
components/HX-Workflows/tg/sessions/resource_catalog.sqlite3
components/HX-Workflows/tg/sessions/resource_catalog.sqlite3-*
components/HX-Workflows/tg/sessions/resource_links.json
components/HX-Workflows/tg/sessions/resource_previews.sqlite3
components/HX-Workflows/tg/sessions/resource_previews.sqlite3-*
components/HX-Workflows/tg/sessions/resource_previews.json
```

内置 GitHub Actions 和生成的工作流示例使用 `actions/cache` 保存上述资源状态，因此跨运行仍可累计去重结果。缓存内容可能包含私有地址、消息位置或资源键；仓库与 Actions 缓存必须受访问控制，不应再把这些文件上传为公开 artifact 或提交到 Git。

本地运行小号签到时，页面会生成下面这种命令：

```bash
TG_SESSION_STRINGS="$(cat sessions/tg_session_strings.txt)" uv run python scripts/sign_from_config.py --config config/signins.yml run-enabled --mail
```

敏感凭证不要提交到仓库。
