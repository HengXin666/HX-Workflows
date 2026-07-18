from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import local_web_accounts  # noqa: E402
import local_web_api  # noqa: E402
import local_web_flows  # noqa: E402
import local_web_server  # noqa: E402
import sign_from_config  # noqa: E402
import signer_actions  # noqa: E402
import signer_core  # noqa: E402


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


class LocalApiBoundaryTests(unittest.TestCase):
    def test_page_limit_is_bounded(self) -> None:
        self.assertEqual(local_web_server.parse_page_limit("40"), 40)
        for value in ("0", "101", "invalid"):
            with self.assertRaises(ValueError):
                local_web_server.parse_page_limit(value)


class LocalHttpIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.previous_workflows = local_web_server.Handler.workflows
        local_web_server.Handler.workflows = Path(self.temporary.name) / "workflows.json"
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

    def test_non_object_json_is_rejected_as_bad_request(self) -> None:
        status, payload = self.request("POST", "/api/workflows", [])
        self.assertEqual(status, 400)
        self.assertIn("必须是对象", str(payload["error"]))


class ActionInterpreterTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
