"""Decisions, review text, and status cards."""

from __future__ import annotations

import json
import unittest

from hygiene.config import parse_config
from hygiene.decide import classify
from hygiene.github_api import _frontmatter, branch_ref_path
from hygiene.models import (
    BranchInfo,
    Config,
    RepoSnapshot,
    SkillInstance,
    parse_item_id,
    parse_version,
)
from hygiene.render import badge_files, mark_done, pending_selection, render_issue, selected_ids
from hygiene.trees import GitFile, plan_skill_replacement, safe_branch_name, safe_skill_dir


def config() -> Config:
    return parse_config(
        """
owner: HengXin666
report_repository: HX-Workflows
actor: HengXin666
include_forks: false
include_archived: false
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


if __name__ == "__main__":
    unittest.main()
