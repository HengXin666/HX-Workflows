"""Config validation, visibility inheritance, repo creation, git mirroring and failure paths."""

from __future__ import annotations

import smtplib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mirror.__main__ import notify
from mirror.config import TargetConfig, parse_config
from mirror.git_ops import GitError, count_updated_refs
from mirror.http_client import HttpError
from mirror.notify import load_mail_config, render_email, should_send
from mirror.plan import RemoteRepo, SourceRepo, plan_repo, source_skip_reason
from mirror.report import mask_report, render_markdown, write_reports
from mirror.run import Outcome, RunReport, prepare_targets, retry, run
from mirror.targets import CnbTarget, GiteeTarget, GitLabTarget

BASE = {
    "source": {"owner": "HengXin666"},
    "targets": [{"name": "gitlab", "provider": "gitlab", "owner": "hx", "token_env": "GITLAB_TOKEN"}],
}


def config(**policy: object) -> object:
    """
    Build a minimal mirror config for the decision and run tests
    .agents/notes/implemented/feature/2026-11-28-multi-platform-mirror.md
    """
    data = {**BASE, "policy": policy}
    return parse_config(data)


class FakeClient:
    def __init__(self, responses: dict[tuple[str, str], object] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, str, object]] = []

    def request(self, method: str, path: str, *, body: object = None, params: object = None) -> object:
        self.calls.append((method, path, body))
        value = self.responses.get((method, path))
        if isinstance(value, Exception):
            raise value
        return value

    def get_or_none(self, path: str, params: object = None) -> object:
        self.calls.append(("GET", path, None))
        return self.responses.get(("GET", path))

    def paginate(self, path: str, params: object = None) -> list[dict[str, object]]:
        self.calls.append(("PAGE", path, None))
        value = self.responses.get(("PAGE", path), [])
        return value if isinstance(value, list) else []


class FakeTarget:
    def __init__(self, name: str = "fake", existing: dict[str, RemoteRepo] | None = None, push_url: str = "", can_change: bool = True) -> None:
        self.config = TargetConfig(name=name, provider="gitlab", owner="hx", token_env="X", base_url="https://x", api_url="https://x/api")
        self.repos = dict(existing or {})
        self.url = push_url
        self.can_change = can_change
        self.created: list[tuple[str, bool]] = []
        self.changed: list[tuple[str, bool]] = []

    def prepare(self) -> None:
        return None

    def get(self, name: str) -> RemoteRepo | None:
        return self.repos.get(name)

    def create(self, name: str, private: bool, description: str) -> None:
        self.created.append((name, private))
        self.repos[name] = RemoteRepo(name=name, private=private)

    def set_private(self, name: str, private: bool) -> bool:
        if self.can_change:
            self.changed.append((name, private))
        return self.can_change

    def before_push(self, name: str) -> None:
        return None

    def set_default_branch(self, name: str, branch: str) -> bool:
        return True

    def push_url(self, name: str) -> str:
        return self.url

    def credentials(self) -> tuple[str, str]:
        return "u", "secret-token"


class FakeSource:
    def __init__(self, repos: list[SourceRepo], url: str = "") -> None:
        self.repos = repos
        self.url = url

    def list_repos(self) -> list[SourceRepo]:
        return self.repos

    def clone_url(self, name: str) -> str:
        return self.url

    def credentials(self) -> tuple[str, str]:
        return "x-access-token", "source-token"


class ConfigTest(unittest.TestCase):
    def test_defaults_inherit_visibility_and_create(self) -> None:
        parsed = config()
        self.assertEqual(parsed.policy.visibility, "follow")
        self.assertTrue(parsed.policy.create_missing)
        self.assertEqual(parsed.targets[0].api_url, "https://gitlab.com/api/v4")

    def test_rejects_token_value_in_token_env(self) -> None:
        data = {**BASE, "targets": [{"provider": "gitlab", "owner": "hx", "token_env": "glpat-abc123"}]}
        with self.assertRaisesRegex(ValueError, "环境变量名"):
            parse_config(data)

    def test_rejects_unknown_provider_and_duplicate_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "provider"):
            parse_config({**BASE, "targets": [{"provider": "svn", "owner": "a", "token_env": "A"}]})
        twice = [{"provider": "gitee", "owner": "a", "token_env": "A"}] * 2
        with self.assertRaisesRegex(ValueError, "重复"):
            parse_config({**BASE, "targets": twice})

    def test_self_hosted_forgejo_needs_urls(self) -> None:
        with self.assertRaisesRegex(ValueError, "base_url"):
            parse_config({**BASE, "targets": [{"provider": "forgejo", "owner": "a", "token_env": "A"}]})


class PlanTest(unittest.TestCase):
    def test_missing_repo_is_created_with_source_visibility(self) -> None:
        plan = plan_repo(SourceRepo("a", private=True), None, "follow", True, True)
        self.assertTrue(plan.create)
        self.assertTrue(plan.private)

    def test_existing_repo_visibility_follows_source(self) -> None:
        plan = plan_repo(SourceRepo("a", private=False), RemoteRepo("a", private=True), "follow", True, True)
        self.assertTrue(plan.change_visibility)
        self.assertFalse(plan.private)
        same = plan_repo(SourceRepo("a", private=False), RemoteRepo("a", private=True), "follow", True, False)
        self.assertFalse(same.change_visibility)

    def test_missing_repo_is_skipped_when_creation_disabled(self) -> None:
        plan = plan_repo(SourceRepo("a", private=False), None, "follow", False, True)
        self.assertIn("create_missing", plan.skip_reason)

    def test_source_filters(self) -> None:
        source = config().source
        self.assertIn("fork", source_skip_reason(SourceRepo("a", private=False, fork=True), source, ()))
        self.assertEqual(source_skip_reason(SourceRepo("a", private=True, archived=True), source, ()), "")
        self.assertIn("repo_limit", source_skip_reason(SourceRepo("a", private=False), source, ("b",)))


class TargetAdapterTest(unittest.TestCase):
    def test_gitlab_creates_in_namespace_and_opens_force_push(self) -> None:
        client = FakeClient(
            {
                ("GET", "/user"): {"username": "hx"},
                ("GET", "/namespaces/hx"): {"id": 7},
                ("PAGE", "/projects/hx%2Frepo/protected_branches"): [{"name": "main", "allow_force_push": False}],
                ("PAGE", "/projects/hx%2Frepo/protected_tags"): [{"name": "v*"}],
            }
        )
        target = GitLabTarget(config().targets[0], "t", client)  # type: ignore[arg-type]
        target.prepare()
        target.create("repo", True, "desc")
        target.before_push("repo")
        create = next(call for call in client.calls if call[0] == "POST")
        self.assertEqual(create[2]["namespace_id"], 7)  # type: ignore[index]
        self.assertEqual(create[2]["visibility"], "private")  # type: ignore[index]
        self.assertIn(("PATCH", "/projects/hx%2Frepo/protected_branches/main", {"allow_force_push": True}), client.calls)
        self.assertIn(("DELETE", "/projects/hx%2Frepo/protected_tags/v%2A", None), client.calls)

    def test_gitee_uses_org_endpoint_for_other_owner(self) -> None:
        cfg = parse_config({**BASE, "targets": [{"provider": "gitee", "owner": "team", "token_env": "G"}]}).targets[0]
        client = FakeClient({("GET", "/user"): {"login": "hx"}})
        target = GiteeTarget(cfg, "t", client)  # type: ignore[arg-type]
        target.prepare()
        target.create("repo", False, "x" * 500)
        method, path, body = client.calls[-1]
        self.assertEqual(path, "/orgs/team/repos")
        self.assertFalse(body["private"])  # type: ignore[index]
        self.assertEqual(len(body["description"]), 200)  # type: ignore[index]
        self.assertEqual(target.credentials(), ("hx", "t"))

    def test_cnb_reports_visibility_but_cannot_change_it(self) -> None:
        cfg = parse_config({**BASE, "targets": [{"provider": "cnb", "owner": "grp/sub", "token_env": "C"}]}).targets[0]
        client = FakeClient({("GET", "/grp/sub/repo"): {"visibility_level": "Public"}})
        target = CnbTarget(cfg, "t", client)  # type: ignore[arg-type]
        self.assertEqual(target.get("repo"), RemoteRepo("repo", private=False))
        self.assertFalse(target.set_private("repo", True))
        target.create("new", True, "")
        self.assertEqual(client.calls[-1][1], "/grp/sub/-/repos")
        self.assertEqual(client.calls[-1][2]["visibility"], "private")  # type: ignore[index]


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class RunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def make_source(self) -> Path:
        work = self.root / "work"
        work.mkdir()
        git("init", "-q", "-b", "main", cwd=work)
        (work / "a.txt").write_text("1", encoding="utf-8")
        git("add", ".", cwd=work)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init", cwd=work)
        git("tag", "v1", cwd=work)
        git("branch", "dev", cwd=work)
        return work

    def test_creates_private_repo_and_pushes_branches_and_tags(self) -> None:
        work = self.make_source()
        remote = self.root / "target.git"
        git("init", "-q", "--bare", str(remote), cwd=self.root)
        target = FakeTarget(push_url=str(remote))
        source = FakeSource([SourceRepo("repo", private=True, default_branch="main")], url=str(work))
        report = run(config(), source, targets=[target], cache=self.root / "cache")  # type: ignore[arg-type]
        self.assertFalse(report.failed, render_markdown(report))
        self.assertEqual(target.created, [("repo", True)])
        refs = git("for-each-ref", "--format=%(refname)", cwd=remote).split()
        self.assertEqual(sorted(refs), ["refs/heads/dev", "refs/heads/main", "refs/tags/v1"])
        self.assertEqual(report.outcomes[0].updated_refs, 3)
        self.assertTrue(report.outcomes[0].changed)
        self.assertEqual([repo.name for repo in report.repos], ["repo"])
        self.assertFalse((self.root / "cache" / "repo.git").exists())
        again = run(config(), source, targets=[target], cache=self.root / "cache")  # type: ignore[arg-type]
        self.assertEqual(again.outcomes[0].updated_refs, 0)
        self.assertFalse(again.outcomes[0].changed)

    def test_dry_run_writes_nothing(self) -> None:
        target = FakeTarget(existing={"repo": RemoteRepo("repo", private=True)})
        source = FakeSource([SourceRepo("repo", private=False), SourceRepo("new", private=True)])
        report = run(config(), source, targets=[target], dry_run=True, cache=self.root / "cache")  # type: ignore[arg-type]
        self.assertEqual(target.created, [])
        self.assertEqual(target.changed, [])
        actions = {item.repo: item.actions for item in report.outcomes}
        self.assertIn("可见性改为公开", actions["repo"])
        self.assertIn("新建 (私有)", actions["new"])

    def test_unsupported_visibility_change_is_a_warning(self) -> None:
        work = self.make_source()
        remote = self.root / "target.git"
        git("init", "-q", "--bare", str(remote), cwd=self.root)
        target = FakeTarget(existing={"repo": RemoteRepo("repo", private=False)}, push_url=str(remote), can_change=False)
        source = FakeSource([SourceRepo("repo", private=True)], url=str(work))
        report = run(config(), source, targets=[target], cache=self.root / "cache")  # type: ignore[arg-type]
        self.assertFalse(report.failed)
        self.assertIn("手动", report.outcomes[0].warnings[0])

    def test_push_failure_is_reported_without_leaking_token(self) -> None:
        work = self.make_source()
        target = FakeTarget(push_url=str(self.root / "missing" / "secret-token.git"))
        source = FakeSource([SourceRepo("repo", private=False)], url=str(work))
        with patch("mirror.run.time.sleep"):
            report = run(config(max_retries=1), source, targets=[target], cache=self.root / "cache")  # type: ignore[arg-type]
        self.assertTrue(report.failed)
        self.assertTrue(report.outcomes[0].error)
        self.assertNotIn("secret-token", report.outcomes[0].error)

    def test_missing_token_skips_target_without_failing(self) -> None:
        report = RunReport(dry_run=False)
        ready = prepare_targets(config(), {}, report)  # type: ignore[arg-type]
        self.assertEqual(ready, [])
        self.assertIn("GITLAB_TOKEN", report.target_problems["gitlab"])
        self.assertFalse(report.target_problems_failed)


class ReportTest(unittest.TestCase):
    def test_private_names_are_masked_everywhere(self) -> None:
        report = RunReport(dry_run=False, private_repos={"secret-repo"})
        report.outcomes.append(
            Outcome(repo="secret-repo", target="gitlab", target_repo="secret-repo", error="push https://x/hx/secret-repo.git failed", private=True)
        )
        report.outcomes.append(Outcome(repo="open", target="gitlab", target_repo="open"))
        report.skipped_repos["secret-repo"] = "x"
        with tempfile.TemporaryDirectory() as tmp:
            write_reports(report, Path(tmp), None)
            text = (Path(tmp) / "report.md").read_text(encoding="utf-8") + (Path(tmp) / "report.json").read_text(encoding="utf-8")
        self.assertNotIn("secret-repo", text)
        self.assertIn("私有仓库-", text)
        self.assertIn("open", text)
        self.assertIn("secret-repo", render_markdown(report))
        self.assertEqual(mask_report(RunReport(dry_run=True)).outcomes, [])


MAIL_ENV = {"SMTP_HOST": "smtp.example.test", "SMTP_USERNAME": "bot@example.test", "SMTP_PASSWORD": "pw", "SMTP_TO": "me@example.test"}


def email_report() -> RunReport:
    """
    Build a run with one updated private repo, one unchanged repo, one failure and one skipped platform
    .agents/notes/implemented/feature/2026-11-28-mirror-email-report.md
    """
    report = RunReport(dry_run=False, private_repos={"secret-repo"})
    report.repos = [SourceRepo("secret-repo", private=True), SourceRepo("quiet", private=False), SourceRepo("broken", private=False)]
    report.outcomes += [
        Outcome(repo="secret-repo", target="gitlab", target_repo="secret-repo", actions=["新建 (私有)", "推送 3 个分支/标签"], private=True, updated_refs=3),
        Outcome(repo="quiet", target="gitlab", target_repo="quiet", actions=["推送: 已是最新"]),
        Outcome(repo="broken", target="gitlab", target_repo="broken", error="git push 失败: rejected"),
    ]
    report.target_problems["gitee"] = "跳过: 没有配置 secret GITEE_TOKEN"
    return report


def mail_config() -> object:
    data = {
        **BASE,
        "targets": [
            {"name": "gitlab", "provider": "gitlab", "owner": "loli715548", "token_env": "GITLAB_TOKEN"},
            {"name": "gitee", "provider": "gitee", "owner": "Heng_Xin_666", "token_env": "GITEE_TOKEN"},
        ],
    }
    return parse_config(data)


class FakeSmtp:
    sent: list[object] = []
    fail = False

    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.host = host

    def __enter__(self) -> "FakeSmtp":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def starttls(self) -> None:
        return None

    def login(self, user: str, password: str) -> None:
        if FakeSmtp.fail:
            raise smtplib.SMTPAuthenticationError(535, b"bad")

    def send_message(self, message: object) -> None:
        FakeSmtp.sent.append(message)


class EmailTest(unittest.TestCase):
    def test_email_lists_configured_changed_failed_and_links(self) -> None:
        email = render_email(mail_config(), email_report(), "https://github.com/HengXin666/HX-Workflows/actions/runs/1")  # type: ignore[arg-type]
        self.assertIn("secret-repo", email.html)
        self.assertIn("https://gitlab.com/loli715548/secret-repo", email.html)
        self.assertIn("https://github.com/HengXin666/secret-repo", email.html)
        self.assertIn("https://gitlab.com/loli715548", email.html)
        self.assertIn("今日同步 · 1 个仓库有更新", email.html)
        self.assertIn("已配置的仓库 · 3 个", email.html)
        self.assertIn("git push 失败: rejected", email.html)
        self.assertIn("GITEE_TOKEN", email.html)
        self.assertIn("1 项失败", email.subject)
        self.assertIn("今日更新 1 个", email.subject)
        self.assertIn("actions/runs/1", email.text)
        self.assertIn("color-scheme:dark", email.html)
        self.assertIn("📁</span><a href=\"https://gitlab.com/loli715548\"", email.html)
        self.assertIn("max-width:560px", email.html)
        changed_section = email.text.split("今日有更新")[1].split("已配置的仓库")[0]
        self.assertNotIn("quiet", changed_section)

    def test_quiet_day_says_everything_is_current(self) -> None:
        report = RunReport(dry_run=False, repos=[SourceRepo("quiet", private=False)])
        report.outcomes.append(Outcome(repo="quiet", target="gitlab", target_repo="quiet", actions=["推送: 已是最新"]))
        email = render_email(mail_config(), report)  # type: ignore[arg-type]
        self.assertIn("全部成功", email.subject)
        self.assertIn("都已是最新", email.html)

    def test_mail_config_and_send_modes(self) -> None:
        self.assertIsNone(load_mail_config({"SMTP_HOST": "x"}))
        mail = load_mail_config({**MAIL_ENV, "MIRROR_MAIL_TO": "a@x.test; b@x.test"})
        assert mail is not None
        self.assertEqual(mail.mail_to, ("a@x.test", "b@x.test"))
        self.assertTrue(mail.use_ssl)
        quiet = RunReport(dry_run=False)
        self.assertFalse(should_send("failure", quiet))
        self.assertTrue(should_send("failure", email_report()))
        self.assertFalse(should_send("never", email_report()))
        with self.assertRaises(ValueError):
            parse_config({**BASE, "notify": {"email": "sometimes"}})

    def test_notify_sends_skips_and_reports_failure(self) -> None:
        FakeSmtp.sent, FakeSmtp.fail = [], False
        cfg = mail_config()
        self.assertFalse(notify(cfg, email_report(), MAIL_ENV, FakeSmtp))  # type: ignore[arg-type]
        self.assertEqual(len(FakeSmtp.sent), 1)
        self.assertFalse(notify(cfg, email_report(), {}, FakeSmtp))  # type: ignore[arg-type]
        self.assertEqual(len(FakeSmtp.sent), 1)
        FakeSmtp.fail = True
        self.assertTrue(notify(cfg, email_report(), MAIL_ENV, FakeSmtp))  # type: ignore[arg-type]

    def test_porcelain_counts_only_changed_refs(self) -> None:
        output = "To x\n*\trefs/heads/main:refs/heads/main\t[new branch]\n=\trefs/tags/v1:refs/tags/v1\t[up to date]\n+\trefs/heads/dev:refs/heads/dev\tabc...def (forced update)\nDone\n"
        self.assertEqual(count_updated_refs(output), 2)


class RetryTest(unittest.TestCase):
    def test_retries_transient_then_succeeds(self) -> None:
        calls: list[int] = []

        def flaky() -> str:
            calls.append(1)
            if len(calls) < 3:
                raise GitError("network")
            return "ok"

        with patch("mirror.run.time.sleep"):
            self.assertEqual(retry(flaky, 3), "ok")
        self.assertEqual(len(calls), 3)

    def test_does_not_retry_client_errors(self) -> None:
        calls: list[int] = []

        def denied() -> None:
            calls.append(1)
            raise HttpError(403, "forbidden")

        with self.assertRaises(HttpError):
            retry(denied, 3)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
