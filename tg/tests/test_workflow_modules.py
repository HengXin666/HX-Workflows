from __future__ import annotations

import asyncio
import json
import os
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import local_web_accounts  # noqa: E402
import local_web_api  # noqa: E402
import local_web_bots  # noqa: E402
import local_web_flows  # noqa: E402
import local_web_github  # noqa: E402
import local_web_login  # noqa: E402
import local_web_resource_groups  # noqa: E402
import local_web_server  # noqa: E402
import local_web_telegram  # noqa: E402
import sign_from_config  # noqa: E402
import signer_actions  # noqa: E402
import signer_core  # noqa: E402
import signer_jobs  # noqa: E402


class SessionStorageTests(unittest.TestCase):
    def test_session_write_is_atomic_private_and_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.txt"
            local_web_accounts.append_session(path, "session-a")
            local_web_accounts.append_session(path, "session-a")
            local_web_accounts.append_session(path, "session-b")
            self.assertEqual(local_web_accounts.read_lines(path), ["session-a", "session-b"])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*")), [])

    def test_corrupt_or_wrong_shape_account_cache_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sessions = Path(directory) / "sessions.txt"
            cache = local_web_accounts.account_cache_path(sessions)
            cache.write_text("not-json", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "JSON 损坏"):
                local_web_accounts.load_account_cache(sessions)
            cache.write_text(json.dumps({"session": "secret"}), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "对象数组"):
                local_web_accounts.load_account_cache(sessions)


class WorkflowCompilationTests(unittest.TestCase):
    def test_workflow_storage_round_trip_and_delete(self) -> None:
        flow = {
            "id": "daily-test",
            "nodes": [{"id": "task", "data": {"kind": "task", "taskId": "daily-test"}}],
            "edges": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workflows.json"
            saved = local_web_flows.save_workflow(path, flow.copy())
            self.assertEqual(local_web_flows.read_workflows(path)[0]["id"], saved["id"])
            self.assertTrue(local_web_flows.delete_workflow(path, "daily-test"))
            self.assertEqual(local_web_flows.read_workflows(path), [])

    def test_compatibility_entrypoint_reexports_split_functions(self) -> None:
        self.assertIs(local_web_api.flow_to_signins, local_web_flows.flow_to_signins)
        self.assertIs(local_web_api.parse_page_limit, local_web_server.parse_page_limit)
        self.assertIs(sign_from_config.text_matches, signer_core.text_matches)

    def test_continuous_request_and_resource_catalog_compile_to_actions(self) -> None:
        flow = {
            "id": "resources",
            "nodes": [
                {"id": "task", "data": {"kind": "task", "taskId": "resources"}},
                {
                    "id": "request",
                    "data": {
                        "kind": "continuous_request",
                        "requestMode": "button",
                        "requestText": "随机获取资源",
                        "repeatCount": 8,
                        "intervalSeconds": 2.5,
                        "responseWaitSeconds": 4,
                        "responseLimit": 12,
                        "maxFailures": 3,
                    },
                },
                {"id": "catalog", "data": {"kind": "resource_catalog"}},
            ],
            "edges": [
                {"source": "task", "target": "request"},
                {"source": "request", "target": "catalog"},
            ],
        }
        actions = local_web_flows.flow_to_signins(flow)["jobs"][0]["actions"]
        self.assertEqual(
            actions[0],
            {
                "type": "continuous_request",
                "node_id": "request",
                "request_mode": "button",
                "request_text": "随机获取资源",
                "repeat_count": 8,
                "interval_seconds": 2.5,
                "response_wait_seconds": 4.0,
                "response_limit": 12,
                "save_as": "resource_responses",
                "links_save_as": "resource_records",
                "max_failures": 3,
            },
        )
        self.assertEqual(
            actions[1],
            {
                "type": "resource_catalog",
                "node_id": "catalog",
                "source": "resource_records",
                "dedupe_store": "sessions/resource_catalog.sqlite3",
                "export_path": "sessions/resource_links.json",
            },
        )

    def test_preview_catalog_compiles_from_continuous_responses(self) -> None:
        flow = {
            "id": "preview-index",
            "nodes": [
                {"id": "task", "data": {"kind": "task", "taskId": "preview-index"}},
                {
                    "id": "request",
                    "data": {
                        "kind": "continuous_request",
                        "requestMode": "message",
                        "requestText": "♻️ 随机",
                        "repeatCount": 3,
                        "intervalSeconds": 2,
                        "responseWaitSeconds": 2,
                        "responseLimit": 10,
                        "maxFailures": 1,
                        "saveAs": "preview_responses",
                        "linksSaveAs": "unused_links",
                    },
                },
                {
                    "id": "previews",
                    "data": {
                        "kind": "preview_catalog",
                        "callbackPrefix": "unlock:",
                    },
                },
            ],
            "edges": [
                {"source": "task", "target": "request"},
                {"source": "request", "target": "previews"},
            ],
        }
        actions = local_web_flows.flow_to_signins(flow)["jobs"][0]["actions"]
        self.assertEqual(
            actions[1],
            {
                "type": "preview_catalog",
                "node_id": "previews",
                "source": "preview_responses",
                "callback_prefix": "unlock:",
                "dedupe_store": "sessions/resource_previews.sqlite3",
                "export_path": "sessions/resource_previews.json",
            },
        )

    def test_generated_github_cache_persists_preview_catalog(self) -> None:
        workflow = local_web_flows.flow_to_github_actions({"id": "preview-cache"})
        cache = next(
            step
            for step in workflow["jobs"]["tg"]["steps"]
            if step.get("uses") == "actions/cache@v4"
        )
        paths = set(str(cache["with"]["path"]).splitlines())
        self.assertTrue(
            {
                "components/HX-Workflows/tg/sessions/resource_catalog.sqlite3",
                "components/HX-Workflows/tg/sessions/resource_catalog.sqlite3-*",
                "components/HX-Workflows/tg/sessions/resource_links.json",
                "components/HX-Workflows/tg/sessions/resource_previews.sqlite3",
                "components/HX-Workflows/tg/sessions/resource_previews.sqlite3-*",
                "components/HX-Workflows/tg/sessions/resource_previews.json",
            }.issubset(paths)
        )
        checked_in_workflow = local_web_accounts.GITHUB_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("tg/sessions/resource_previews.sqlite3", checked_in_workflow)
        self.assertIn("tg/sessions/resource_previews.sqlite3-*", checked_in_workflow)
        self.assertIn("tg/sessions/resource_previews.json", checked_in_workflow)

    @staticmethod
    def record(resource_key: str = "preview-key") -> dict[str, object]:
        return {
            "resource_key": resource_key,
            "callback_data": f"unlock:{resource_key}",
            "button_text": "解锁资源",
            "description": "资源预览",
            "summary_text": "图片: 2个 | 视频: 3个 | 文件: 4个",
            "photos": 2,
            "videos": 3,
            "files": 4,
            "unlock_cost": 5,
            "source_peer": "@preview_bot",
            "source_message_id": 41,
            "message_link": "https://t.me/preview_bot/41",
            "preview_message_ids": [38, 39, 40],
            "preview_message_links": [
                "https://t.me/preview_bot/38",
                "https://t.me/preview_bot/39",
                "https://t.me/preview_bot/40",
            ],
            "preview_media": [
                {"message_id": 38, "kind": "photo", "url": "https://t.me/preview_bot/38"},
                {"message_id": 39, "kind": "video", "url": "https://t.me/preview_bot/39"},
            ],
            "account_index": 7,
            "account_id": "alt-7",
            "round": 9,
        }

    def test_fresh_schema_column_order_and_named_insert_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "previews.sqlite3"
            conn = signer_core.init_resource_preview_db(path)
            try:
                columns = [row[1] for row in conn.execute("PRAGMA table_info(resource_previews)")]
                self.assertEqual(
                    columns,
                    [
                        "resource_key",
                        "callback_data",
                        "button_text",
                        "description",
                        "summary_text",
                        "photos",
                        "videos",
                        "files",
                        "unlock_cost",
                        "source_peer",
                        "source_message_id",
                        "message_link",
                        "preview_message_ids",
                        "preview_message_links",
                        "preview_media",
                        "first_account_index",
                        "first_account_id",
                        "first_round",
                        "seen_count",
                        "created_at",
                        "last_seen_at",
                    ],
                )
                signer_core.catalog_resource_preview(conn, self.record())
                row = conn.execute(
                    """
                    SELECT source_peer, source_message_id, message_link,
                           first_account_index, first_account_id, first_round,
                           preview_message_ids
                    FROM resource_previews WHERE resource_key = ?
                    """,
                    ("preview-key",),
                ).fetchone()
                self.assertEqual(
                    tuple(row),
                    (
                        "@preview_bot",
                        41,
                        "https://t.me/preview_bot/41",
                        7,
                        "alt-7",
                        9,
                        "[38,39,40]",
                    ),
                )
            finally:
                conn.close()
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_legacy_preview_schema_is_upgraded_without_losing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "previews.sqlite3"
            legacy = sqlite3.connect(path)
            legacy.executescript(
                """
                CREATE TABLE resource_previews (
                    resource_key TEXT PRIMARY KEY,
                    description TEXT,
                    photos INTEGER,
                    source_peer TEXT,
                    source_message_id INTEGER,
                    message_link TEXT,
                    account_index INTEGER,
                    request_round INTEGER,
                    seen_count INTEGER,
                    created_at TEXT,
                    last_seen_at TEXT
                );
                CREATE TABLE resource_preview_provenance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    resource_key TEXT NOT NULL,
                    source_peer TEXT,
                    source_message_id INTEGER,
                    message_link TEXT,
                    first_account_index INTEGER,
                    "round" INTEGER,
                    seen_at TEXT
                );
                INSERT INTO resource_previews
                    (resource_key, description, photos, source_peer, source_message_id,
                     message_link, account_index, request_round, seen_count, created_at, last_seen_at)
                VALUES
                    ('legacy-key', '旧描述', 2, '@old_bot', 4,
                     'https://t.me/old_bot/4', 3, 2, 1, '2026-01-01', '2026-01-01');
                INSERT INTO resource_preview_provenance
                    (resource_key, source_peer, source_message_id, message_link,
                     first_account_index, "round", seen_at)
                VALUES
                    ('legacy-key', '@old_bot', 4, 'https://t.me/old_bot/4', 3, 2, '2026-01-01');
                """
            )
            legacy.commit()
            legacy.close()

            conn = signer_core.init_resource_preview_db(path)
            try:
                stored = signer_core.catalog_resource_preview(conn, self.record("legacy-key"))
                previews = signer_core.list_resource_previews(conn)
            finally:
                conn.close()

        self.assertEqual(stored["seen_count"], 2)
        self.assertEqual(len(previews), 1)
        self.assertEqual(previews[0]["resource_key"], "legacy-key")
        self.assertEqual(previews[0]["callback_data"], "unlock:legacy-key")
        self.assertEqual(previews[0]["description"], "旧描述")
        self.assertEqual(previews[0]["videos"], 3)
        self.assertEqual(previews[0]["preview_message_ids"], [38, 39, 40])
        self.assertEqual(
            previews[0]["provenance"][-1]["preview_media"],
            [
                {"message_id": 38, "kind": "photo", "url": "https://t.me/preview_bot/38"},
                {"message_id": 39, "kind": "video", "url": "https://t.me/preview_bot/39"},
            ],
        )
        self.assertEqual(
            [
                (item["account_index"], item["account_id"], item["round"])
                for item in previews[0]["provenance"]
            ],
            [(3, "", 2), (7, "alt-7", 9)],
        )

    def test_continuous_request_compile_rejects_out_of_range_values(self) -> None:
        base = {
            "kind": "continuous_request",
            "requestMode": "message",
            "requestText": "随机获取资源",
            "repeatCount": 3,
            "intervalSeconds": 1,
            "responseWaitSeconds": 2,
            "responseLimit": 5,
            "maxFailures": 2,
        }
        invalid = {
            "requestMode": "http",
            "requestText": "",
            "repeatCount": 1001,
            "intervalSeconds": -1,
            "responseWaitSeconds": 301,
            "responseLimit": 0,
            "maxFailures": 4,
        }
        for key, value in invalid.items():
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                data = {**base, key: value}
                local_web_flows.flow_to_signins(
                    {
                        "id": "invalid",
                        "nodes": [
                            {"id": "task", "data": {"kind": "task"}},
                            {"id": "request", "data": data},
                        ],
                        "edges": [{"source": "task", "target": "request"}],
                    }
                )
        with self.assertRaisesRegex(ValueError, "repeatCount"):
            local_web_flows._compile_continuous_request({**base, "repeatCount": "3"}, "request")

    def test_get_loli_av_inputs_are_inferred_from_the_connected_parse_node(self) -> None:
        flow = {
            "id": "get-loli-av",
            "nodes": [
                {"id": "task", "data": {"kind": "task", "taskId": "get-loli"}},
                {"id": "send", "data": {"kind": "send", "text": "♻️ 随机"}},
                {
                    "id": "parse",
                    "data": {"kind": "parse", "extract": "links", "saveAs": "last_links"},
                },
                {
                    "id": "catalog",
                    "data": {"kind": "resource_catalog", "source": "resource_records"},
                },
                {
                    "id": "pool",
                    "data": {
                        "kind": "resource_pool",
                        "poolPeer": "https://t.me/+resource",
                        "source": "resource_responses",
                    },
                },
            ],
            "edges": [
                {"source": "task", "target": "send"},
                {"source": "send", "target": "parse"},
                {"source": "parse", "target": "catalog"},
                {"source": "catalog", "target": "pool"},
            ],
        }
        actions = local_web_flows.flow_to_signins(flow)["jobs"][0]["actions"]
        catalog = next(action for action in actions if action["type"] == "resource_catalog")
        pool = next(action for action in actions if action["type"] == "resource_pool")
        self.assertEqual(catalog["source"], "last_links")
        self.assertEqual(pool["source"], "last_parse")

    def test_custom_resource_input_remains_explicit(self) -> None:
        self.assertEqual(
            local_web_flows._resolve_node_input(
                {"inputMode": "variable", "source": "custom_links"},
                "last_links",
                "resource_records",
                {"resource_records", "last_links"},
            ),
            "custom_links",
        )

    def test_parse_limit_is_preserved_in_compiled_action(self) -> None:
        compiled = local_web_flows.flow_to_signins(
            {
                "id": "parse-limit",
                "nodes": [
                    {"id": "task", "data": {"kind": "task"}},
                    {"id": "parse", "data": {"kind": "parse", "limit": 17}},
                ],
                "edges": [{"source": "task", "target": "parse"}],
            }
        )
        self.assertEqual(compiled["jobs"][0]["actions"][0]["limit"], 17)


class SignerJobConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_job_collect_respects_account_concurrency(self) -> None:
        running = 0
        peak = 0

        async def fake_run_one_account(job, _defaults, _session, account_index):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1
            return signer_core.RunResult(
                job_id=str(job["id"]),
                account_index=account_index,
                status="success",
                matched=True,
                summary="ok",
                messages=[],
                node_logs={},
            )

        config = {
            "defaults": {"account_concurrency": 2},
            "jobs": [{"id": "parallel", "enabled": True, "peer": "@bot", "actions": [{"type": "send", "text": "hi"}]}],
        }
        with patch.object(signer_jobs, "read_secret_lines", return_value=["a", "b", "c", "d"]):
            with patch.object(signer_jobs, "run_one_account", side_effect=fake_run_one_account):
                results, code = await signer_jobs.run_job_collect(config, "parallel")

        self.assertEqual(code, 0)
        self.assertEqual([result.account_index for result in results], [1, 2, 3, 4])
        self.assertEqual(peak, 2)

    async def test_invalid_account_concurrency_is_rejected(self) -> None:
        config = {
            "defaults": {"account_concurrency": 99},
            "jobs": [{"id": "parallel", "enabled": True, "peer": "@bot", "actions": [{"type": "send", "text": "hi"}]}],
        }
        with patch.object(signer_jobs, "read_secret_lines", return_value=["a"]):
            with self.assertRaisesRegex(RuntimeError, "account_concurrency"):
                await signer_jobs.run_job_collect(config, "parallel")


class ConfigurationRuleTests(unittest.TestCase):
    def test_invalid_config_shapes_are_rejected_with_domain_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signins.yml"
            path.write_text("defaults: []\njobs: {}\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "defaults"):
                signer_core.load_yaml(path)

    def test_api_credentials_require_a_positive_integer_id(self) -> None:
        with patch.dict(os.environ, {"TEST_ID": "abc", "TEST_HASH": "hash"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "正整数"):
                signer_core.telegram_api_config("TEST_ID", "TEST_HASH")

    def test_filter_rejection_precedes_inclusion(self) -> None:
        filters = {
            "include": {"contains": ["成功"]},
            "exclude": {"contains": ["已领取"]},
        }
        self.assertTrue(signer_core.text_matches("签到成功", filters))
        self.assertFalse(signer_core.text_matches("签到成功，今日已领取", filters))

    def test_resource_pool_fingerprint_is_persisted_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            conn = signer_core.init_resource_pool_db(Path(directory) / "seen.sqlite3")
            try:
                self.assertFalse(signer_core.resource_seen(conn, "photo:1"))
                signer_core.mark_resource_seen(conn, "photo:1", "photo", "pool", "source", 1, 1)
                signer_core.mark_resource_seen(conn, "photo:1", "photo", "pool", "source", 1, 1)
                self.assertTrue(signer_core.resource_seen(conn, "photo:1"))
                count = conn.execute("SELECT COUNT(*) FROM resource_pool_seen").fetchone()[0]
                self.assertEqual(count, 1)
            finally:
                conn.close()

    def test_private_bot_message_link_uses_telegram_deep_link(self) -> None:
        bot = SimpleNamespace(id=42, username="resource_bot", bot=True, first_name="Resource")
        self.assertEqual(
            signer_core.telegram_message_link(bot, 99),
            "tg://openmessage?user_id=42&message_id=99",
        )


class LocalApiBoundaryTests(unittest.TestCase):
    def test_page_limit_is_bounded(self) -> None:
        self.assertEqual(local_web_server.parse_page_limit("40"), 40)
        for value in ("0", "101", "invalid"):
            with self.assertRaises(ValueError):
                local_web_server.parse_page_limit(value)

    def test_message_ids_and_signed_media_urls_are_bounded(self) -> None:
        self.assertEqual(local_web_server.parse_message_ids("9, 7,9"), [9, 7])
        for value in ("0", "bad", ",".join(str(index) for index in range(1, 22))):
            with self.subTest(value=value), self.assertRaises(ValueError):
                local_web_server.parse_message_ids(value)

        messages = [
            {
                "id": "9",
                "media": {"kind": "video", "has_thumbnail": True, "media_id": "video:2:987"},
            }
        ]
        local_web_server.attach_message_media_urls(
            messages,
            "alt-1",
            "@preview_bot",
            "http://127.0.0.1:2334",
        )
        media = messages[0]["media"]
        self.assertIsInstance(media, dict)
        thumbnail = urllib.parse.urlparse(media["thumbnail_url"])
        original = urllib.parse.urlparse(media["content_url"])
        thumbnail_query = urllib.parse.parse_qs(thumbnail.query)
        original_query = urllib.parse.parse_qs(original.query)
        self.assertEqual(thumbnail_query["variant"], ["thumbnail"])
        self.assertEqual(original_query["variant"], ["original"])
        self.assertNotEqual(thumbnail_query["token"], original_query["token"])
        self.assertNotIn("account", thumbnail_query)
        self.assertNotIn("message_id", thumbnail_query)
        self.assertEqual(
            thumbnail_query["token"][0],
            local_web_server._media_asset_signature(thumbnail_query["asset"][0], "thumbnail"),
        )

        duplicate = [{"id": "99", "media": {"kind": "video", "has_thumbnail": True, "media_id": "video:2:987"}}]
        local_web_server.attach_message_media_urls(duplicate, "alt-2", "@other_bot", "")
        self.assertEqual(duplicate[0]["media"]["thumbnail_url"], media["thumbnail_url"])
        self.assertEqual(duplicate[0]["media"]["content_url"], media["content_url"])
        self.assertEqual(len(local_web_server.media_sources_for_asset(thumbnail_query["asset"][0])), 2)

    def test_telegram_requests_share_a_lock_only_within_one_account(self) -> None:
        first = local_web_server.telegram_account_lock("alt-lock-test-1")
        self.assertIs(first, local_web_server.telegram_account_lock("alt-lock-test-1"))
        self.assertIsNot(first, local_web_server.telegram_account_lock("alt-lock-test-2"))

    def test_resource_previews_are_read_from_export_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "resource_previews.json"
            missing = local_web_server.read_resource_previews(path)
            self.assertEqual(missing["previews"], [])
            self.assertIsNone(missing["updated_at"])

            path.write_text(json.dumps([{"resource_key": "a"}, "skip"], ensure_ascii=False), encoding="utf-8")
            payload = local_web_server.read_resource_previews(path)
            self.assertEqual(payload["previews"], [{"resource_key": "a"}])
            self.assertEqual(payload["path"], str(path))
            self.assertIsInstance(payload["updated_at"], float)

            path.write_text(json.dumps({"resource_key": "bad"}, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "数组"):
                local_web_server.read_resource_previews(path)

    def test_resource_preview_files_include_configured_and_detected_indexes_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = root / "sessions"
            sessions.mkdir()
            default = sessions / "resource_previews.json"
            detected = sessions / "second_catalog.json"
            unrelated = sessions / "accounts.json"
            workflows = sessions / "web_workflows.json"
            default.write_text(json.dumps([{"resource_key": "default"}]), encoding="utf-8")
            detected.write_text(json.dumps([{"resource_key": "second"}]), encoding="utf-8")
            unrelated.write_text(json.dumps([{"id": "account"}]), encoding="utf-8")
            workflows.write_text(
                json.dumps(
                    [
                        {
                            "nodes": [
                                {
                                    "data": {
                                        "kind": "preview_catalog",
                                        "exportPath": "sessions/not_generated.json",
                                    }
                                }
                            ]
                        }
                    ]
                ),
                encoding="utf-8",
            )

            files = local_web_server.resource_preview_files(
                workflows,
                root=root,
                default_path=default,
            )
            paths = {item["path"] for item in files}
            self.assertIn(str(default.resolve()), paths)
            self.assertIn(str(detected.resolve()), paths)
            self.assertIn(str((sessions / "not_generated.json").resolve()), paths)
            self.assertNotIn(str(unrelated.resolve()), paths)
            self.assertEqual(
                local_web_server.select_resource_preview_file(
                    files,
                    str(detected.resolve()),
                    default_path=default,
                ),
                detected.resolve(),
            )
            with self.assertRaisesRegex(ValueError, "允许的文件列表"):
                local_web_server.select_resource_preview_file(
                    files,
                    str(unrelated.resolve()),
                    default_path=default,
                )

    def test_2fa_password_is_kept_out_of_public_job_state(self) -> None:
        job = local_web_login.create_login_job("2fa-test", "alt")
        job.status = "needs_password"
        updated = local_web_login.submit_login_password(job.id, "cloud-password")
        self.assertEqual(updated.status, "verifying_password")
        self.assertNotIn("cloud-password", repr(updated))
        with local_web_login.login_lock:
            local_web_login.passwords.pop(job.id, None)
            local_web_login.password_events.pop(job.id, None)
            local_web_login.login_jobs.pop(job.id, None)

    def test_run_flow_keeps_account_ids_aligned_with_sessions(self) -> None:
        captured_env: dict[str, str] = {}

        class Process:
            stdout = iter(())

            def wait(self, timeout: int) -> int:
                self.timeout = timeout
                return 0

        def start_process(*_args: object, **kwargs: object) -> Process:
            captured_env.update(dict(kwargs["env"]))
            return Process()

        available = [
            ({"id": "main-1", "role": "main"}, "main-session"),
            ({"id": "alt-1", "role": "alt"}, "alt-one-session"),
            ({"id": "alt-2", "role": "alt"}, "alt-two-session"),
        ]
        job_id = "account-alignment"
        local_web_server.run_jobs[job_id] = local_web_server.RunJob(id=job_id)
        try:
            with tempfile.TemporaryDirectory() as directory, patch.object(
                local_web_server, "sessions_with_accounts", return_value=available
            ), patch.object(
                local_web_server, "flow_to_signins", return_value={"jobs": []}
            ), patch.object(
                local_web_server.subprocess, "Popen", side_effect=start_process
            ):
                root = Path(directory)
                local_web_server.run_flow(
                    job_id,
                    {"id": "alignment", "nodes": [], "edges": []},
                    root / "alts.txt",
                    root / "main.txt",
                    account_ids=["alt-2", "main-1"],
                    account_concurrency=3,
                )
        finally:
            local_web_server.run_jobs.pop(job_id, None)

        self.assertEqual(captured_env["TG_SESSION_STRINGS"], "main-session\nalt-two-session")
        self.assertEqual(captured_env["TG_ACCOUNT_IDS"], "main-1\nalt-2")
        self.assertEqual(captured_env["TG_ACCOUNT_CONCURRENCY"], "3")

    def test_run_flow_cancel_state_wins_over_process_exit_code(self) -> None:
        job_id = "cancel-wins"

        class Output:
            emitted = False

            def __iter__(self) -> Output:
                return self

            def __next__(self) -> str:
                if self.emitted:
                    raise StopIteration
                self.emitted = True
                with local_web_server.jobs_lock:
                    job = local_web_server.run_jobs[job_id]
                    job.cancel_requested = True
                    job.status = "cancelling"
                return "worker stopped\n"

        class Process:
            stdout = Output()

            def wait(self, timeout: int | float) -> int:
                del timeout
                return 143

        local_web_server.run_jobs[job_id] = local_web_server.RunJob(id=job_id)
        try:
            with tempfile.TemporaryDirectory() as directory, patch.object(
                local_web_server, "sessions_with_accounts", return_value=[]
            ), patch.object(
                local_web_server, "flow_to_signins", return_value={"jobs": []}
            ), patch.object(
                local_web_server.subprocess, "Popen", return_value=Process()
            ):
                root = Path(directory)
                local_web_server.run_flow(
                    job_id,
                    {"id": "cancel", "nodes": [], "edges": []},
                    root / "alts.txt",
                    root / "main.txt",
                )
            job = local_web_server.run_jobs[job_id]
            self.assertEqual(job.status, "cancelled")
            self.assertIsNone(job.error)
            self.assertIsNone(job.failed_node)
            self.assertNotIn(job_id, local_web_server.run_processes)
        finally:
            local_web_server.run_processes.pop(job_id, None)
            local_web_server.run_jobs.pop(job_id, None)

    def test_run_flow_setup_failure_reaches_an_error_state(self) -> None:
        job_id = "setup-failure"
        local_web_server.run_jobs[job_id] = local_web_server.RunJob(id=job_id)
        try:
            with tempfile.TemporaryDirectory() as directory, patch.object(
                local_web_server, "flow_to_signins", return_value={"jobs": []}
            ), patch.object(
                local_web_server, "sessions_with_accounts", side_effect=RuntimeError("account cache failed")
            ), patch.object(
                local_web_server.subprocess, "Popen"
            ) as start_process:
                root = Path(directory)
                local_web_server.run_flow(
                    job_id,
                    {"id": "setup", "nodes": [{"id": "setup-node", "data": {}}], "edges": []},
                    root / "alts.txt",
                    root / "main.txt",
                )
            job = local_web_server.run_jobs[job_id]
            self.assertEqual(job.status, "error")
            self.assertEqual(job.failed_node, "setup-node")
            self.assertIn("account cache failed", str(job.error))
            start_process.assert_not_called()
        finally:
            local_web_server.run_processes.pop(job_id, None)
            local_web_server.run_jobs.pop(job_id, None)

    def test_cancel_is_idempotent_and_terminates_registered_process_once(self) -> None:
        job_id = "cancel-idempotent"
        process = SimpleNamespace()
        local_web_server.run_jobs[job_id] = local_web_server.RunJob(id=job_id)
        local_web_server.run_processes[job_id] = process
        try:
            with patch.object(local_web_server, "_terminate_process_group") as terminate:
                first, first_status = local_web_server.cancel_run_job(job_id)
                second, second_status = local_web_server.cancel_run_job(job_id)
            self.assertEqual((first_status, second_status), (HTTPStatus.OK, HTTPStatus.OK))
            self.assertEqual(first["status"], "cancelling")
            self.assertTrue(first["cancel_requested"])
            self.assertEqual(second["status"], "cancelling")
            terminate.assert_called_once_with(process)
        finally:
            local_web_server.run_processes.pop(job_id, None)
            local_web_server.run_jobs.pop(job_id, None)

    def test_process_group_cancel_escalates_after_grace_period(self) -> None:
        class Process:
            pid = 4321
            waits = 0

            def poll(self) -> None:
                return None

            def wait(self, timeout: int | float) -> int:
                self.waits += 1
                if self.waits == 1:
                    raise subprocess.TimeoutExpired("uv", timeout)
                return -9

        process = Process()
        with patch.object(local_web_server.os, "killpg") as kill_group, patch.object(
            local_web_server, "_wait_for_process_group_exit", return_value=False
        ):
            local_web_server._terminate_process_group(process)  # type: ignore[arg-type]
        self.assertEqual(
            kill_group.call_args_list,
            [
                call(4321, signal.SIGTERM),
                call(4321, signal.SIGKILL),
            ],
        )

    def test_process_group_cancel_kills_children_after_leader_exits(self) -> None:
        class Process:
            pid = 4321

            def poll(self) -> int:
                return 0

            def wait(self, timeout: int | float) -> int:
                del timeout
                return 0

        process = Process()
        with patch.object(local_web_server, "RUN_CANCEL_GRACE_SECONDS", 0.0), patch.object(
            local_web_server.os, "killpg"
        ) as kill_group:
            local_web_server._terminate_process_group(process)  # type: ignore[arg-type]
        self.assertEqual(
            kill_group.call_args_list,
            [
                call(4321, signal.SIGTERM),
                call(4321, 0),
                call(4321, signal.SIGKILL),
            ],
        )


class ResourceGroupTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_creates_group_and_only_missing_accounts_join(self) -> None:
        channel_type = type("Channel", (), {})
        group = channel_type()
        group.id = 77
        imported: list[str] = []

        class Client:
            def __init__(self, session: str) -> None:
                self.session = session

            async def connect(self) -> None:
                return None

            async def disconnect(self) -> None:
                return None

            async def __call__(self, request: object) -> object:
                request_name = request.__class__.__name__
                if request_name == "CreateChannelRequest":
                    return SimpleNamespace(chats=[group])
                if request_name == "ExportChatInviteRequest":
                    return SimpleNamespace(link="https://t.me/+resource-invite")
                if request_name == "CheckChatInviteRequest":
                    return SimpleNamespace(chat=group) if self.session == "existing" else SimpleNamespace()
                if request_name == "ImportChatInviteRequest":
                    imported.append(self.session)
                    return SimpleNamespace(chats=[group])
                raise AssertionError(request_name)

        def client_factory(session: str, *_args, **_kwargs) -> Client:
            return Client(session)

        accounts = [
            ({"id": "main-1", "name": "Main"}, "owner"),
            ({"id": "alt-1", "name": "Existing"}, "existing"),
            ({"id": "alt-2", "name": "Missing"}, "missing"),
        ]
        with patch.object(local_web_resource_groups, "StringSession", side_effect=lambda value: value):
            payload = await local_web_resource_groups.synchronize_resource_group(
                "Resources",
                "main-1",
                accounts,
                123,
                "hash",
                client_factory=client_factory,
                join_delay=0,
            )

        self.assertEqual(payload["group_id"], "-10077")
        self.assertEqual(
            [item["status"] for item in payload["results"]],
            ["owner", "already", "joined"],
        )
        self.assertEqual(imported, ["missing"])

    async def test_resource_group_disconnects_when_connection_fails(self) -> None:
        class Client:
            disconnected = False

            async def connect(self) -> None:
                raise TimeoutError("connect timeout")

            async def disconnect(self) -> None:
                self.disconnected = True

        client = Client()
        with patch.object(local_web_resource_groups, "StringSession", side_effect=lambda value: value):
            with self.assertRaises(TimeoutError):
                await local_web_resource_groups._owner_group(
                    "owner",
                    123,
                    "hash",
                    "Resources",
                    "",
                    None,
                    lambda *_args, **_kwargs: client,
                )
        self.assertTrue(client.disconnected)

    async def test_resource_group_state_is_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "resource_groups.json"
            local_web_resource_groups._save_resource_group(
                path,
                {
                    "group_id": "-10077",
                    "invite_link": "https://t.me/+private",
                    "results": [],
                },
            )
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


class TelegramAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_message_anchor_is_used_as_the_history_offset(self) -> None:
        class Client:
            requested: dict[str, object] = {}

            async def connect(self) -> None:
                return None

            async def disconnect(self) -> None:
                return None

            async def get_messages(self, _entity: object, **kwargs: object) -> list[object]:
                self.requested = kwargs
                return []

        client = Client()
        with (
            patch.object(local_web_telegram, "StringSession", side_effect=lambda value: value),
            patch.object(local_web_telegram, "TelegramClient", return_value=client),
            patch.object(local_web_telegram, "resolve_dialog_entity", new=AsyncMock(return_value=object())),
        ):
            messages = await local_web_telegram.list_messages(
                "session",
                123,
                "hash",
                "@preview_bot",
                limit=80,
                anchor_message_id=41,
            )

        self.assertEqual(messages, [])
        self.assertEqual(client.requested, {"limit": 80, "offset_id": 42})

    async def test_specific_messages_include_renderable_media_metadata(self) -> None:
        image = SimpleNamespace(
            id=12,
            date=None,
            out=False,
            message="图片说明",
            media=SimpleNamespace(),
            photo=SimpleNamespace(id=987, dc_id=2, sizes=[SimpleNamespace(w=800, h=600)]),
            video=None,
            video_note=None,
            document=None,
            file=SimpleNamespace(
                mime_type="image/jpeg",
                name="photo.jpg",
                size=12345,
                width=800,
                height=600,
                duration=0,
            ),
            get_sender=AsyncMock(return_value=SimpleNamespace(first_name="Sender", last_name="")),
        )

        class Client:
            requested_ids: list[int] = []

            async def connect(self) -> None:
                return None

            async def disconnect(self) -> None:
                return None

            async def get_messages(self, _entity: object, **kwargs: object) -> list[object]:
                self.requested_ids = list(kwargs["ids"])  # type: ignore[arg-type]
                return [image]

        client = Client()
        with (
            patch.object(local_web_telegram, "StringSession", side_effect=lambda value: value),
            patch.object(local_web_telegram, "TelegramClient", return_value=client),
            patch.object(local_web_telegram, "resolve_dialog_entity", new=AsyncMock(return_value=object())),
        ):
            messages = await local_web_telegram.list_messages(
                "session",
                123,
                "hash",
                "@preview_bot",
                message_ids=[12, 14],
            )

        self.assertEqual(client.requested_ids, [12, 14])
        self.assertEqual(messages[0]["text"], "图片说明")
        self.assertEqual(
            messages[0]["media"],
            {
                "kind": "image",
                "mime_type": "image/jpeg",
                "file_name": "photo.jpg",
                "size": 12345,
                "width": 800,
                "height": 600,
                "duration": 0,
                "has_thumbnail": True,
                "media_id": "image:2:987",
            },
        )

    async def test_thumbnail_download_uses_the_best_bounded_size(self) -> None:
        sizes = [
            SimpleNamespace(w=320, h=240, type="m"),
            SimpleNamespace(w=800, h=600, type="x"),
            SimpleNamespace(w=1280, h=960, type="y"),
        ]
        message = SimpleNamespace(
            id=12,
            media=SimpleNamespace(),
            photo=SimpleNamespace(sizes=sizes),
            document=None,
            video=None,
            video_note=None,
            file=SimpleNamespace(mime_type="image/jpeg", size=99999),
        )

        class Client:
            selected_thumb: object | None = None

            async def connect(self) -> None:
                return None

            async def disconnect(self) -> None:
                return None

            async def get_messages(self, _entity: object, *, ids: int) -> object:
                self.message_id = ids
                return message

            async def download_media(self, _message: object, *, file: object, thumb: object) -> bytes:
                self.selected_thumb = thumb
                self.file_target = file
                return b"jpeg-thumbnail"

        client = Client()
        with (
            patch.object(local_web_telegram, "StringSession", side_effect=lambda value: value),
            patch.object(local_web_telegram, "TelegramClient", return_value=client),
            patch.object(local_web_telegram, "resolve_dialog_entity", new=AsyncMock(return_value=object())),
        ):
            content, content_type = await local_web_telegram.download_message_media(
                "session",
                123,
                "hash",
                "@preview_bot",
                12,
                thumbnail=True,
            )

        self.assertEqual(content, b"jpeg-thumbnail")
        self.assertEqual(content_type, "image/jpeg")
        self.assertIs(client.selected_thumb, sizes[0])

    async def test_bot_command_lookup_returns_history_when_official_menu_fails(self) -> None:
        class Client:
            async def connect(self) -> None:
                return None

            async def disconnect(self) -> None:
                return None

            async def __call__(self, _request: object) -> object:
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

            async def get_messages(self, _entity: object, limit: int) -> list[object]:
                self.limit = limit
                return [SimpleNamespace(message="", buttons=[[SimpleNamespace(text="♻️ 随机")]])]

        client = Client()
        with (
            patch.object(local_web_telegram, "StringSession", side_effect=lambda value: value),
            patch.object(local_web_telegram, "TelegramClient", return_value=client),
            patch.object(
                local_web_telegram,
                "resolve_dialog_entity",
                new=AsyncMock(return_value=SimpleNamespace(bot=True)),
            ),
            patch.object(local_web_telegram, "COMMAND_SOURCE_TIMEOUT", 0.01),
        ):
            payload = await local_web_telegram.list_bot_commands("session", 123, "hash", "@bot")

        self.assertEqual(payload["commands"], [{"command": "♻️ 随机", "description": "键盘按钮"}])
        self.assertEqual(payload["source"], "keyboard")
        self.assertTrue(payload["partial"])
        self.assertIn("官方菜单", payload["warning"])

    async def test_bot_command_lookup_has_total_timeout_and_disconnects(self) -> None:
        class Client:
            disconnected = False

            async def connect(self) -> None:
                await asyncio.Event().wait()

            async def disconnect(self) -> None:
                self.disconnected = True

        client = Client()
        with (
            patch.object(local_web_telegram, "StringSession", side_effect=lambda value: value),
            patch.object(local_web_telegram, "TelegramClient", return_value=client),
            patch.object(local_web_telegram, "COMMAND_REQUEST_TIMEOUT", 0.01),
        ):
            with self.assertRaises(TimeoutError):
                await local_web_telegram.list_bot_commands("session", 123, "hash", "@bot")
        self.assertTrue(client.disconnected)


class GitHubCliBoundaryTests(unittest.TestCase):
    def test_repository_and_workflow_inputs_are_restricted(self) -> None:
        self.assertEqual(local_web_github.validate_repository("owner/repo"), "owner/repo")
        for value in ("repo", "owner/repo;rm", "owner/../repo"):
            with self.assertRaises(ValueError):
                local_web_github.validate_repository(value)
        with self.assertRaises(ValueError):
            local_web_github.validate_workflow("../unsafe.yml")

    def test_secret_values_use_stdin_and_never_argv(self) -> None:
        calls: list[tuple[list[str], str | None]] = []

        def record(args: list[str], *, cwd: Path, stdin: str | None = None, timeout: int = 30) -> str:
            del cwd, timeout
            calls.append((args, stdin))
            return ""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            alt = root / "alt.txt"
            main = root / "main.txt"
            local_web_accounts.write_lines(alt, ["session-super-secret"])
            local_web_accounts.write_lines(main, ["main-super-secret"])
            with patch.object(local_web_github, "_run_gh", side_effect=record):
                uploaded = local_web_github.sync_local_credentials(
                    cwd=root,
                    repository="owner/repo",
                    names=["TG_SESSION_STRINGS", "TG_API_HASH"],
                    alt_out=alt,
                    main_out=main,
                    api_id=123,
                    api_hash="hash-super-secret",
                )
        self.assertEqual(uploaded, ["TG_SESSION_STRINGS", "TG_API_HASH"])
        self.assertEqual([call[1] for call in calls], ["session-super-secret", "hash-super-secret"])
        argv = " ".join(part for call, _stdin in calls for part in call)
        self.assertNotIn("super-secret", argv)


class BotOrchestrationTests(unittest.TestCase):
    def test_bot_blueprint_round_trip_preserves_nodes_and_edges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flows.json"
            blueprint = {
                "nodes": [
                    {"id": "start", "position": {"x": 10, "y": 20}, "data": {"kind": "command", "config": {"commands": "/start"}}},
                    {"id": "end", "position": {"x": 200, "y": 20}, "data": {"kind": "end", "config": {}}},
                ],
                "edges": [{"id": "edge", "source": "start", "target": "end"}],
            }
            saved = local_web_bots.save_bot_workflow(
                path,
                {"api_version": 2, "id": "visual", "name": "Visual", "bot_id": "bot", "blueprint": blueprint},
            )
            self.assertEqual(saved["api_version"], 2)
            self.assertEqual(saved["blueprint"], blueprint)
            self.assertEqual(local_web_bots.list_bot_workflows(path)[0]["blueprint"], blueprint)

    def test_bot_token_is_private_and_never_returned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bots.json"
            with patch.object(local_web_bots, "call_bot_api", return_value={"id": 42, "username": "test_bot", "first_name": "Test"}):
                bot = local_web_bots.add_bot(path, "12345:abcdefghijklmnopqrstuvwxyz", "自动化机器人")
            self.assertNotIn("token", bot)
            self.assertEqual(bot["username"], "@test_bot")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertNotIn("abcdefghijklmnopqrstuvwxyz", json.dumps(local_web_bots.list_bots(path)))

    def test_v2_rejects_legacy_steps_and_dangling_edges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flows.json"
            with self.assertRaisesRegex(ValueError, "API v2 已移除 steps"):
                local_web_bots.save_bot_workflow(path, {"id": "legacy", "steps": []})
            with self.assertRaisesRegex(ValueError, "不存在的节点"):
                local_web_bots.save_bot_workflow(path, {
                    "id": "broken",
                    "blueprint": {
                        "nodes": [{"id": "start", "data": {"kind": "command", "config": {}}}],
                        "edges": [{"id": "missing", "source": "start", "target": "absent"}],
                    },
                })

    def test_legacy_record_with_blueprint_is_projected_to_v2(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flows.json"
            local_web_accounts.write_private_text(path, json.dumps([{
                "id": "legacy-visual", "name": "Legacy", "bot_id": "bot", "steps": [{"method": "getMe"}],
                "blueprint": {"nodes": [], "edges": []},
            }]))
            workflows = local_web_bots.list_bot_workflows(path)
            self.assertEqual(workflows, [{
                "api_version": 2, "id": "legacy-visual", "name": "Legacy", "bot_id": "bot",
                "blueprint": {"nodes": [], "edges": []},
            }])

    def test_blueprint_debugger_routes_by_callback_without_external_side_effects(self) -> None:
        workflow = {
            "blueprint": {
                "nodes": [
                    {"id": "start", "data": {"kind": "command", "category": "触发", "label": "开始", "config": {"commands": ["/start"]}}},
                    {"id": "menu", "data": {"kind": "menu", "category": "消息", "label": "菜单", "branches": ["商店", "排行"], "config": {"buttons": [{"label": "商店", "callback_data": "shop"}, {"label": "排行", "callback_data": "rank"}]}}},
                    {"id": "shop", "data": {"kind": "send", "category": "消息", "label": "商店消息", "config": {"text": "shop"}}},
                    {"id": "rank", "data": {"kind": "database", "category": "数据", "label": "查询排行", "config": {"operation": "select", "table": "users"}}},
                ],
                "edges": [
                    {"id": "a", "source": "start", "target": "menu"},
                    {"id": "b", "source": "menu", "sourceHandle": "branch-0", "target": "shop"},
                    {"id": "c", "source": "menu", "sourceHandle": "branch-1", "target": "rank"},
                ],
            },
        }
        with patch.object(local_web_bots, "call_bot_api") as external_call:
            result = local_web_bots.debug_bot_workflow(workflow, {"callback_data": "rank"})
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["visited_nodes"], ["start", "menu", "rank"])
        self.assertEqual(result["logs"][1]["branch"], "排行")
        external_call.assert_not_called()

    def test_blueprint_debugger_halts_an_unbounded_cycle(self) -> None:
        workflow = {
            "blueprint": {
                "nodes": [
                    {"id": "start", "data": {"kind": "command", "category": "触发", "label": "开始", "config": {}}},
                    {"id": "loop", "data": {"kind": "delay", "category": "逻辑", "label": "循环", "config": {"seconds": 1}}},
                ],
                "edges": [{"id": "a", "source": "start", "target": "loop"}, {"id": "b", "source": "loop", "target": "start"}],
            },
        }
        result = local_web_bots.debug_bot_workflow(workflow, {})
        self.assertEqual(result["status"], "halted")
        self.assertIn("避免死循环", result["logs"][-1]["summary"])

    def test_live_debug_consumes_real_update_and_calls_bot_api(self) -> None:
        session = {
            "token": "12345:abcdefghijklmnopqrstuvwxyz",
            "bot_username": "@test_bot",
            "logs": [],
            "workflow": {
                "blueprint": {
                    "nodes": [
                        {"id": "start", "data": {"kind": "command", "category": "触发", "label": "/start", "config": {"commands": ["/start"]}}},
                        {"id": "reply", "data": {"kind": "send", "category": "消息", "label": "真实回复", "config": {"text": "你好 {{user.first_name}}", "parse_mode": "HTML"}}},
                    ],
                    "edges": [{"id": "edge", "source": "start", "target": "reply"}],
                },
            },
        }
        update = {"update_id": 9, "message": {"text": "/start", "chat": {"id": 88}, "from": {"id": 7, "first_name": "HX"}}}
        with tempfile.TemporaryDirectory() as directory:
            session["db_path"] = str(Path(directory) / "debug.sqlite3")
            with patch.object(local_web_bots, "call_bot_api", return_value={"message_id": 11}) as call:
                local_web_bots._live_handle_update(session, update)
            self.assertEqual(call.call_args.args[1], "sendMessage")
            self.assertEqual(call.call_args.args[2]["text"], "你好 HX")
            self.assertTrue(any(log["event"] == "update" for log in session["logs"]))
            self.assertTrue(any(log["event"] == "api" for log in session["logs"]))

    def test_menu_callback_ids_are_internal_stable_and_short(self) -> None:
        first = local_web_bots._menu_callback_id("main-menu", 1)
        self.assertEqual(first, local_web_bots._menu_callback_id("main-menu", 1))
        self.assertNotEqual(first, local_web_bots._menu_callback_id("main-menu", 2))
        self.assertLessEqual(len(first.encode()), 64)
        checkin = local_web_bots._checkin_callback_id("bot-debug-1234567890", "abcdef1234567890abcdef1234567890", 42)
        self.assertLessEqual(len(checkin.encode()), 64)

    def test_commands_are_extracted_for_telegram_and_invalid_chinese_command_is_skipped(self) -> None:
        workflow = {"blueprint": {"nodes": [
            {"data": {"kind": "command", "label": "/签到", "config": {"commands": ["/签到", "/checkin", "/start"]}}},
            {"data": {"kind": "menu", "label": "主菜单", "config": {"buttons": [{"label": "积分商店"}, {"label": "排行榜"}, {"label": "邀请好友"}]}}},
        ]}}
        self.assertEqual(local_web_bots._workflow_commands(workflow), [
            {"command": "checkin", "description": "签到"},
            {"command": "start", "description": "签到"},
            {"command": "shop", "description": "积分商店"},
            {"command": "ranking", "description": "排行榜"},
            {"command": "invite", "description": "邀请好友"},
        ])

    def test_captcha_is_a_nontrivial_png(self) -> None:
        image = local_web_bots._captcha_png("7 + 5 = ?")
        self.assertTrue(image.startswith(b"\x89PNG"))
        self.assertGreater(len(image), 10_000)

    def test_live_debug_refuses_to_replace_an_existing_webhook(self) -> None:
        workflow = {"bot_id": "bot", "blueprint": {"nodes": [], "edges": []}}
        with (
            patch.object(local_web_bots, "_bot_token", return_value="12345:abcdefghijklmnopqrstuvwxyz"),
            patch.object(local_web_bots, "call_bot_api", return_value={"url": "https://production.example/webhook"}),
        ):
            with self.assertRaisesRegex(ValueError, "不允许同时使用 getUpdates"):
                local_web_bots.start_live_debug(Path("bots.json"), workflow)


class LocalHttpIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.previous_workflows = local_web_server.Handler.workflows
        self.previous_bots = local_web_server.Handler.bots
        self.previous_bot_workflows = local_web_server.Handler.bot_workflows
        local_web_server.Handler.workflows = Path(self.temporary.name) / "workflows.json"
        local_web_server.Handler.bots = Path(self.temporary.name) / "bots.json"
        local_web_server.Handler.bot_workflows = Path(self.temporary.name) / "bot-workflows.json"
        with local_web_server.jobs_lock:
            self.previous_run_jobs = dict(local_web_server.run_jobs)
            self.previous_run_processes = dict(local_web_server.run_processes)
            local_web_server.run_jobs.clear()
            local_web_server.run_processes.clear()
        self.previous_run_events = dict(local_web_server.run_events)
        local_web_server.run_events.clear()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), local_web_server.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        local_web_server.Handler.workflows = self.previous_workflows
        local_web_server.Handler.bots = self.previous_bots
        local_web_server.Handler.bot_workflows = self.previous_bot_workflows
        with local_web_server.jobs_lock:
            local_web_server.run_jobs.clear()
            local_web_server.run_jobs.update(self.previous_run_jobs)
            local_web_server.run_processes.clear()
            local_web_server.run_processes.update(self.previous_run_processes)
        local_web_server.run_events.clear()
        local_web_server.run_events.update(self.previous_run_events)
        self.temporary.cleanup()

    def request(self, method: str, path: str, payload: object | None = None) -> tuple[int, dict[str, object]]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def request_with_origin(self, method: str, path: str, origin: str) -> tuple[int, dict[str, object]]:
        request = urllib.request.Request(
            self.base_url + path,
            data=b"{}",
            method=method,
            headers={"Content-Type": "application/json", "Origin": origin},
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_workflow_crud_crosses_real_http_boundary(self) -> None:
        status, payload = self.request("GET", "/api/workflows")
        self.assertEqual((status, payload), (200, {"workflows": []}))

        flow = {
            "id": "http-flow",
            "nodes": [{"id": "task", "data": {"kind": "task", "taskId": "http-flow"}}],
            "edges": [],
        }
        status, payload = self.request("POST", "/api/workflows", flow)
        self.assertEqual(status, 200)
        self.assertEqual(payload["workflow"]["id"], "http-flow")

        status, payload = self.request("DELETE", "/api/workflows/http-flow")
        self.assertEqual((status, payload["deleted"]), (200, True))

    def test_bot_workflow_crud_crosses_real_http_boundary(self) -> None:
        status, payload = self.request("GET", "/api/bots")
        self.assertEqual((status, payload), (200, {"bots": []}))
        flow = {
            "api_version": 2,
            "id": "bot-http",
            "name": "Bot HTTP",
            "bot_id": "bot-1",
            "blueprint": {"nodes": [{"id": "me", "data": {"kind": "api", "config": {"method": "getMe", "params": "{}"}}}], "edges": []},
        }
        status, payload = self.request("POST", "/api/bot-workflows", flow)
        self.assertEqual(status, 200)
        self.assertEqual(payload["workflow"]["id"], "bot-http")
        status, payload = self.request("DELETE", "/api/bot-workflows/bot-http")
        self.assertEqual((status, payload["deleted"]), (200, True))
        status, payload = self.request("GET", "/api/bot-workflows")
        self.assertEqual((status, payload), (200, {"workflows": []}))

    def test_non_object_json_is_rejected_as_bad_request(self) -> None:
        status, payload = self.request("POST", "/api/workflows", [])
        self.assertEqual(status, 400)
        self.assertIn("必须是对象", str(payload["error"]))

    def test_remote_writes_require_explicit_confirmation(self) -> None:
        status, payload = self.request(
            "POST",
            "/api/github/secrets/sync",
            {"repository": "owner/repo", "names": ["TG_SESSION_STRINGS"]},
        )
        self.assertEqual(status, 400)
        self.assertIn("确认", str(payload["error"]))

        status, payload = self.request(
            "POST",
            "/api/resource-groups/sync",
            {"title": "Resources", "owner_account_id": "main-1"},
        )
        self.assertEqual(status, 400)
        self.assertIn("确认", str(payload["error"]))

    def test_run_start_rejects_a_second_active_job(self) -> None:
        for active_status in ("running", "cancelling"):
            with self.subTest(active_status=active_status):
                active = local_web_server.RunJob(id=f"active-{active_status}", status=active_status)
                with local_web_server.jobs_lock:
                    local_web_server.run_jobs[active.id] = active
                status, payload = self.request(
                    "POST",
                    "/api/run/start",
                    {
                        "workflow": {
                            "id": "second-run",
                            "nodes": [{"id": "task", "data": {"kind": "task", "taskId": "second-run"}}],
                            "edges": [],
                        }
                    },
                )
                self.assertEqual(status, 409)
                self.assertIn("活动", str(payload["error"]))
                self.assertEqual(payload["job"]["id"], active.id)
                self.assertEqual(payload["job"]["status"], active_status)
                with local_web_server.jobs_lock:
                    local_web_server.run_jobs.pop(active.id, None)

    def test_run_cancel_is_idempotent_over_http(self) -> None:
        job = local_web_server.RunJob(id="http-cancel")
        process = SimpleNamespace()
        with local_web_server.jobs_lock:
            local_web_server.run_jobs[job.id] = job
            local_web_server.run_processes[job.id] = process
        with patch.object(local_web_server, "_terminate_process_group") as terminate:
            first_status, first = self.request("POST", "/api/run/cancel", {"id": job.id})
            second_status, second = self.request("POST", "/api/run/cancel", {"id": job.id})
        self.assertEqual((first_status, second_status), (200, 200))
        self.assertEqual(first["status"], "cancelling")
        self.assertEqual(second["status"], "cancelling")
        terminate.assert_called_once_with(process)

        with local_web_server.jobs_lock:
            local_web_server._set_run_cancelled(local_web_server.run_jobs[job.id])
        final_status, final = self.request("POST", "/api/run/cancel", {"id": job.id})
        self.assertEqual(final_status, 200)
        self.assertEqual(final["status"], "cancelled")

    def test_run_cancel_returns_not_found_for_unknown_job(self) -> None:
        status, payload = self.request("POST", "/api/run/cancel", {"id": "missing"})
        self.assertEqual(status, 404)
        self.assertEqual(payload["status"], "not_found")

    def test_cancelled_run_is_a_terminal_sse_state(self) -> None:
        job = local_web_server.RunJob(id="cancelled-stream", status="cancelled", cancel_requested=True)
        with local_web_server.jobs_lock:
            local_web_server.run_jobs[job.id] = job
        with urllib.request.urlopen(f"{self.base_url}/api/run/stream?id={job.id}", timeout=3) as response:
            stream = response.read().decode("utf-8")
        self.assertIn('"status": "cancelled"', stream)

    def test_2fa_password_rejects_unknown_login_job(self) -> None:
        status, payload = self.request("POST", "/api/qr/password", {"id": "missing", "password": "secret"})
        self.assertEqual(status, 400)
        self.assertIn("登录任务不存在", str(payload["error"]))

    def test_cross_site_mutation_is_rejected(self) -> None:
        status, payload = self.request_with_origin("POST", "/api/github/secrets/sync", "https://evil.example")
        self.assertEqual(status, 403)
        self.assertIn("仅允许本机", str(payload["error"]))

    def test_invalid_continuous_request_is_rejected_at_compile_boundary(self) -> None:
        status, payload = self.request(
            "POST",
            "/api/workflows/compile",
            {
                "id": "invalid-loop",
                "nodes": [
                    {"id": "task", "data": {"kind": "task"}},
                    {
                        "id": "request",
                        "data": {
                            "kind": "continuous_request",
                            "requestMode": "message",
                            "requestText": "资源",
                            "repeatCount": 1001,
                        },
                    },
                ],
                "edges": [{"source": "task", "target": "request"}],
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("repeatCount", str(payload["error"]))


class ActionInterpreterTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_dialog_avatar_is_encoded_and_cached(self) -> None:
        entity = SimpleNamespace(id=123, photo=SimpleNamespace(photo_id=456))
        client = SimpleNamespace(download_profile_photo=AsyncMock(return_value=b"image-bytes"))
        local_web_telegram.dialog_avatar_cache.clear()
        semaphore = asyncio.Semaphore(1)
        first = await local_web_telegram._dialog_avatar(client, entity, True, semaphore)
        second = await local_web_telegram._dialog_avatar(client, entity, True, semaphore)
        self.assertTrue(first.startswith("data:image/jpeg;base64,"))
        self.assertEqual(first, second)
        client.download_profile_photo.assert_awaited_once()

    async def test_open_action_resolves_entity_once_and_records_completion(self) -> None:
        client = SimpleNamespace(get_entity=AsyncMock(return_value=object()))
        context = await signer_actions.run_actions(
            client,
            "@initial",
            [{"type": "open", "peer": "@target", "node_id": "open-node"}],
            1,
        )
        client.get_entity.assert_awaited_once_with("@target")
        self.assertEqual(context["node_logs"]["open-node"], ["开始 open", "打开对话: @target", "完成 open"])

    async def test_continuous_request_runs_exact_rounds_and_extracts_all_links(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.sent = 0
                self.current: list[object] = []

            async def send_message(self, peer: str, text: str) -> object:
                self.sent += 1
                sent_id = self.sent * 10
                self.current = [
                    SimpleNamespace(
                        id=sent_id + 1,
                        out=False,
                        message=f"资源 https://example.com/items/{self.sent}",
                        buttons=[[SimpleNamespace(url="tg://resolve?domain=resource_pool")]],
                    )
                ]
                return SimpleNamespace(id=sent_id)

            async def get_messages(self, peer: str, limit: int) -> list[object]:
                return self.current[:limit]

        client = Client()
        with patch.object(signer_actions.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            context = await signer_actions.run_actions(
                client,
                "@resource_bot",
                [
                    {
                        "type": "continuous_request",
                        "request_mode": "message",
                        "request_text": "随机获取资源",
                        "repeat_count": 3,
                        "interval_seconds": 2.5,
                        "response_wait_seconds": 0,
                        "response_limit": 5,
                        "max_failures": 3,
                    }
                ],
                7,
            )

        self.assertEqual(client.sent, 3)
        self.assertEqual([item.args[0] for item in sleep.await_args_list], [2.5, 2.5])
        self.assertEqual(len(context["resource_responses"]), 3)
        self.assertEqual(len(context["resource_records"]), 6)
        self.assertEqual(context["resource_records"][0]["account_index"], 7)
        self.assertEqual(context["resource_records"][-1]["round"], 3)
        self.assertIn("https://example.com/items/1", context["last_links"])
        self.assertIn("tg://resolve?domain=resource_pool", context["last_links"])

    async def test_continuous_request_recovers_before_failure_threshold(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.attempts = 0
                self.current: list[object] = []

            async def send_message(self, peer: str, text: str) -> object:
                self.attempts += 1
                if self.attempts == 1:
                    raise RuntimeError("temporary failure")
                sent_id = self.attempts * 10
                self.current = [
                    SimpleNamespace(
                        id=sent_id + 1,
                        out=False,
                        message=f"https://example.com/recovered/{self.attempts}",
                        buttons=[],
                    )
                ]
                return SimpleNamespace(id=sent_id)

            async def get_messages(self, peer: str, limit: int) -> list[object]:
                return self.current[:limit]

        client = Client()
        with patch.object(signer_actions.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            context = await signer_actions.run_actions(
                client,
                "@resource_bot",
                [
                    {
                        "type": "continuous_request",
                        "request_mode": "message",
                        "request_text": "随机获取资源",
                        "repeat_count": 3,
                        "interval_seconds": 0.5,
                        "response_wait_seconds": 0,
                        "response_limit": 5,
                        "max_failures": 2,
                    }
                ],
                1,
            )

        self.assertEqual(client.attempts, 3)
        self.assertEqual([item.args[0] for item in sleep.await_args_list], [0.5, 0.5])
        self.assertEqual(len(context["resource_responses"]), 2)
        self.assertEqual(len(context["resource_records"]), 2)

    async def test_continuous_button_request_clicks_exact_rounds(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.clicks = 0
                self.after_click = False

            async def get_messages(self, peer: str, limit: int) -> list[object]:
                if self.after_click:
                    self.after_click = False
                    return [
                        SimpleNamespace(
                            id=self.clicks * 10 + 11,
                            out=False,
                            message="https://example.com/button-result",
                            buttons=[],
                        )
                    ]
                client = self

                class ButtonMessage:
                    id = client.clicks * 10 + 10

                    async def click(self, text: str) -> str:
                        if text != "随机获取资源":
                            raise RuntimeError("wrong button")
                        client.clicks += 1
                        client.after_click = True
                        return "https://example.com/request-target"

                return [ButtonMessage()]

        client = Client()
        with patch.object(signer_actions.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            context = await signer_actions.run_actions(
                client,
                "@resource_bot",
                [
                    {
                        "type": "continuous_request",
                        "request_mode": "button",
                        "request_text": "随机获取资源",
                        "repeat_count": 2,
                        "interval_seconds": 0.5,
                        "response_wait_seconds": 0,
                        "response_limit": 5,
                        "max_failures": 2,
                    }
                ],
                1,
            )
        self.assertEqual(client.clicks, 2)
        sleep.assert_awaited_once_with(0.5)
        self.assertEqual(len(context["resource_responses"]), 2)
        self.assertIn("https://example.com/request-target", context["last_links"])

    async def test_continuous_button_request_collects_direct_url_without_new_message(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.clicks = 0

            async def get_messages(self, peer: str, limit: int) -> list[object]:
                client = self

                class ButtonMessage:
                    id = 10
                    out = False
                    message = "选择资源"

                    async def click(self, text: str) -> str:
                        client.clicks += 1
                        return f"https://example.com/direct/{client.clicks}"

                return [ButtonMessage()]

        client = Client()
        with patch.object(signer_actions.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            context = await signer_actions.run_actions(
                client,
                "@resource_bot",
                [
                    {
                        "type": "continuous_request",
                        "request_mode": "button",
                        "request_text": "随机获取资源",
                        "repeat_count": 2,
                        "interval_seconds": 0.5,
                        "response_wait_seconds": 0,
                        "response_limit": 5,
                        "max_failures": 2,
                    }
                ],
                1,
            )

        self.assertEqual(client.clicks, 2)
        sleep.assert_awaited_once_with(0.5)
        self.assertEqual(context["resource_responses"], [])
        self.assertEqual(
            context["last_links"],
            ["https://example.com/direct/1", "https://example.com/direct/2"],
        )

    async def test_continuous_button_request_collects_edited_message(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.edited = False

            async def get_messages(self, peer: str, limit: int) -> list[object]:
                if self.edited:
                    return [
                        SimpleNamespace(
                            id=20,
                            out=False,
                            message="https://example.com/edited-result",
                            edit_date="updated",
                            buttons=[],
                        )
                    ]
                client = self

                class ButtonMessage:
                    id = 20
                    out = False
                    message = "等待选择"
                    edit_date = None

                    async def click(self, text: str) -> object:
                        client.edited = True
                        return SimpleNamespace(message="", url="")

                return [ButtonMessage()]

        context = await signer_actions.run_actions(
            Client(),
            "@resource_bot",
            [
                {
                    "type": "continuous_request",
                    "request_mode": "button",
                    "request_text": "随机获取资源",
                    "repeat_count": 1,
                    "interval_seconds": 0.5,
                    "response_wait_seconds": 0,
                    "response_limit": 5,
                    "max_failures": 1,
                }
            ],
            1,
        )

        self.assertEqual(len(context["resource_responses"]), 1)
        self.assertEqual(context["last_links"], ["https://example.com/edited-result"])

    async def test_preview_catalog_indexes_callback_without_clicking_unlock(self) -> None:
        unlock_button = SimpleNamespace(
            text="🔓 解锁资源",
            data=b"unlock:WYyunbot_65p_28v_0d_preview-key",
            url="",
            click=AsyncMock(),
        )
        ignored_button = SimpleNamespace(
            text="下载资源",
            data=b"download:must-not-index",
            url="",
            click=AsyncMock(),
        )
        bot = SimpleNamespace(id=777, bot=True, username="WYpanbot")

        def response_messages() -> list[object]:
            photos = [
                SimpleNamespace(
                    id=message_id,
                    out=False,
                    message="",
                    buttons=[],
                    photo=SimpleNamespace(id=message_id + 1000),
                    video=None,
                    video_note=None,
                    file=None,
                    document=None,
                    chat=bot,
                )
                for message_id in (11, 12, 13)
            ]
            summary = SimpleNamespace(
                id=14,
                out=False,
                message=(
                    "♻️ 为您推荐\n"
                    "📦 文件描述: 预览资源 A\n"
                    "📸 图片: 65个 | 🎬 视频: 28个 | 📄 文件: 0个\n\n"
                    "💎 解锁需要: 5 积分"
                ),
                buttons=[[unlock_button, ignored_button]],
                photo=None,
                video=None,
                video_note=None,
                file=None,
                document=None,
                chat=bot,
            )
            return [summary, *reversed(photos)]

        class Client:
            def __init__(self) -> None:
                self.sent_texts: list[str] = []

            async def send_message(self, _peer: str, text: str) -> object:
                self.sent_texts.append(text)
                return SimpleNamespace(id=10)

            async def get_messages(self, _peer: str, limit: int) -> list[object]:
                return response_messages()[:limit]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            actions = [
                {
                    "type": "continuous_request",
                    "request_mode": "message",
                    "request_text": "♻️ 随机",
                    "repeat_count": 1,
                    "interval_seconds": 0.5,
                    "response_wait_seconds": 0,
                    "response_limit": 10,
                    "max_failures": 1,
                },
                {
                    "type": "preview_catalog",
                    "source": "resource_responses",
                    "callback_prefix": "unlock:",
                    "dedupe_store": str(root / "previews.sqlite3"),
                    "export_path": str(root / "previews.json"),
                },
            ]
            clients = [Client(), Client()]
            with patch.dict(os.environ, {"TG_ACCOUNT_IDS": "main-1\nalt-1"}), patch(
                "builtins.print"
            ) as output:
                await signer_actions.run_actions(clients[0], "@WYpanbot", actions, 1)
                await signer_actions.run_actions(clients[1], "@WYpanbot", actions, 2)

            exported = json.loads((root / "previews.json").read_text(encoding="utf-8"))

        self.assertEqual([client.sent_texts for client in clients], [["♻️ 随机"], ["♻️ 随机"]])
        unlock_button.click.assert_not_awaited()
        ignored_button.click.assert_not_awaited()
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["resource_key"], "WYyunbot_65p_28v_0d_preview-key")
        self.assertEqual(exported[0]["description"], "预览资源 A")
        self.assertEqual(exported[0]["unlock_cost"], 5)
        self.assertEqual(exported[0]["account_id"], "main-1")
        self.assertEqual(exported[0]["seen_count"], 2)
        self.assertEqual(len(exported[0]["preview_message_links"]), 3)
        self.assertEqual(
            [item["kind"] for item in exported[0]["preview_media"]],
            ["photo", "photo", "photo"],
        )
        self.assertEqual(
            [item["message_id"] for item in exported[0]["preview_media"]],
            [11, 12, 13],
        )
        self.assertEqual(len(exported[0]["provenance"]), 2)
        self.assertEqual(
            [item["account_id"] for item in exported[0]["provenance"]],
            ["main-1", "alt-1"],
        )

        markers = [
            str(call.args[0])
            for call in output.call_args_list
            if call.args and str(call.args[0]).startswith("::resource-preview::")
        ]
        job = local_web_server.RunJob(id="preview-run")
        for marker in markers:
            preview = local_web_server.parse_resource_preview_marker(marker)
            self.assertIsNotNone(preview)
            local_web_server.merge_resource_preview(job, preview or {})
        self.assertEqual(len(job.resource_previews), 1)
        self.assertEqual(job.resource_previews[0]["seen_count"], 2)
        self.assertFalse(job.resource_previews[0]["is_new"])

    async def test_resource_catalog_deduplicates_across_accounts_and_emits_markers(self) -> None:
        class Client:
            def __init__(self, offset: int) -> None:
                self.offset = offset
                self.current: list[object] = []

            async def send_message(self, peer: str, text: str) -> object:
                self.current = [
                    SimpleNamespace(
                        id=self.offset + 2,
                        out=False,
                        message="https://example.com/shared",
                        buttons=[],
                    )
                ]
                return SimpleNamespace(id=self.offset + 1)

            async def get_messages(self, peer: str, limit: int) -> list[object]:
                return self.current[:limit]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            actions = [
                {
                    "type": "continuous_request",
                    "request_mode": "message",
                    "request_text": "随机获取资源",
                    "repeat_count": 1,
                    "interval_seconds": 0.5,
                    "response_wait_seconds": 0,
                    "response_limit": 5,
                    "max_failures": 1,
                },
                {
                    "type": "resource_catalog",
                    "dedupe_store": str(root / "catalog.sqlite3"),
                    "export_path": str(root / "links.json"),
                },
            ]
            with patch("builtins.print") as output:
                await signer_actions.run_actions(Client(10), "@resource_bot", actions, 1)
                await signer_actions.run_actions(Client(20), "@resource_bot", actions, 2)

            exported = json.loads((root / "links.json").read_text(encoding="utf-8"))
            self.assertEqual(len(exported), 1)
            self.assertEqual(exported[0]["seen_count"], 2)
            self.assertEqual([item["account_index"] for item in exported[0]["provenance"]], [1, 2])
            self.assertEqual(stat.S_IMODE((root / "catalog.sqlite3").stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((root / "links.json").stat().st_mode), 0o600)

            markers = [
                call.args[0]
                for call in output.call_args_list
                if call.args and str(call.args[0]).startswith("::resource-link::")
            ]
            self.assertEqual(len(markers), 2)
            job = local_web_server.RunJob(id="resource-run")
            for marker in markers:
                resource = local_web_server.parse_resource_link_marker(marker)
                self.assertIsNotNone(resource)
                local_web_server.merge_resource_link(job, resource or {})
            self.assertEqual(len(job.resource_links), 1)
            self.assertEqual(job.resource_links[0]["seen_count"], 2)
            self.assertFalse(job.resource_links[0]["is_new"])

    async def test_forwarded_media_pool_link_is_added_to_resource_catalog(self) -> None:
        media = SimpleNamespace(
            id=11,
            out=False,
            message="",
            buttons=[],
            photo=SimpleNamespace(id=321),
            video=None,
            video_note=None,
            file=None,
            document=None,
        )
        client = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(id=10)),
            get_messages=AsyncMock(return_value=[media]),
            forward_messages=AsyncMock(return_value=SimpleNamespace(id=50)),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = await signer_actions.run_actions(
                client,
                "@resource_bot",
                [
                    {
                        "type": "continuous_request",
                        "request_mode": "message",
                        "request_text": "随机获取资源",
                        "repeat_count": 1,
                        "interval_seconds": 0.5,
                        "response_wait_seconds": 0,
                        "response_limit": 5,
                        "max_failures": 1,
                    },
                    {
                        "type": "resource_pool",
                        "pool_peer": "@resource_archive",
                        "source": "resource_responses",
                        "media_types": ["photo"],
                        "dedupe": True,
                        "dedupe_store": str(root / "media.sqlite3"),
                        "join_pool": False,
                    },
                    {
                        "type": "resource_catalog",
                        "source": "resource_records",
                        "dedupe_store": str(root / "catalog.sqlite3"),
                        "export_path": str(root / "links.json"),
                    },
                ],
                1,
            )

            exported = json.loads((root / "links.json").read_text(encoding="utf-8"))
        self.assertEqual(context["resource_records"][0]["url"], "https://t.me/resource_archive/50")
        self.assertEqual(exported[0]["url"], "https://t.me/resource_archive/50")
        client.forward_messages.assert_awaited_once_with("@resource_archive", media)

    async def test_after_send_parse_wait_is_not_applied_twice(self) -> None:
        client = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(id=10)),
            get_messages=AsyncMock(
                return_value=[SimpleNamespace(id=11, out=False, message="回执", buttons=[])]
            ),
        )
        with patch.object(signer_actions.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            await signer_actions.run_actions(
                client,
                "@resource_bot",
                [
                    {"type": "send", "text": "请求"},
                    {"type": "parse", "after_send": True, "wait_seconds": 2, "limit": 5},
                ],
                1,
            )
        sleep.assert_awaited_once_with(2.0)


if __name__ == "__main__":
    unittest.main()
