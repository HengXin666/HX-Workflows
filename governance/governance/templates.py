"""Render the files that governance writes into each target repository."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MARKER = "hx-governance: managed"
VERSION = "v1"
YAML_MARK = f"# {MARKER} {VERSION} (由 HX-Workflows 仓库治理生成, 删掉这一行后不再自动覆盖)"
MD_MARK = f"<!-- {MARKER} {VERSION} (由 HX-Workflows 仓库治理生成, 删掉这一行后不再自动覆盖) -->"
MODE_FILE = "100644"
MODE_EXEC = "100755"

ISSUE_DIR = ".github/ISSUE_TEMPLATE"
PR_TEMPLATE = ".github/pull_request_template.md"
CONVENTION = ".github/commit-convention.yml"
LINT_WORKFLOW = ".github/workflows/commit-lint.yml"
LINT_SCRIPT = ".github/scripts/commit_lint.py"
HOOK = ".github/hooks/commit-msg"
HOOK_INSTALL = "git config core.hooksPath .github/hooks"

IMAGE_HINT = "可以直接把图片或录屏拖进输入框, 或者截图后 Ctrl+V 粘贴, GitHub 会自动上传并插入链接. 请尽量图文并茂."


@dataclass(frozen=True)
class FileSpec:
    path: str
    content: str
    mode: str = MODE_FILE


@dataclass(frozen=True)
class RenderInput:
    style: str
    types: tuple[str, ...]
    max_header_length: int
    baseline: str
    default_branch: str
    pr_commits: str
    skip_bot_commits: bool
    check_name: str


def is_managed(text: str | None) -> bool:
    return bool(text) and MARKER in text


def example_title(style: str, kind: str = "feat", scope: str = "login", subject: str = "支持扫码登录") -> str:
    scope_text = f"({scope})" if scope else ""
    if style == "bracket":
        return f"[{kind}{scope_text}]: {subject}"
    return f"{kind}{scope_text}: {subject}"


def setup_title(style: str) -> str:
    return example_title(style, "chore", "governance" if style == "conventional" else "", "接入仓库治理规范")


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line else "" for line in text.splitlines())


def feature_form() -> str:
    return f"""{YAML_MARK}
name: ✨ 功能需求
description: 提出新功能或改进. 请尽量图文并茂, 写清楚为什么需要和怎样才算完成.
labels: ["type: feat", "status: triage"]
body:
  - type: markdown
    attributes:
      value: |
        感谢提出需求! {IMAGE_HINT}
  - type: textarea
    id: background
    attributes:
      label: 背景 / 痛点
      description: 现在遇到了什么问题, 谁会遇到, 发生得多频繁. 可以附一张现状截图.
      placeholder: |
        例: 每次导出报表都要手动筛选 3 次, 数据量大时要等 5 分钟以上.
        ![现状截图](拖入图片后自动生成链接)
    validations:
      required: true
  - type: textarea
    id: expected
    attributes:
      label: 期望行为
      description: 希望它怎样工作. 写用户能看到的结果, 不必写实现方案.
      placeholder: |
        例: 导出页增加「按日期范围导出」, 选好范围后一次导出, 30 秒内完成.
    validations:
      required: true
  - type: textarea
    id: acceptance
    attributes:
      label: 验收标准
      description: 满足哪些条件才算完成. 建议用勾选列表, 每条都能验证.
      placeholder: |
        - [ ] 可以选择开始和结束日期
        - [ ] 10 万行数据 30 秒内导出完成
        - [ ] 导出的文件名包含日期范围
    validations:
      required: true
  - type: textarea
    id: mockup
    attributes:
      label: 原型 / 截图
      description: |
        上传原型图、手绘草图、参考产品的截图或录屏. {IMAGE_HINT}
        图片多时可以用表格并排放, 例如 `| 现在 | 期望 |`.
      placeholder: |
        ![原型](拖入图片后自动生成链接)
        参考: https://example.com/similar-feature
    validations:
      required: false
  - type: dropdown
    id: priority
    attributes:
      label: 优先级
      description: 维护者会按这里的选择打上 priority 标签.
      options:
        - P0 紧急, 阻塞使用
        - P1 高, 希望尽快
        - P2 中, 排期处理
        - P3 低, 有空再做
      default: 2
    validations:
      required: true
"""


def bug_form() -> str:
    return f"""{YAML_MARK}
name: 🐛 缺陷报告
description: 报告一个错误或异常行为. 截图、录屏和日志能大大加快定位.
labels: ["type: fix", "status: triage"]
body:
  - type: markdown
    attributes:
      value: |
        提交前请先搜索是否已有相同问题. {IMAGE_HINT}
  - type: textarea
    id: summary
    attributes:
      label: 现象
      description: 一两句话说清楚哪里出了问题.
      placeholder: |
        例: 在设置页点击「保存」后页面白屏, 刷新后修改丢失.
    validations:
      required: true
  - type: textarea
    id: steps
    attributes:
      label: 复现步骤
      description: 按顺序写出最少的操作步骤, 别人照做就能看到问题.
      placeholder: |
        1. 打开 设置 -> 个人资料
        2. 修改昵称为超过 20 个字符的名字
        3. 点击「保存」
        4. 页面白屏
    validations:
      required: true
  - type: textarea
    id: expected-vs-actual
    attributes:
      label: 期望 vs 实际
      description: 期望看到什么, 实际看到什么.
      placeholder: |
        期望: 提示「昵称不能超过 20 个字符」.
        实际: 页面白屏, 控制台报 TypeError.
    validations:
      required: true
  - type: textarea
    id: media
    attributes:
      label: 截图 / 录屏
      description: |
        出错画面、控制台、网络面板的截图, 或者一段录屏. {IMAGE_HINT}
      placeholder: |
        ![出错截图](拖入图片后自动生成链接)
    validations:
      required: false
  - type: textarea
    id: environment
    attributes:
      label: 环境
      description: 操作系统、浏览器或运行时、项目版本或提交号.
      placeholder: |
        - 系统: Windows 11 23H2
        - 浏览器 / 运行时: Chrome 131 / Python 3.12
        - 版本: v1.4.2 (或提交 abc1234)
    validations:
      required: true
  - type: textarea
    id: logs
    attributes:
      label: 日志
      description: 相关的报错或日志, 粘贴原文即可, 会自动按代码块显示. 记得去掉令牌和密码.
      render: shell
      placeholder: |
        Traceback (most recent call last):
          File "app.py", line 42, in save
        TypeError: ...
    validations:
      required: false
"""


def question_form() -> str:
    return f"""{YAML_MARK}
name: 💬 提问 / 讨论
description: 使用上的疑问、设计讨论或想法. 不确定是不是缺陷时也可以先在这里问.
labels: ["type: question", "status: triage"]
body:
  - type: markdown
    attributes:
      value: |
        {IMAGE_HINT}
  - type: textarea
    id: question
    attributes:
      label: 问题 / 想讨论的内容
      description: 想知道什么, 或者想讨论什么方案.
      placeholder: |
        例: 配置文件里的 `timeout` 是按秒还是毫秒? 我设置 30 之后好像没有生效.
    validations:
      required: true
  - type: textarea
    id: context
    attributes:
      label: 背景与已尝试的做法
      description: 你的使用场景, 已经看过的文档或试过的办法. 可以附截图.
      placeholder: |
        - 场景: 在 CI 里调用接口
        - 已尝试: 把 timeout 改成 30000, 仍然 30 毫秒超时
        ![截图](拖入图片后自动生成链接)
    validations:
      required: false
"""


def issue_config() -> str:
    return f"""{YAML_MARK}
blank_issues_enabled: false
"""


def pr_template(style: str, types: tuple[str, ...], max_header_length: int) -> str:
    rule = "`type(scope): 描述`" if style == "conventional" else "`[type]: 描述`"
    return f"""{MD_MARK}
<!--
PR 标题规则: {rule}, 例如 `{example_title(style)}`.
类型: {', '.join(types)}. 标题行不超过 {max_header_length} 个字符.
合并方式是 squash, PR 标题就是默认分支上的提交信息. 标题不合规时 commit-lint 会失败并给出建议标题.
本地提交检查: {HOOK_INSTALL}
-->

## 关联 Issue

Closes #

## 改动说明

<!-- 改了什么, 为什么这样改. 有取舍就写上 -->

-

## 截图对比

<!-- 有界面变化时贴改前/改后截图, 直接把图片拖进表格单元格即可. 没有界面变化写「无」 -->

| 改前 | 改后 |
| --- | --- |
|  |  |

## 测试方式

<!-- 怎么验证的: 跑了哪些测试, 手动点了哪些路径 -->

- [ ]

## 自查清单

- [ ] PR 标题符合提交规范
- [ ] 已关联 Issue (Closes #编号)
- [ ] 已自测, 补充或更新了必要的测试
- [ ] 需要时已更新文档
- [ ] 界面改动附了改前/改后截图
"""


def convention_file(data: RenderInput) -> str:
    return f"""{YAML_MARK}
# 提交规范. CI (.github/workflows/commit-lint.yml) 和本地钩子 (.github/hooks/commit-msg) 都读这个文件.
# 本地启用钩子: {HOOK_INSTALL}
# style: conventional = type(scope): 描述 ; bracket = [type]: 描述
style: {data.style}
types: [{', '.join(data.types)}]
max_header_length: {data.max_header_length}
# 接入时默认分支的最新提交. 它和它之前的提交都不检查.
baseline: {data.baseline}
# PR 里单个提交不合规时: warn 只提示, error 让检查失败, off 不检查. squash 合并只保留 PR 标题.
pr_commits: {data.pr_commits}
skip_bot_commits: {'true' if data.skip_bot_commits else 'false'}
"""


def lint_workflow(data: RenderInput) -> str:
    branch = data.default_branch.replace('"', "")
    return f"""{YAML_MARK}
name: commit-lint

on:
  pull_request:
    types: [opened, edited, synchronize, reopened]
  push:
    branches: ["{branch}"]

permissions:
  contents: read
  pull-requests: write

concurrency:
  group: commit-lint-${{{{ github.event.pull_request.number || github.sha }}}}
  cancel-in-progress: true

jobs:
  commit-lint:
    name: {data.check_name}
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - name: 拉取仓库
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: 检查 PR 标题与提交信息
        env:
          GITHUB_TOKEN: ${{{{ github.token }}}}
        run: python3 .github/scripts/commit_lint.py ci
"""


def hook_script() -> str:
    return f"""#!/usr/bin/env sh
# {MARKER} {VERSION} (由 HX-Workflows 仓库治理生成, 删掉这一行后不再自动覆盖)
# 安装: {HOOK_INSTALL}
root="$(git rev-parse --show-toplevel)" || exit 0
command -v python3 >/dev/null 2>&1 || {{ echo "commit-msg: 没有 python3, 跳过提交规范检查" >&2; exit 0; }}
exec python3 "$root/{LINT_SCRIPT}" hook "$1"
"""


def lint_script() -> str:
    return (Path(__file__).parent / "commit_lint.py").read_text(encoding="utf-8")


def render_files(data: RenderInput) -> dict[str, FileSpec]:
    files = [
        FileSpec(f"{ISSUE_DIR}/feature.yml", feature_form()),
        FileSpec(f"{ISSUE_DIR}/bug.yml", bug_form()),
        FileSpec(f"{ISSUE_DIR}/question.yml", question_form()),
        FileSpec(f"{ISSUE_DIR}/config.yml", issue_config()),
        FileSpec(PR_TEMPLATE, pr_template(data.style, data.types, data.max_header_length)),
        FileSpec(CONVENTION, convention_file(data)),
        FileSpec(LINT_WORKFLOW, lint_workflow(data)),
        FileSpec(LINT_SCRIPT, lint_script(), MODE_EXEC),
        FileSpec(HOOK, hook_script(), MODE_EXEC),
    ]
    return {item.path: item for item in files}


def setup_body(style: str, files: list[str], skipped: list[str], baseline: str) -> str:
    lines = [
        "由 [HX-Workflows 仓库治理](https://github.com/HengXin666/HX-Workflows/tree/main/governance) 自动生成.",
        "",
        (
            f"- 提交规范: {('约定式 `type(scope): 描述`' if style == 'conventional' else '方括号 `[type]: 描述`')}, "
            f"例如 `{example_title(style)}`"
        ),
        f"- 基线提交: `{baseline[:12] or '无 (空仓库)'}`, 它和它之前的提交都不检查",
        f"- 本地钩子: `{HOOK_INSTALL}`",
        "",
        "### 写入的文件",
        "",
        *[f"- `{path}`" for path in files],
    ]
    if skipped:
        lines += ["", "### 跳过 (仓库里已有自定义文件)", "", *[f"- {item}" for item in skipped]]
    lines += [
        "",
        "合并后 commit-lint 会检查之后的 PR 标题和推送. 公开仓库再执行一次治理后会加上默认分支规则集.",
    ]
    return "\n".join(lines) + "\n"
