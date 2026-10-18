"""Decisions, review text, and status cards."""

from __future__ import annotations

import base64
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import hygiene.decide as decide_module
from hygiene.config import load_config, parse_config
from hygiene.decide import classify, skill_source_after_rescan
from hygiene.github_api import _frontmatter, branch_ref_path
from hygiene.local_git import LocalRepo, clone_args, git_env
from hygiene.models import (
    BranchInfo,
    CanonicalSkill,
    Config,
    RepoSnapshot,
    SkillInstance,
    parse_item_id,
    parse_version,
    version_equal,
)
from hygiene.render import badge_files, mark_done, one_line, pending_selection, render_issue, selected_ids, unchecked_ids
from hygiene.trees import GitFile, git_blob_mode, plan_skill_replacement, safe_branch_name, safe_skill_dir


def config() -> Config:
    return parse_config(
        """
owner: HengXin666
report_repository: HX-Workflows
actor: HengXin666
include_forks: false
include_archived: false
include_private: false
active_within_days: 0
skill_roots:
  - .agents/skills
preferred_sources:
  - HXLoLi
protected_branches:
  - main
  - develop
protected_branch_globs:
  - release/*
ignored_repositories:
  - secret
"""
    )


def snapshot(
    repo: str,
    *,
    default: str = "main",
    fork: bool = False,
    archived: bool = False,
    branches: tuple[BranchInfo, ...] = (),
    pulls: tuple[str, ...] = (),
    skills: tuple[SkillInstance, ...] = (),
    errors: tuple[str, ...] = (),
    private: bool = False,
    pushed_at: str | None = None,
) -> RepoSnapshot:
    return RepoSnapshot(
        owner="HengXin666",
        repo=repo,
        default_branch=default,
        archived=archived,
        fork=fork,
        branches=branches,
        open_pr_heads=pulls,
        skills=skills,
        errors=errors,
        private=private,
        pushed_at=pushed_at,
    )


def skill(
    repo: str,
    version: str | None,
    tree: str,
    *,
    path: str = ".agents/skills/hx-agent-notes",
    committed_at: str | None = "2026-10-01T00:00:00Z",
    name: str = "hx-agent-notes",
) -> SkillInstance:
    return SkillInstance(
        owner="HengXin666",
        repo=repo,
        path=path,
        folder=path.rsplit("/", 1)[-1],
        declared_name=name,
        version=version,
        parsed_version=parse_version(version),
        tree_sha=tree,
        commit_sha="abc1234def",
        committed_at=committed_at,
    )


class DecideTests(unittest.TestCase):
    def test_merged_branch_is_deletable(self) -> None:
        report = classify(
            [
                snapshot(
                    "demo",
                    branches=(
                        BranchInfo("main", protected=True),
                        BranchInfo("feat", protected=False, ahead=0, behind=4, status="BEHIND"),
                    ),
                )
            ],
            config(),
            "2026-10-18 18:00 +0800",
            "https://github.com/HengXin666/HX-Workflows/actions/runs/1",
        )
        self.assertEqual([item.branch for item in report.branches_deletable], ["feat"])

    def test_unmerged_protected_and_open_pr_are_rejected(self) -> None:
        report = classify(
            [
                snapshot(
                    "demo",
                    branches=(
                        BranchInfo("main", protected=True, ahead=0, behind=0),
                        BranchInfo("develop", protected=False, ahead=0, behind=1),
                        BranchInfo("wip", protected=False, ahead=2, behind=1, status="DIVERGED"),
                        BranchInfo("release/1", protected=False, ahead=0, behind=1),
                        BranchInfo("review", protected=False, ahead=0, behind=1),
                    ),
                    pulls=("review",),
                )
            ],
            config(),
            "2026-10-18 18:00 +0800",
            "",
        )
        self.assertEqual(report.branches_deletable, [])
        self.assertEqual(report.branches_not_merged, 1)
        self.assertEqual(report.branches_skipped_protected, 2)
        self.assertEqual(report.branches_skipped_pr, 1)

    def test_compare_failure_is_not_deletable(self) -> None:
        report = classify(
            [snapshot("demo", branches=(BranchInfo("feat", protected=False, compare_error="无法比较"),))],
            config(),
            "2026-10-18 18:00 +0800",
            "",
        )
        self.assertEqual(report.branches_deletable, [])
        self.assertEqual(report.branches_compare_failed, 1)
        self.assertTrue(any("比较失败" in item for item in report.errors))

    def test_highest_version_wins_and_preferred_repo_breaks_ties(self) -> None:
        report = classify(
            [
                snapshot("other", skills=(skill("other", "2.3", "tree-a", committed_at="2026-10-02T00:00:00Z"),)),
                snapshot("HXLoLi", skills=(skill("HXLoLi", "2.3", "tree-b", committed_at="2026-09-01T00:00:00Z"),)),
                snapshot("old", skills=(skill("old", "1.0", "tree-c", committed_at="2026-10-18T00:00:00Z"),)),
            ],
            config(),
            "2026-10-18 18:00 +0800",
            "",
        )
        self.assertEqual(report.canonical[0].repo, "HXLoLi")
        self.assertEqual([item.repo for item in report.skills_outdated], ["old"])
        self.assertTrue(report.skills_outdated[0].suspicious)
        self.assertEqual([item.repo for item in report.skills_diverged], ["other"])
        self.assertFalse(report.skills_diverged[0].actionable)

    def test_identical_tree_is_not_an_update(self) -> None:
        report = classify(
            [
                snapshot("HXLoLi", skills=(skill("HXLoLi", "2.3", "same"),)),
                snapshot("copy", skills=(skill("copy", "2.0", "same"),)),
            ],
            config(),
            "2026-10-18 18:00 +0800",
            "",
        )
        self.assertEqual(report.skills_outdated, [])
        self.assertEqual(len(report.skills_version_only), 1)

    def test_fork_archived_and_ignored_repos_are_skipped(self) -> None:
        report = classify(
            [
                snapshot("forked", fork=True, branches=(BranchInfo("old", protected=False, ahead=0, behind=1),)),
                snapshot("old", archived=True, branches=(BranchInfo("old", protected=False, ahead=0, behind=1),)),
                snapshot("secret", branches=(BranchInfo("old", protected=False, ahead=0, behind=1),)),
            ],
            config(),
            "2026-10-18 18:00 +0800",
            "",
        )
        self.assertEqual(report.repos_considered, 0)
        self.assertEqual(report.branches_deletable, [])
        self.assertEqual(report.repos_skipped_fork, 1)
        self.assertEqual(report.repos_skipped_archived, 1)
        self.assertEqual(report.repos_skipped_ignored, 1)


class ReviewTests(unittest.TestCase):
    def test_issue_is_chinese_and_unchecked_until_done(self) -> None:
        report = classify(
            [
                snapshot(
                    "demo",
                    branches=(BranchInfo("feat/x", protected=False, ahead=0, behind=3),),
                    skills=(skill("demo", "1.0", "old"),),
                ),
                snapshot("HXLoLi", skills=(skill("HXLoLi", "2.3", "new"),)),
            ],
            config(),
            "2026-10-18 18:00 +0800",
            "https://example.test/run",
        )
        body = render_issue(report)
        self.assertIn("仓库卫生审阅", body)
        self.assertIn("勾选要处理的项", body)
        self.assertIn("- [ ] <!--id:branch:HengXin666/demo:feat%2Fx-->", body)
        self.assertEqual(selected_ids(body), [])
        self.assertFalse(pending_selection(body))
        checked = body.replace("- [ ] <!--id:branch:", "- [x] <!--id:branch:", 1)
        chosen = selected_ids(checked)
        self.assertEqual(len(chosen), 1)
        self.assertEqual(parse_item_id(chosen[0]).value, "feat/x")
        done = mark_done(checked, set(chosen))
        self.assertIn("已处理", done)
        self.assertEqual(selected_ids(done), [])
        self.assertIn("hygiene-data", done)
        failed = mark_done(checked, set(), set(chosen))
        self.assertIn("- [ ] ", failed)
        self.assertIn("上次失败", failed)
        self.assertEqual(selected_ids(failed), [])
        self.assertTrue(unchecked_ids(failed))

    def test_cards_cover_pending_clean_and_failed_scans(self) -> None:
        pending = badge_files(None)
        self.assertIn("尚未扫描", pending["summary.svg"])
        self.assertEqual(json.loads(pending["summary.json"])["message"], "尚未扫描")
        clean = classify([], config(), "2026-10-18 18:00 +0800", "")
        clean_cards = badge_files(clean)
        self.assertIn(">0<", clean_cards["summary.svg"])
        self.assertEqual(json.loads(clean_cards["branches.json"])["color"], "2ea44f")
        clean.errors.append("demo: 分支扫描失败")
        failed_cards = badge_files(clean)
        self.assertIn("部分失败", failed_cards["summary.svg"])
        self.assertEqual(json.loads(failed_cards["summary.json"])["color"], "orange")


class TreeTests(unittest.TestCase):
    def test_replacement_updates_adds_and_deletes(self) -> None:
        changes = plan_skill_replacement(
            ".agents/skills/demo",
            [
                GitFile(".agents/skills/demo/SKILL.md", "100644", "old"),
                GitFile(".agents/skills/demo/old.py", "100644", "gone"),
            ],
            [
                GitFile(".agents/skills/demo/SKILL.md", "100644", "new"),
                GitFile(".agents/skills/demo/keep.py", "100755", "keep"),
            ],
        )
        by_path = {item.path: item for item in changes}
        self.assertEqual(by_path[".agents/skills/demo/SKILL.md"].sha, "new")
        self.assertEqual(by_path[".agents/skills/demo/SKILL.md"].upload_sha, "new")
        self.assertIsNone(by_path[".agents/skills/demo/old.py"].sha)
        self.assertEqual(by_path[".agents/skills/demo/keep.py"].mode, "100755")

    def test_unchanged_blob_is_omitted(self) -> None:
        changes = plan_skill_replacement(
            ".agents/skills/demo",
            [GitFile(".agents/skills/demo/SKILL.md", "100644", "same")],
            [GitFile(".agents/skills/demo/SKILL.md", "100644", "same")],
        )
        self.assertEqual(changes, [])

    def test_path_and_branch_guards(self) -> None:
        roots = (".agents/skills",)
        self.assertTrue(safe_skill_dir(".agents/skills/hx-agent-notes", roots))
        self.assertFalse(safe_skill_dir(".agents/skills/../secrets", roots))
        self.assertFalse(safe_skill_dir(".github/workflows/scan.yml", roots))
        self.assertTrue(safe_branch_name("feat/x", "main"))
        self.assertFalse(safe_branch_name("main", "main"))
        self.assertFalse(safe_branch_name("release/../../main", "main"))
        self.assertEqual(branch_ref_path("feat/x"), "heads/feat%2Fx")

    def test_frontmatter_version(self) -> None:
        name, version = _frontmatter('---\nname: hx-agent-notes\nmetadata:\n  version: "2.3"\n---\n')
        self.assertEqual(name, "hx-agent-notes")
        self.assertEqual(parse_version(version), (2, 3))
        self.assertIsNone(parse_version("2.3-beta"))
        self.assertTrue(parse_version("2.10") > parse_version("2.9"))
        self.assertIsNone(parse_version("2.²"))
        self.assertTrue(version_equal(None, None))
        name, version = _frontmatter('---\nname: hx-agent-notes # kept\nmetadata:\n  version: "2.3" # release\n---\n')
        self.assertEqual(name, "hx-agent-notes")
        self.assertEqual(version, "2.3")
        self.assertEqual(git_blob_mode("120000"), "120000")
        self.assertIsNone(git_blob_mode("160000"))


class ReviewFixTests(unittest.TestCase):
    def test_shipped_config_loads(self) -> None:
        loaded = load_config(Path(__file__).resolve().parents[1] / "config.yml")
        self.assertEqual(loaded.ignored_repositories, ())
        self.assertIn("bilibili-token", loaded.protected_branches)
        self.assertTrue(loaded.include_private)
        self.assertEqual(loaded.active_within_days, 365)

    def test_quotes_comments_duplicates_and_bool_text(self) -> None:
        loaded = parse_config(
            """
owner: HengXin666
report_repository: HX-Workflows
actor: HengXin666
skill_roots:
  - .agents/skills
protected_branch_globs:
  - "feature/#*"
protected_branches:
  - true
ignored_repositories: []
"""
        )
        self.assertEqual(loaded.protected_branch_globs, ("feature/#*",))
        self.assertEqual(loaded.protected_branches, ("true",))
        self.assertEqual(loaded.ignored_repositories, ())
        with self.assertRaises(ValueError):
            parse_config("owner: a\nowner: b\n")

    def test_private_repo_is_not_scanned(self) -> None:
        report = classify(
            [snapshot("hidden", private=True, branches=(BranchInfo("old", protected=False, ahead=0, behind=1),))],
            config(),
            "2026-10-18 18:00 +0800",
            "",
        )
        self.assertEqual(report.repos_skipped_private, 1)
        self.assertEqual(report.branches_deletable, [])

    def test_recent_private_repo_is_included_and_old_repo_is_not(self) -> None:
        loaded = parse_config(
            """
owner: HengXin666
report_repository: HX-Workflows
actor: HengXin666
include_private: true
active_within_days: 365
skill_roots:
  - .agents/skills
"""
        )
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        old = (now - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
        report = classify(
            [
                snapshot(
                    "hidden",
                    private=True,
                    pushed_at=recent,
                    branches=(BranchInfo("old", protected=False, ahead=0, behind=1),),
                ),
                snapshot(
                    "ancient",
                    pushed_at=old,
                    branches=(BranchInfo("old", protected=False, ahead=0, behind=1),),
                ),
            ],
            loaded,
            "2026-10-18 18:00 +0800",
            "",
        )
        self.assertEqual(report.repos_private, 1)
        self.assertEqual(report.repos_skipped_stale, 1)
        self.assertEqual(report.branch_total, 1)
        report.apply_url = "https://github.com/HengXin666/HX-Workflows/actions/workflows/hygiene-apply.yml"
        body = render_issue(report)
        self.assertIn("包含私有仓库", body)
        self.assertIn(report.apply_url, body)

    def test_private_skill_is_not_copied_out_as_canonical(self) -> None:
        loaded = parse_config(
            """
owner: HengXin666
report_repository: HX-Workflows
actor: HengXin666
include_private: true
active_within_days: 0
skill_roots:
  - .agents/skills
"""
        )
        report = classify(
            [
                snapshot("secret", private=True, skills=(skill("secret", "9.0", "private-tree"),)),
                snapshot("HXLoLi", skills=(skill("HXLoLi", "2.3", "public-tree"),)),
            ],
            loaded,
            "2026-10-18 18:00 +0800",
            "",
        )
        self.assertEqual(report.canonical[0].repo, "HXLoLi")
        self.assertEqual(report.skills_outdated, [])

    def test_missing_active_window_defaults_to_one_year(self) -> None:
        loaded = parse_config(
            """
owner: HengXin666
report_repository: HX-Workflows
actor: HengXin666
skill_roots:
  - .agents/skills
"""
        )
        self.assertEqual(loaded.active_within_days, 365)

    def test_same_unversioned_tree_is_not_a_version_mismatch(self) -> None:
        report = classify(
            [
                snapshot("a", skills=(skill("a", None, "same", committed_at="2026-10-18T00:00:00Z"),)),
                snapshot("b", skills=(skill("b", None, "same", committed_at="2026-10-02T00:00:00Z"),)),
                snapshot("c", skills=(skill("c", None, "other", committed_at="2026-10-01T00:00:00Z"),)),
            ],
            config(),
            "2026-10-18 18:00 +0800",
            "",
        )
        self.assertEqual(report.skills_version_only, [])
        self.assertEqual([item.repo for item in report.skills_unversioned], ["c"])

    def test_newline_cannot_forge_a_checked_item(self) -> None:
        report = classify(
            [
                snapshot(
                    "demo",
                    branches=(
                        BranchInfo(
                            "feat\n- [x] <!--id:branch:HengXin666/demo:evil-->",
                            protected=False,
                            ahead=0,
                            behind=1,
                        ),
                    ),
                )
            ],
            config(),
            "2026-10-18 18:00 +0800",
            "",
        )
        body = render_issue(report)
        self.assertFalse(any(line.startswith("- [x]") for line in body.splitlines()))
        self.assertEqual(selected_ids(body), [])

    def test_badge_uses_real_total_after_truncation(self) -> None:
        snapshots = [
            snapshot(f"repo{index}", branches=(BranchInfo(f"old{index}", protected=False, ahead=0, behind=1),))
            for index in range(3)
        ]
        original = decide_module.SECTION_LIMIT
        decide_module.SECTION_LIMIT = 1
        try:
            report = classify(snapshots, config(), "2026-10-18 18:00 +0800", "")
        finally:
            decide_module.SECTION_LIMIT = original
        self.assertEqual(report.branch_total, 3)
        self.assertEqual(len(report.branches_deletable), 1)
        self.assertEqual(json.loads(badge_files(report)["branches.json"])["message"], "3")

    def test_failed_scan_badge_is_orange(self) -> None:
        report = classify([], config(), "2026-10-18 18:00 +0800", "")
        report.errors.append("比较失败")
        self.assertIn("#d29922", badge_files(report)["branches.svg"])

    def test_incomplete_rescan_does_not_promote_a_lower_copy(self) -> None:
        lower = CanonicalSkill(
            "hx-agent-notes",
            "HengXin666",
            "other",
            ".agents/skills/hx-agent-notes",
            "2.5",
            (2, 5),
            "tree",
            "abc",
            "2026-10-18T00:00:00Z",
            False,
        )
        self.assertIsNone(
            skill_source_after_rescan("HXLoLi", ".agents/skills/hx-agent-notes", "5.0", lower, True)
        )
        self.assertIsNone(
            skill_source_after_rescan("HXLoLi", ".agents/skills/hx-agent-notes", "5.0", lower, False)
        )
        higher = CanonicalSkill(
            "hx-agent-notes",
            "HengXin666",
            "other",
            ".agents/skills/hx-agent-notes",
            "6.0",
            (6, 0),
            "tree",
            "abc",
            "2026-10-18T00:00:00Z",
            False,
        )
        self.assertEqual(
            skill_source_after_rescan("HXLoLi", ".agents/skills/hx-agent-notes", "5.0", higher, False),
            ("other", ".agents/skills/hx-agent-notes"),
        )
        self.assertEqual(
            skill_source_after_rescan("other", ".agents/skills/hx-agent-notes", "2.5", lower, False),
            ("other", ".agents/skills/hx-agent-notes"),
        )

    def test_issue_shrinks_to_the_limit(self) -> None:
        report = classify(
            [
                snapshot(
                    "demo",
                    branches=tuple(
                        BranchInfo(f"branch-{index}-" + ("x" * 80), protected=False, ahead=0, behind=1)
                        for index in range(8)
                    ),
                )
            ],
            config(),
            "2026-10-18 18:00 +0800",
            "",
        )
        body = render_issue(report, limit=1200)
        self.assertLessEqual(len(body), 1200)


class LocalGitTests(unittest.TestCase):
    def test_clone_command_keeps_the_token_out_of_the_arguments(self) -> None:
        token = "secret-token"
        args = clone_args("HengXin666", "HX-Workflows", Path("/tmp/example"))
        self.assertNotIn(token, " ".join(args))
        self.assertIn("https://github.com/HengXin666/HX-Workflows.git", args)
        header = git_env(token)["GIT_CONFIG_VALUE_0"]
        self.assertTrue(header.startswith("AUTHORIZATION: Basic "))
        decoded = base64.b64decode(header.removeprefix("AUTHORIZATION: Basic ")).decode()
        self.assertEqual(decoded, f"x-access-token:{token}")
        self.assertNotIn(token, " ".join(args))

    def test_local_repo_sees_merged_branch_and_skill_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _git(root, ["init", "-b", "main"])
            skill = root / ".agents" / "skills" / "hx-agent-notes" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("---\nname: hx-agent-notes\nmetadata:\n  version: 1.0\n---\n", encoding="utf-8")
            _git(root, ["add", "."])
            _git(root, ["-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-m", "one"])
            _git(root, ["branch", "merged"])
            skill.write_text("---\nname: hx-agent-notes\nmetadata:\n  version: 2.0\n---\n", encoding="utf-8")
            _git(root, ["add", "."])
            _git(root, ["-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-m", "two"])
            _git(root, ["branch", "open"])
            _git(root, ["checkout", "open"])
            skill.write_text("---\nname: hx-agent-notes\nmetadata:\n  version: 3.0\n---\n", encoding="utf-8")
            _git(root, ["add", "."])
            _git(root, ["-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-m", "three"])
            _git(root, ["checkout", "main"])
            local = LocalRepo(root)
            ahead, behind = local.ahead_behind("main", "merged")
            self.assertEqual((ahead, behind), (0, 1))
            open_ahead, _open_behind = local.ahead_behind("main", "open")
            self.assertEqual(open_ahead, 1)
            skills, errors = local.read_skills("HengXin666", "demo", local.tip("main"), (".agents/skills",))
            self.assertEqual(errors, [])
            self.assertEqual(skills[0].version, "2.0")
            self.assertEqual(skills[0].folder, "hx-agent-notes")


def _git(root: Path, args: list[str]) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
