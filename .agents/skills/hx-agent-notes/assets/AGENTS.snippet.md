## Agent Notes 红线

改声明前先读其顶部多行注释引用的 `.agents/notes/...md`. 每条决策只保留一篇 note, `## Code` 逐行列仓库相对精确文件路径, 每个直接父目录只选一个代表文件, 在该文件的声明顶部反向引用该 note. 禁止 glob, 花括号和文件头占位引用

代码或 note 改动必须在同一 diff 配对, 同目录兄弟代码也算该决策的代码端. 单边改动必须 review, 不可用无关 note 或豁免文本放行. 过时事实就地改写, 废弃 note 直接删除并修复所有引用. `## Alternatives considered` 必须包含什么都不做 / 复用现有, 每项先给最强理由再否决

完成改动运行 `uv run scripts/redlines/agent_notes.py --diff`, 全量调研用 `--all`, 提交前用 `--staged`. AST 必须支持 cpp/ts/tsx/js/mjs/go/py/rs, 缺依赖或解析失败即失败. 规则见 `.agents/notes/AGENTS.md`

受保护的非 AST 资源改动报告 resource-review, 由人工核对决策和引用

CI 工作流始终容错完成, 有问题时在对应代码片段评论并保存完整 JSON 诊断. CI 成功只表示流程完成, 双链是否有效以诊断内容为准. 本地校验仍用非零退出码暴露问题

所有门禁先保存本次完整诊断文件, 包括成功结果和工具故障. 对外按本次问题总数决定展示: 超过 10 条只列数量与类型统计, 不列逐项详情; 不超过 10 条才可展开全部详情. 错误, 警告和待审核合计计数, 不按类型拆开绕过阈值. 终端, hook, CI 摘要与机器人评论遵守同一规则, 附完整报告路径或 artifact 链接. 子进程 stdout/stderr 与复现命令写入文件, 不直接透传; 报告写入失败按工具错误失败, 保持原校验退出码语义

baseline-review, resource-review, policy-review 和 migration-review 为人工审核提示, 始终保留在完整 JSON 与数量/类型摘要中, 不单独阻断本地提交. AST 错误, 路径或锚点错误, 单边 diff, 无主源码/目录和未知审核规则继续阻断. JSON 的 ok 仍表示没有任何诊断, exit 0 仅表示没有本地阻断项, 待人工审核不称为双链已有效
