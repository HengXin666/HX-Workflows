# Agent Note: HX-Workflows 接入 Agent Notes v2

Status: implemented

Decision-ID: repository-agent-notes-v2-adoption

## Code

- `scripts/redlines/agent_notes.py`
- `bilibili-api/bilibili_api/api.py`
- `bilibili-api/tests/test_core.py`
- `scripts/check_arch.py`
- `tg/runner.py`
- `tg/scripts/qr_login.py`
- `tg/tests/test_runner.py`
- `tg/web/vite.config.ts`
- `tg/web/src/App.tsx`
- `tg/web/src/components/ui.tsx`
- `tg/web/src/lib/api.ts`

## Problem

旧检查依赖宽松路径和任意 note 改动, 不能证明代码与决策互相对应. 仓库需要可独立运行的本地红线和能定位问题的 CI 评论

## Decision

本仓使用 version 2 配置和本地 vendored 实现, 每个受保护源码目录有精确代表文件及 AST 声明锚点. 上面的存量目录登记迁移边界, 保持原有行为和公开契约, 后续业务变更优先更新该业务已有决策, 新决策单独记录. 这些登记只约束变更流程, 不代替业务正确性审核

本地 diff, all 和 staged 检查使用同一严格扫描器. GitHub Diff 和 Full 保存完整诊断, Report 在相应代码片段评论, 依赖或 API 故障也保留诊断且工作流容错完成. CI 成功仅表示报告流程完成

配置保留原有受保护源码范围并纳入门禁实现, 根目录源码按精确文件登记. 不通过无关 note, 空函数或 NOTE-EXEMPT 放行. 无本仓有效代码约束的记录直接退役, 有指导价值的理由先迁入存活决策, 修复入站链接, 冻结归档保持原样

## Alternatives considered

- 什么都不做 / 复用旧检查: 迁移成本最低, 但缺少精确路径和 AST 双向约束, 无关 note 仍能掩盖源码变更
- 只修改配置版本: 改动最少, 但旧 note 和锚点无法构成 v2 关系图, 工具错误会遮住具体问题
- 另建参考记录目录: 能保留没有源码落点的旧材料, 但会分散决策与维护入口, 因此统一使用现有 notes 树, 有效理由迁入存活决策, 历史由 Git 保留
- 为每个目录发明业务决策: 能填满覆盖清单, 但会伪造实现理由, 因此只登记真实迁移流程约束

## Consequences

新鲜 checkout 可使用本仓门禁, 本地校验对无效双链返回非零, CI 在对应片段报告问题. 存量业务理由保持原意, 迁移边界仍需人工审核. 多份 vendored 实现更新时需要同时验证版本和内容一致性

## Verification

逐仓检查格式, 精确路径, AST 归属, 每目录唯一锚点和完整双链. 精确 Git 树检查用于验证 checkout 内容, v1 比较基线仍保留 migration-review 和 policy-review, 不把首次迁移误称为已独立审核
