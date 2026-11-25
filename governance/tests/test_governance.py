"""Style detection, message validation, template rendering and decision logic."""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import yaml

from governance import commit_lint
from governance.apply_run import apply_files, apply_plan, ruleset_body
from governance.commit_lint import (
    Convention,
    classify,
    detect_style,
    parse_convention,
    suggest,
    validate,
)
from governance.config import load_config, parse_config
from governance.decide import RepoSnapshot, choose_style, plan_repo
from governance.github_api import GitHubError, _collect_tree
from governance.labels import Label, plan_labels
from governance.render import (
    Report,
    extract_payload,
    mark_done,
    render_issue,
    selected_repos,
    unchecked_repos,
)
from governance.templates import (
    CONVENTION,
    HOOK,
    LINT_SCRIPT,
    LINT_WORKFLOW,
    MODE_EXEC,
    PR_TEMPLATE,
    RenderInput,
    is_managed,
    render_files,
    setup_title,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = load_config(ROOT / "config.yml")
HEAD = "a" * 40
GOOD_SETTINGS = {
    "delete_branch_on_merge": True,
    "allow_squash_merge": True,
    "squash_merge_commit_title": "PR_TITLE",
    "squash_merge_commit_message": "COMMIT_MESSAGES",
}


def make_snapshot(**changes: object) -> RepoSnapshot:
    """
    Build a repository snapshot for the governance decision tests
    .agents/notes/implemented/feature/2026-11-25-repository-governance.md
    """
    base: dict[str, object] = {
        "owner": "HengXin666",
        "repo": "demo",
        "default_branch": "main",
        "head_sha": HEAD,
        "settings": dict(GOOD_SETTINGS),
        "labels": [Label(spec.name, spec.color, spec.description) for spec in CONFIG.labels],
        "headers": ["fix(core): 修复崩溃"] * 10,
        "rulesets": [],
    }
    base.update(changes)
    return RepoSnapshot(**base)  # type: ignore[arg-type]


def onboarded_files(style: str = "conventional", baseline: str = "b" * 40, branch: str = "main") -> tuple[dict, dict]:
    rendered = render_files(
        RenderInput(style, CONFIG.commit_types, CONFIG.max_header_length, baseline, branch, CONFIG.pr_commits, True, "commit-lint")
    )
    return {path: spec.content for path, spec in rendered.items()}, {path: spec.mode for path, spec in rendered.items()}


class StyleDetectionTest(unittest.TestCase):
    def test_classify_shapes(self) -> None:
        self.assertEqual(classify("feat(api): 新增接口"), "conventional")
        self.assertEqual(classify("fix: typo"), "conventional")
        self.assertEqual(classify("[fix]: 修复"), "bracket")
        self.assertEqual(classify("[feat] add page"), "bracket")
        self.assertEqual(classify("【docs】更新说明"), "bracket")
        self.assertEqual(classify("更新 README"), "other")
        self.assertEqual(classify("Note: something"), "other")
        self.assertEqual(classify("Merge pull request #3 from a/b"), "ignored")
        self.assertEqual(classify("Merge branch 'main' into dev"), "ignored")
        self.assertEqual(classify('Revert "feat: x"'), "ignored")

    def test_dominant_conventional(self) -> None:
        stats = detect_style(["feat: a", "fix(x): b", "chore: c", "随手改", "Merge pull request #1 from x"])
        self.assertEqual(stats.dominant, "conventional")
        self.assertEqual((stats.total, stats.conventional, stats.other, stats.ignored), (4, 3, 1, 1))

    def test_dominant_bracket(self) -> None:
        stats = detect_style(["[fix]: a", "[feat]: b", "[docs]: c", "feat: d", "update"])
        self.assertEqual(stats.dominant, "bracket")

    def test_no_style_when_share_too_low(self) -> None:
        headers = ["feat: a", "fix: b", "chore: c"] + [f"改动 {index}" for index in range(20)]
        self.assertEqual(detect_style(headers).dominant, "none")

    def test_small_history(self) -> None:
        self.assertEqual(detect_style(["[feat] a", "[fix] b"]).dominant, "bracket")
        self.assertEqual(detect_style(["feat: a", "fix: b"], min_count=3).dominant, "none")
        self.assertEqual(detect_style([]).dominant, "none")


class ValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conventional = Convention()
        self.bracket = Convention(style="bracket")

    def test_conventional(self) -> None:
        self.assertEqual(validate("feat(login): 支持扫码登录", self.conventional), [])
        self.assertEqual(validate("fix!: 修改返回值", self.conventional), [])
        self.assertTrue(validate("[fix]: 修复", self.conventional))
        self.assertTrue(validate("feat:没有空格", self.conventional))
        self.assertIn("类型", validate("feature: x", self.conventional)[0])
        self.assertIn("类型", validate("Fix: x", self.conventional)[0])
        self.assertEqual(validate("", self.conventional), ["提交信息为空"])

    def test_bracket(self) -> None:
        self.assertEqual(validate("[fix]: 修复登录", self.bracket), [])
        self.assertEqual(validate("[feat(ui)]: 新按钮", self.bracket), [])
        self.assertTrue(validate("fix: 修复登录", self.bracket))
        self.assertEqual(validate("[fix] 修复登录", self.bracket), [])
        self.assertTrue(validate("[fix]修复登录", self.bracket))
        self.assertTrue(validate("[Fix]: 修复登录", self.bracket))

    def test_ignored_messages(self) -> None:
        for header in ("Merge pull request #12 from HengXin666/x", 'Revert "feat: a"', "Initial commit", "fixup! feat: a"):
            self.assertEqual(validate(header, self.conventional), [], header)

    def test_length(self) -> None:
        header = "feat: " + "字" * 70
        errors = validate(header, self.conventional)
        self.assertEqual(len(errors), 1)
        self.assertIn("超过 72", errors[0])

    def test_header_skips_comments(self) -> None:
        self.assertEqual(commit_lint.header_of("# 注释\n\nfeat: a\n\n正文"), "feat: a")


class SuggestTest(unittest.TestCase):
    def test_convert_between_styles(self) -> None:
        self.assertEqual(suggest("[fix]: 修复登录", Convention()), "fix: 修复登录")
        self.assertEqual(suggest("fix(ui): 修复登录", Convention(style="bracket")), "[fix(ui)]: 修复登录")
        self.assertEqual(suggest("feature: 新页面", Convention()), "feat: 新页面")
        self.assertEqual(suggest("Fix: crash", Convention()), "fix: crash")

    def test_guess_type_from_words_and_branch(self) -> None:
        self.assertEqual(suggest("修复 登录按钮无响应。", Convention()), "fix: 修复 登录按钮无响应")
        self.assertEqual(suggest("更新 README 文档", Convention()), "docs: 更新 README 文档")
        self.assertEqual(suggest("Add export page", Convention()), "feat: Add export page")
        self.assertEqual(suggest("调整一下", Convention(), branch="perf/cache"), "perf: 调整一下")
        self.assertEqual(suggest("调整一下", Convention()), "chore: 调整一下")

    def test_suggestion_is_valid_and_short(self) -> None:
        convention = Convention()
        for title in ("随便改改", "[docs] 更新", "x" * 200, "Fix: crash on start."):
            suggestion = suggest(title, convention)
            self.assertEqual(validate(suggestion, convention), [], suggestion)


class ConventionFileTest(unittest.TestCase):
    def test_round_trip(self) -> None:
        files, _modes = onboarded_files("bracket", "c" * 40)
        parsed = parse_convention(files[CONVENTION])
        self.assertEqual(parsed.style, "bracket")
        self.assertEqual(parsed.baseline, "c" * 40)
        self.assertEqual(parsed.types, CONFIG.commit_types)
        self.assertEqual(parsed.max_header_length, 72)
        self.assertEqual(parsed.pr_commits, "warn")
        self.assertTrue(parsed.skip_bot_commits)

    def test_bad_style(self) -> None:
        with self.assertRaises(ValueError):
            parse_convention("style: angular\n")


class TemplateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.files, self.modes = onboarded_files()

    def test_yaml_files_parse(self) -> None:
        for path, text in self.files.items():
            if path.endswith(".yml"):
                self.assertIsInstance(yaml.safe_load(text), dict, path)
            self.assertTrue(is_managed(text), path)

    def test_issue_forms(self) -> None:
        feature = yaml.safe_load(self.files[".github/ISSUE_TEMPLATE/feature.yml"])
        bug = yaml.safe_load(self.files[".github/ISSUE_TEMPLATE/bug.yml"])
        question = yaml.safe_load(self.files[".github/ISSUE_TEMPLATE/question.yml"])
        config = yaml.safe_load(self.files[".github/ISSUE_TEMPLATE/config.yml"])
        self.assertIs(config["blank_issues_enabled"], False)
        self.assertIn("type: feat", feature["labels"])
        self.assertIn("type: fix", bug["labels"])
        self.assertIn("type: question", question["labels"])
        feature_labels = [item["attributes"]["label"] for item in feature["body"] if item["type"] != "markdown"]
        self.assertEqual(feature_labels, ["背景 / 痛点", "期望行为", "验收标准", "原型 / 截图", "优先级"])
        priority = next(item for item in feature["body"] if item["type"] == "dropdown")
        self.assertEqual(len(priority["attributes"]["options"]), 4)
        bug_labels = [item["attributes"]["label"] for item in bug["body"] if item["type"] != "markdown"]
        self.assertEqual(bug_labels, ["现象", "复现步骤", "期望 vs 实际", "截图 / 录屏", "环境", "日志"])
        logs = next(item for item in bug["body"] if item.get("id") == "logs")
        self.assertEqual(logs["attributes"]["render"], "shell")
        for form in (feature, bug, question):
            for item in form["body"]:
                if item["type"] == "textarea":
                    self.assertTrue(item["attributes"].get("placeholder"), item)
                    self.assertTrue(item["attributes"].get("description"), item)
        self.assertIn("拖", self.files[".github/ISSUE_TEMPLATE/feature.yml"])
        self.assertIn("图文并茂", self.files[".github/ISSUE_TEMPLATE/bug.yml"])

    def test_pr_template(self) -> None:
        text = self.files[PR_TEMPLATE]
        for part in ("Closes #", "## 改动说明", "| 改前 | 改后 |", "## 测试方式", "## 自查清单", "type(scope): 描述"):
            self.assertIn(part, text)
        bracket, _modes = onboarded_files("bracket")
        self.assertIn("[type]: 描述", bracket[PR_TEMPLATE])

    def test_workflow_and_hook(self) -> None:
        workflow = yaml.safe_load(self.files[LINT_WORKFLOW])
        triggers = workflow[True] if True in workflow else workflow["on"]
        self.assertEqual(triggers["pull_request"]["types"], ["opened", "edited", "synchronize", "reopened"])
        self.assertEqual(triggers["push"]["branches"], ["main"])
        job = workflow["jobs"]["commit-lint"]
        self.assertEqual(job["name"], "commit-lint")
        self.assertEqual(job["steps"][0]["with"]["fetch-depth"], 0)
        self.assertIn("${{ github.token }}", self.files[LINT_WORKFLOW])
        self.assertEqual(self.modes[HOOK], MODE_EXEC)
        self.assertEqual(self.modes[LINT_SCRIPT], MODE_EXEC)
        self.assertIn(".github/scripts/commit_lint.py\" hook", self.files[HOOK])
        source = (ROOT / "governance" / "commit_lint.py").read_text(encoding="utf-8")
        self.assertEqual(self.files[LINT_SCRIPT], source)

    def test_other_branch_name(self) -> None:
        files, _modes = onboarded_files(branch="master")
        self.assertIn('branches: ["master"]', files[LINT_WORKFLOW])

    def test_setup_title_matches_style(self) -> None:
        self.assertEqual(validate(setup_title("conventional"), Convention()), [])
        self.assertEqual(validate(setup_title("bracket"), Convention(style="bracket")), [])


class LabelTest(unittest.TestCase):
    def test_rename_update_create_never_delete(self) -> None:
        existing = [
            Label("bug", "d73a4a", "Something isn't working"),
            Label("enhancement", "a2eeef", "New feature"),
            Label("type: docs", "000000", "旧描述"),
            Label("documentation", "0075ca", "Docs"),
            Label("wontfix", "ffffff", ""),
        ]
        ops = plan_labels(existing, CONFIG.labels)
        by_target = {op.new_name: op for op in ops}
        self.assertEqual(by_target["type: fix"].kind, "rename")
        self.assertEqual(by_target["type: fix"].name, "bug")
        self.assertEqual(by_target["type: feat"].kind, "rename")
        self.assertEqual(by_target["type: docs"].kind, "update")
        self.assertEqual(by_target["status: triage"].kind, "create")
        self.assertNotIn("wontfix", {op.name for op in ops})
        self.assertNotIn("documentation", {op.name for op in ops})
        self.assertEqual({op.kind for op in ops}, {"create", "update", "rename"})

    def test_already_synced(self) -> None:
        existing = [Label(spec.name, spec.color.upper(), spec.description) for spec in CONFIG.labels]
        self.assertEqual(plan_labels(existing, CONFIG.labels), [])


class DecisionTest(unittest.TestCase):
    def test_new_empty_repo(self) -> None:
        plan = plan_repo(make_snapshot(empty=True, head_sha="", headers=[], labels=[], settings={}), CONFIG)
        self.assertEqual((plan.style, plan.style_source), ("conventional", "empty"))
        self.assertEqual(plan.file_changes, {})
        self.assertEqual(plan.ruleset, "defer")
        self.assertTrue(plan.settings_patch)
        self.assertEqual(len(plan.label_ops), len(CONFIG.labels))
        self.assertFalse(plan.compliant)

    def test_stock_bracket_repo_keeps_style_and_baseline(self) -> None:
        plan = plan_repo(make_snapshot(headers=["[fix]: a", "[feat]: b", "[docs]: c", "随手"]), CONFIG)
        self.assertEqual((plan.style, plan.style_source), ("bracket", "history"))
        self.assertEqual(plan.baseline, HEAD)
        convention = parse_convention(plan.file_changes[CONVENTION].content)
        self.assertEqual((convention.style, convention.baseline), ("bracket", HEAD))
        self.assertEqual(plan.ruleset, "defer")

    def test_mixed_history_defaults_to_conventional(self) -> None:
        plan = plan_repo(make_snapshot(headers=[f"改动 {index}" for index in range(30)]), CONFIG)
        self.assertEqual((plan.style, plan.style_source), ("conventional", "default"))

    def test_override_and_reviewed_style(self) -> None:
        snapshot = make_snapshot(headers=["[fix]: a"] * 5)
        self.assertEqual(choose_style(detect_style(snapshot.headers), CONFIG, snapshot, "conventional"), ("conventional", "reviewed"))
        forced = parse_config((ROOT / "config.yml").read_text(encoding="utf-8").replace("overrides: {}", "overrides:\n  demo:\n    style: conventional\n"))
        self.assertEqual(plan_repo(snapshot, forced).style_source, "override")

    def test_onboarded_repo_keeps_baseline_and_is_compliant(self) -> None:
        files, modes = onboarded_files("bracket", "b" * 40)
        rulesets = [{"id": 1, "name": CONFIG.ruleset_name, "enforcement": "active", "rule_types": ["pull_request", "required_status_checks"]}]
        plan = plan_repo(make_snapshot(files=files, modes=modes, rulesets=rulesets, headers=["feat: a"] * 9), CONFIG)
        self.assertEqual((plan.style, plan.style_source, plan.baseline), ("bracket", "existing", "b" * 40))
        self.assertEqual(plan.file_changes, {})
        self.assertEqual(plan.ruleset, "ok")
        self.assertTrue(plan.compliant)

    def test_ruleset_created_only_after_check_lands(self) -> None:
        files, modes = onboarded_files()
        plan = plan_repo(make_snapshot(files=files, modes=modes), CONFIG)
        self.assertEqual(plan.ruleset, "create")
        self.assertFalse(plan.compliant)
        private = plan_repo(make_snapshot(files=files, modes=modes, private=True, rulesets=None), CONFIG)
        self.assertEqual(private.ruleset, "private")
        self.assertTrue(private.compliant)
        unknown = plan_repo(make_snapshot(files=files, modes=modes, rulesets=None), CONFIG)
        self.assertEqual(unknown.ruleset, "unknown")

    def test_ruleset_skipped_when_workflow_pushes(self) -> None:
        files, modes = onboarded_files()
        files[".github/workflows/badges.yml"] = "steps:\n  - run: git push origin main\n"
        plan = plan_repo(make_snapshot(files=files, modes=modes), CONFIG)
        self.assertEqual(plan.ruleset, "pushes")
        self.assertIn("badges.yml", plan.ruleset_reason)
        forced = parse_config((ROOT / "config.yml").read_text(encoding="utf-8").replace("overrides: {}", "overrides:\n  demo:\n    ruleset: true\n"))
        self.assertEqual(plan_repo(make_snapshot(files=files, modes=modes), forced).ruleset, "create")

    def test_custom_templates_are_not_overwritten(self) -> None:
        snapshot = make_snapshot(
            files={
                ".github/ISSUE_TEMPLATE/bug_report.md": "---\nname: Bug\n---\n",
                "docs/pull_request_template.md": "我的模板",
                CONVENTION: "style: bracket\n",
            }
        )
        plan = plan_repo(snapshot, CONFIG)
        self.assertFalse(any(path.startswith(".github/ISSUE_TEMPLATE/") for path in plan.file_changes))
        self.assertNotIn(PR_TEMPLATE, plan.file_changes)
        self.assertNotIn(CONVENTION, plan.file_changes)
        self.assertIn(LINT_WORKFLOW, plan.file_changes)
        self.assertEqual(len(plan.files_skipped), 3)
        self.assertEqual(plan.style_source, "history")

    def test_managed_file_with_wrong_mode_is_rewritten(self) -> None:
        files, modes = onboarded_files()
        modes[HOOK] = "100644"
        plan = plan_repo(make_snapshot(files=files, modes=modes), CONFIG)
        self.assertEqual(list(plan.file_changes), [HOOK])

    def test_settings_patch_sends_squash_pair(self) -> None:
        settings = dict(GOOD_SETTINGS, squash_merge_commit_message="PR_BODY", delete_branch_on_merge=False)
        plan = plan_repo(make_snapshot(settings=settings), CONFIG)
        self.assertEqual(
            plan.settings_patch,
            {"delete_branch_on_merge": True, "squash_merge_commit_title": "PR_TITLE", "squash_merge_commit_message": "COMMIT_MESSAGES"},
        )
        self.assertEqual(plan_repo(make_snapshot(), CONFIG).settings_patch, {})

    def test_snapshot_tree_modes(self) -> None:
        snapshot = make_snapshot()
        _collect_tree(
            {"entries": [
                {"name": "hooks", "type": "tree", "object": {"entries": [
                    {"name": "commit-msg", "type": "blob", "mode": 33261, "object": {"text": "x", "isBinary": False}}
                ]}},
                {"name": "logo.png", "type": "blob", "mode": 33188, "object": {"text": None, "isBinary": True}},
            ]},
            ".github",
            snapshot,
        )
        self.assertEqual(snapshot.modes[".github/hooks/commit-msg"], "100755")
        self.assertEqual(snapshot.files[".github/hooks/commit-msg"], "x")
        self.assertIsNone(snapshot.files[".github/logo.png"])


class ConfigTest(unittest.TestCase):
    def test_repo_config(self) -> None:
        self.assertEqual(CONFIG.owner, "HengXin666")
        self.assertTrue(CONFIG.include_private)
        self.assertEqual(CONFIG.active_within_days, 365)
        self.assertEqual(CONFIG.settings.squash_merge_commit_title, "PR_TITLE")
        self.assertIn("bug", next(spec for spec in CONFIG.labels if spec.name == "type: fix").aliases)

    def test_rejects_bad_values(self) -> None:
        text = (ROOT / "config.yml").read_text(encoding="utf-8")
        for old, new in (
            ("overrides: {}", "overrides:\n  x:\n    style: angular\n"),
            ('color: "1d76db"', 'color: "blue"'),
            ("squash_merge_commit_title: PR_TITLE", "squash_merge_commit_title: TITLE"),
            ("pr_commits: warn", "pr_commits: maybe"),
        ):
            with self.assertRaises(ValueError, msg=new):
                parse_config(text.replace(old, new))


class RenderTest(unittest.TestCase):
    def test_issue_round_trip(self) -> None:
        files, modes = onboarded_files()
        rulesets = [{"id": 1, "name": CONFIG.ruleset_name, "enforcement": "active", "rule_types": ["pull_request", "required_status_checks"]}]
        pending = plan_repo(make_snapshot(repo="todo", labels=[Label("bug", "d73a4a", "")]), CONFIG)
        done = plan_repo(make_snapshot(repo="clean", files=files, modes=modes, rulesets=rulesets), CONFIG)
        broken = plan_repo(make_snapshot(repo="broken", errors=["读取仓库失败: x"]), CONFIG)
        report = Report(scanned_at="2026-11-25 22:00 +0800", plans=[pending, done, broken], issue_number=9)
        body = render_issue(report)
        self.assertIn("- [ ] <!--repo:HengXin666/todo--> `todo` (公开)", body)
        self.assertNotIn("<!--repo:HengXin666/clean-->", body)
        self.assertIn("`clean`", body)
        self.assertIn("`broken`: 读取仓库失败", body)
        self.assertIn("改名 `bug` → `type: fix`", body)
        self.assertEqual(extract_payload(body)["repos"], {"HengXin666/todo": {"style": "conventional"}})
        self.assertEqual(selected_repos(body), [])
        checked = body.replace("- [ ] <!--repo:HengXin666/todo-->", "- [x] <!--repo:HengXin666/todo-->")
        self.assertEqual(selected_repos(checked), ["HengXin666/todo"])
        marked = mark_done(checked, {"HengXin666/todo"}, set())
        self.assertIn("（已处理）", marked)
        self.assertEqual(selected_repos(marked), [])
        self.assertEqual(unchecked_repos(marked), [])
        failed = mark_done(checked, set(), {"HengXin666/todo"})
        self.assertEqual(unchecked_repos(failed), ["HengXin666/todo"])

    def test_issue_is_trimmed(self) -> None:
        plans = [plan_repo(make_snapshot(repo=f"repo-{index}", labels=[]), CONFIG) for index in range(40)]
        body = render_issue(Report(scanned_at="now", plans=plans), limit=8000)
        self.assertLessEqual(len(body), 8000)
        self.assertIn("审阅单过长", body)


class FakeWorker:
    def __init__(self, same_tree: bool = False, open_pr: dict | None = None, fail: int | None = None) -> None:
        self.calls: list[tuple] = []
        self.same_tree = same_tree
        self.open_pr = open_pr
        self.fail = fail

    def __getattr__(self, name: str):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if self.fail and name in {"create_ruleset", "patch_repo"}:
                raise GitHubError(self.fail, "Upgrade to GitHub Pro")
            if name == "open_pull":
                return self.open_pr
            if name == "ref_sha":
                return HEAD if args[2] == "main" else None
            if name == "commit_tree":
                return "tree0"
            if name == "create_tree":
                return "tree0" if self.same_tree else "tree1"
            if name == "create_commit":
                return "c" * 40
            if name == "create_pull":
                return {"html_url": "https://github.com/HengXin666/demo/pull/1"}
            return None

        return call


class ApplyTest(unittest.TestCase):
    def test_ruleset_body(self) -> None:
        body = ruleset_body(CONFIG)
        self.assertEqual(body["bypass_actors"], [])
        self.assertEqual(body["conditions"]["ref_name"]["include"], ["~DEFAULT_BRANCH"])
        self.assertEqual({rule["type"] for rule in body["rules"]}, {"pull_request", "required_status_checks"})
        self.assertEqual(body["rules"][0]["parameters"]["required_approving_review_count"], 0)

    def test_files_open_one_pr(self) -> None:
        worker = FakeWorker()
        plan = plan_repo(make_snapshot(), CONFIG)
        lines, ok = apply_files(worker, CONFIG, plan)  # type: ignore[arg-type]
        self.assertTrue(ok, lines)
        names = [call[0] for call in worker.calls]
        self.assertEqual(names.count("create_pull"), 1)
        self.assertIn("create_ref", names)
        pull = next(call for call in worker.calls if call[0] == "create_pull")
        self.assertEqual(pull[1][2], "chore(governance): 接入仓库治理规范")
        self.assertEqual(pull[1][4], "governance/setup")
        tree = next(call for call in worker.calls if call[0] == "create_tree")
        modes = {entry["path"]: entry["mode"] for entry in tree[1][3]}
        self.assertEqual(modes[HOOK], "100755")

    def test_files_idempotent(self) -> None:
        worker = FakeWorker(same_tree=True)
        lines, ok = apply_files(worker, CONFIG, plan_repo(make_snapshot(), CONFIG))  # type: ignore[arg-type]
        self.assertTrue(ok)
        self.assertEqual(lines, ["文件已经一致"])
        self.assertNotIn("create_pull", [call[0] for call in worker.calls])
        worker = FakeWorker(same_tree=True, open_pr={"html_url": "u", "head": {"sha": "d" * 40}})
        lines, ok = apply_files(worker, CONFIG, plan_repo(make_snapshot(), CONFIG))  # type: ignore[arg-type]
        self.assertEqual(lines, ["治理 PR 已是最新: u"])
        worker = FakeWorker(open_pr={"html_url": "u", "head": {"sha": "d" * 40}})
        apply_files(worker, CONFIG, plan_repo(make_snapshot(), CONFIG))  # type: ignore[arg-type]
        move = next(call for call in worker.calls if call[0] == "move_ref")
        self.assertIs(move[2]["force"], False)

    def test_ruleset_failure_is_reported_not_raised(self) -> None:
        files, modes = onboarded_files()
        plan = plan_repo(make_snapshot(files=files, modes=modes), CONFIG)
        lines, ok = apply_plan(FakeWorker(fail=403), CONFIG, plan)  # type: ignore[arg-type]
        self.assertFalse(ok)
        self.assertTrue(any("规则集不可用 (403)" in line for line in lines), lines)


class CiRunTest(unittest.TestCase):
    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.dir, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, message: str, author: str = "HX <hx@example.com>") -> str:
        (Path(self.dir) / "f.txt").write_text(message, encoding="utf-8")
        self.git("add", "f.txt")
        self.git("commit", "-q", "-m", message, "--author", author)
        return self.git("rev-parse", "HEAD")

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "hx@example.com")
        self.git("config", "user.name", "HX")
        self.git("config", "commit.gpgsign", "false")
        self.old = self.commit("乱写的旧提交")
        self.cwd = os.getcwd()
        os.chdir(self.dir)

    def tearDown(self) -> None:
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def run_ci(self, event_name: str, event: dict, convention: Convention) -> tuple[int, str]:
        event_path = Path(self.dir) / "event.json"
        event_path.write_text(json.dumps(event), encoding="utf-8")
        summary = Path(self.dir) / "summary.md"
        env = {"GITHUB_EVENT_NAME": event_name, "GITHUB_EVENT_PATH": str(event_path), "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_REPOSITORY": ""}
        with patch.dict(os.environ, env), redirect_stdout(io.StringIO()):
            code = commit_lint.run_ci(convention)
        return code, summary.read_text(encoding="utf-8")

    def test_push_respects_baseline_and_bots(self) -> None:
        baseline = self.commit("又一个旧提交")
        self.commit("feat: 新功能")
        self.commit("更新状态卡片", author="github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>")
        after = self.git("rev-parse", "HEAD")
        convention = Convention(baseline=baseline)
        code, summary = self.run_ci("push", {"before": "0" * 40, "after": after}, convention)
        self.assertEqual(code, 0, summary)
        bad = self.commit("随手改")
        code, summary = self.run_ci("push", {"before": after, "after": bad}, convention)
        self.assertEqual(code, 1)
        self.assertIn(bad[:8], summary)
        self.assertNotIn(self.old[:8], summary)

    def test_pull_request_title_and_commits(self) -> None:
        base = self.git("rev-parse", "HEAD")
        head = self.commit("wip")
        event = {"pull_request": {"title": "修复登录", "number": 0, "base": {"sha": base}, "head": {"sha": head, "ref": "fix/login"}}}
        code, summary = self.run_ci("pull_request", event, Convention())
        self.assertEqual(code, 1)
        self.assertIn("建议: `fix: 修复登录`", summary)
        self.assertIn("1 个提交不符合规范", summary)
        event["pull_request"]["title"] = "fix(login): 修复登录"
        code, summary = self.run_ci("pull_request", event, Convention())
        self.assertEqual(code, 0, summary)
        code, _summary = self.run_ci("pull_request", event, Convention(pr_commits="error"))
        self.assertEqual(code, 1)

    def test_hook(self) -> None:
        message = Path(self.dir) / "MSG"
        message.write_text("# 注释\nfeat: 好的提交\n", encoding="utf-8")
        self.assertEqual(commit_lint.run_hook(str(message), Convention()), 0)
        message.write_text("随便写\n", encoding="utf-8")
        with redirect_stdout(io.StringIO()), patch("sys.stderr", new=io.StringIO()) as err:
            self.assertEqual(commit_lint.run_hook(str(message), Convention()), 1)
        self.assertIn("建议: chore: 随便写", err.getvalue())


if __name__ == "__main__":
    unittest.main()
