# 仓库治理

把 `HengXin666` 名下的仓库统一成同一套规矩: 合并设置、Issue 表单、PR 模板、标签和提交规范. 流程和 [仓库卫生](../hygiene/README.md) 一样: 先只读扫描, 结果写进本仓库的一张审阅单, 按仓库勾选, 再加 `apply` 标签执行.

- 扫描: [仓库治理扫描](https://github.com/HengXin666/HX-Workflows/actions/workflows/governance-scan.yml), 每周一 09:00 自动跑, 也可以手动运行
- 执行: [仓库治理修复](https://github.com/HengXin666/HX-Workflows/actions/workflows/governance-apply.yml), 手动运行或在审阅单上加 `apply` 标签. 操作者必须是 `HengXin666`

## 怎么用

1. 复用仓库卫生的 `HYGIENE_TOKEN` (classic PAT). 除了 `repo`, 还要勾选 `workflow`: 治理 PR 里有 `.github/workflows/commit-lint.yml`, 没有这个权限 GitHub 会拒绝写入.
2. 运行「仓库治理扫描」. `repo_limit` 只在日志里看结果, 不写审阅单. 扫描结果也会作为 `governance-report` 附件上传.
3. 在审阅单里勾选要治理的仓库. 每个仓库一行, 下面列出这次会改什么.
4. 给审阅单加 `apply` 标签, 或运行「仓库治理修复」并填审阅单编号 (留空用最新一张).
5. 到各仓库合并治理 PR. 公开仓库合并后再执行一次治理, 才会加上默认分支规则集.

执行时会重新读取每个仓库再决定怎么改, 不照搬扫描时的结果. 全部勾选项完成且没有剩余未勾选的仓库时, 审阅单自动关闭. 已经合规的仓库只列在折叠区, 没有勾选框.

本地只读试跑 (不写任何东西):

```sh
HYGIENE_TOKEN=$(gh auth token) PYTHONPATH=governance uv run --project governance \
  python -m governance scan --config governance/config.yml --dry-run --output governance/output
```

结果在 `governance/output/report.md` (审阅单正文) 和 `report.json`.

测试:

```sh
cd governance && PYTHONPATH=. uv run --group dev pytest -q tests
```

## 每个仓库会做什么

### 1. 合并设置 (直接改)

- 合并后自动删除分支
- 允许 squash, squash 提交标题用 PR 标题 (`PR_TITLE`), 正文用各提交明细 (`COMMIT_MESSAGES`, 不带 PR 模板里的勾选清单)
- 不关闭 merge commit 和 rebase 合并. 想只留 squash, 在 [config.yml](./config.yml) 里设 `disable_other_merge_methods: true`

### 2. 标签 (直接改)

| 组 | 标签 |
| --- | --- |
| 类型 | `type: feat` `type: fix` `type: docs` `type: refactor` `type: perf` `type: test` `type: chore` `type: ci` `type: question` |
| 状态 | `status: triage` `status: in-progress` `status: blocked` |
| 优先级 | `priority: P0` ~ `priority: P3` |

颜色和描述以配置为准, 不一致就更新. GitHub 默认标签里意思相同的直接改名, 已经打在 Issue 上的标签跟着改名: `bug` → `type: fix`, `enhancement` → `type: feat`, `documentation` → `type: docs`, `question` → `type: question`. 新标签已经存在时旧标签保持原样. **不删除任何标签.**

### 3. 文件 (每个仓库开一个 PR)

所有文件放在 `governance/setup` 分支上, 每个仓库只开一个 PR, 不直接推默认分支. 已有打开的治理 PR 时往它上面追加提交; 内容已经一致就什么都不做.

| 文件 | 作用 |
| --- | --- |
| `.github/ISSUE_TEMPLATE/feature.yml` | 功能需求: 背景/痛点, 期望行为, 验收标准, 原型/截图, 优先级下拉 |
| `.github/ISSUE_TEMPLATE/bug.yml` | 缺陷报告: 现象, 复现步骤, 期望 vs 实际, 截图/录屏, 环境, 日志 (按 shell 代码块显示) |
| `.github/ISSUE_TEMPLATE/question.yml` | 提问 / 讨论 |
| `.github/ISSUE_TEMPLATE/config.yml` | 关闭空白 Issue |
| `.github/pull_request_template.md` | 标题规则提醒, `Closes #`, 改动说明, 改前/改后截图表格, 测试方式, 自查清单 |
| `.github/commit-convention.yml` | 本仓库的提交规范: 风格, 类型, 标题长度, 基线提交 |
| `.github/workflows/commit-lint.yml` | 检查 PR 标题、PR 内提交和推送到默认分支的提交 |
| `.github/scripts/commit_lint.py` | 检查脚本, 只用 Python 标准库, CI 和本地钩子共用 |
| `.github/hooks/commit-msg` | 可选的本地钩子 |

表单的每个输入框都有示例和附图说明: 截图、录屏可以直接拖进输入框或粘贴, GitHub 会自动上传. Issue 表单会自动打上对应的 `type:` 标签和 `status: triage`. 优先级下拉只写在正文里, 标签由维护者打.

生成的文件第一行带 `hx-governance: managed` 标记. 有标记的文件下次治理会被更新; 删掉这一行就不再覆盖. 仓库里已经有自定义的 Issue 模板或 PR 模板 (含根目录、`docs/` 和 `PULL_REQUEST_TEMPLATE/` 目录) 时整组跳过, 写在审阅单里. 想强制覆盖, 在 `overrides` 里设 `overwrite_custom_files: true`.

空仓库没有默认分支, 只改设置和标签, 有第一次推送后再扫描才会开 PR.

### 4. 提交规范

两种风格:

- 约定式 `conventional`: `type(scope): 描述`, 例如 `feat(login): 支持扫码登录`. scope 可省, 破坏性改动可写 `feat!: ...`
- 方括号 `bracket`: `[type]: 描述` 或 `[type] 描述`, 例如 `[fix]: 修复登录按钮无响应`. scope 写成 `[fix(ui)]: ...`

类型默认是 `feat fix docs style refactor perf test build ci chore revert`, 标题行 (含类型) 不超过 72 个字符. 合并提交 (`Merge ...`)、`Revert "..."`、`Initial commit` 和 `fixup!` / `squash!` 不检查.

风格怎么定, 按顺序:

1. `overrides` 里强制的风格
2. 已经接入过的仓库, 沿用 `.github/commit-convention.yml` 里的风格和基线
3. 抽样默认分支最近 200 个提交 (不含合并提交). 约定式或方括号占比至少 30% 且多于另一种, 就沿用它
4. 其他情况 (包括空仓库和看不出风格的仓库) 用约定式

接入时把默认分支最新提交记为基线 (`baseline`). 它和之前的提交都不检查, 只管以后.

commit-lint 的检查:

- PR 打开、改标题、推新提交时: 检查 PR 标题. 不合规就失败, 并由机器人在 PR 下评论一个建议标题, 改好后评论自动删除. 合并用 squash, PR 标题就是默认分支上的那条提交, 所以标题合规即可把整个 PR 改写成合规的提交
- PR 内的单个提交默认只提示不失败 (`pr_commits: warn`), 因为 squash 后它们不会进入默认分支. 想严格就改成 `error`
- 推送到默认分支时: 检查基线之后、这次推送带来的提交, 结果写在 job summary, 有不合规就让这次运行失败. 推送已经发生, 只能事后报告. `xxx[bot]` 的提交默认跳过

本地钩子 (可选, 每个克隆执行一次):

```sh
git config core.hooksPath .github/hooks
```

需要本机有 `python3`. 临时跳过用 `git commit --no-verify`.

### 5. 默认分支规则集 (只限公开仓库)

公开仓库会在默认分支上建规则集 `governance-default-branch`: 必须通过 PR 合并, 且 `commit-lint` 检查通过. 绕过名单为空, 所以你自己也要走 PR, 但不需要别人批准 (要求的审批数是 0), 检查通过就能自己合并.

创建时机:

- 必须先有检查才能要求它. 治理 PR 合入、`commit-lint.yml` 进入默认分支之后, 再执行一次治理才会创建. 在此之前审阅单里写「治理 PR 合入后再执行一次」
- 仓库里有工作流会直接写默认分支 (`git push`、`git-auto-commit-action` 等) 时不创建, 否则那些工作流会被拦住. 确认无碍后在 `overrides` 里设 `ruleset: true`. 本仓库的仓库卫生扫描就会推送状态卡片, 属于这种情况
- 某个仓库不想要: `overrides` 里设 `ruleset: false`; 全部关闭: `ruleset.enabled_for_public: false`

GitHub 返回 403 / 422 (计划限制或权限不足) 时跳过并写进执行结果, 不影响其他步骤.

## 能拦住什么, 拦不住什么

| 场景 | 公开仓库 | 私有仓库 (免费计划) |
| --- | --- | --- |
| PR 标题不合规 | 检查失败, 规则集禁止合并 | 检查失败并评论建议标题, 但仍然可以合并 |
| 直接推默认分支 | 规则集禁止 | 能推上去, commit-lint 事后失败报告 |
| 本地提交不合规 | 装了钩子会拦; `--no-verify` 可绕过 | 同左 |
| PR 内的单个提交不合规 | 默认只提示, squash 后不留下 | 同左 |
| 网页上直接编辑文件 | 规则集要求走 PR | 能直接提交, 事后报告 |
| 来自 fork 的 PR | 检查照常, 但只读令牌写不了建议评论 | 同左 |

私有仓库在免费计划下没有规则集和分支保护, 没有办法硬性阻止, 只能靠 commit-lint 失败、建议评论和本地钩子提醒. 治理不使用任何需要付费计划的功能.

规则集创建之前, 公开仓库也和私有仓库一样只能事后报告.

## 配置

[config.yml](./config.yml):

- 范围: `owner`, `include_private`, `include_forks`, `include_archived`, `active_within_days`, `ignored_repositories`, 与仓库卫生一致 (最近 365 天有推送, 含私有, 跳过 fork 和归档)
- 提交规范: `commit_sample_size`, `default_style`, `style_min_share`, `commit_types`, `max_header_length`, `pr_commits`, `skip_bot_commits`
- 合并设置: `settings`
- 规则集: `ruleset`
- 标签: `labels`, 每项 `name` / `color` / `description`, `aliases` 是可以改名迁移过来的旧标签
- 按仓库覆盖: `overrides.<仓库名>` 可设 `style`, `ruleset`, `files`, `labels`, `settings`, `overwrite_custom_files`

审阅单在这个公开仓库里, 私有仓库的名字会出现在审阅单上, 但不会出现私有仓库的代码或提交内容.

## 重复运行

扫描和执行都可以反复跑:

- 设置和标签与配置一致时不调用写接口
- 治理 PR 已经打开就追加提交, 内容一致就跳过; 没有打开的 PR 时才新开, 不会重复开 PR
- 规则集按名字识别, 已存在且规则完整就跳过, 不完整就更新
- 基线一旦写进仓库就不再变化
- 全部合规的仓库不出现在待治理列表里
