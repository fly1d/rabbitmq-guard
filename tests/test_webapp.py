import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from rabbitmq_guard.storage import RunStore
from rabbitmq_guard.webapp import create_server
from rabbitmq_guard.comparison import compare_runs, render_comparison_markdown


ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = ROOT / "data" / "scenarios"


class RunStoreTests(unittest.TestCase):
    def test_save_list_and_get(self) -> None:
        snapshot = json.loads(
            (CASE_DIR / "01_no_consumers.json").read_text(encoding="utf-8")
        )
        from rabbitmq_guard.diagnostics import diagnose

        with tempfile.TemporaryDirectory() as temp_dir:
            store = RunStore(Path(temp_dir) / "guard.db")
            saved = store.save(snapshot, diagnose(snapshot), "无人消费", "test")
            self.assertEqual("high", saved["status"])
            self.assertEqual(1, len(store.list()))
            loaded = store.get(saved["id"])
            self.assertIsNotNone(loaded)
            self.assertEqual("queue.no_consumers", loaded["findings"][0]["rule_id"])

    def test_compare_runs_tracks_new_resolved_and_metric_changes(self) -> None:
        from rabbitmq_guard.diagnostics import diagnose

        healthy = json.loads(
            (CASE_DIR / "00_healthy_baseline.json").read_text(encoding="utf-8")
        )
        memory_alarm = json.loads(
            (CASE_DIR / "05_memory_alarm.json").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RunStore(Path(temp_dir) / "guard.db")
            baseline = store.save(healthy, diagnose(healthy), "整改前", "test")
            current = store.save(memory_alarm, diagnose(memory_alarm), "整改后", "test")
            comparison = compare_runs(baseline, current)

        self.assertEqual("worsened", comparison["outcome"])
        self.assertEqual(1, comparison["summary"]["new"])
        self.assertEqual(0, comparison["summary"]["resolved"])
        self.assertEqual("node.memory_alarm", comparison["findings"]["new"][0]["rule_id"])
        self.assertEqual(6, comparison["metric_changes"][0]["delta"])
        report = render_comparison_markdown(comparison)
        self.assertIn("RabbitMQ Guard 整改复测报告", report)
        self.assertIn("风险上升", report)


class WebAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.server, _ = create_server(
            "127.0.0.1",
            0,
            Path(self.temp_dir.name) / "guard.db",
            CASE_DIR,
            live_enabled=False,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = "http://127.0.0.1:{}".format(self.server.server_address[1])

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def request_json(self, path: str, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=data,
            method="GET" if payload is None else "POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_health_and_scenarios(self) -> None:
        status, health = self.request_json("/api/health")
        self.assertEqual(200, status)
        self.assertFalse(health["live_enabled"])
        _, scenarios = self.request_json("/api/scenarios")
        self.assertGreaterEqual(len(scenarios["scenarios"]), 10)

    def test_scenario_analysis_persists_and_downloads_report(self) -> None:
        _, result = self.request_json(
            "/api/analyze/scenario", {"id": "memory_alarm", "persist": True}
        )
        self.assertEqual("critical", result["summary"]["status"])
        run_id = result["run"]["id"]
        _, runs = self.request_json("/api/runs")
        self.assertEqual(run_id, runs["runs"][0]["id"])
        with urlopen(self.base_url + "/api/runs/{}/report.md".format(run_id), timeout=3) as response:
            report = response.read().decode("utf-8")
        self.assertIn("RabbitMQ Guard 诊断报告", report)
        self.assertIn("节点触发内存告警", report)
        self.assertIn("集群：lab", report)

    def test_live_analysis_is_disabled_by_default(self) -> None:
        with self.assertRaises(HTTPError) as raised:
            self.request_json(
                "/api/analyze/live",
                {"url": "http://localhost:15672", "username": "guest", "password": "guest"},
            )
        self.assertEqual(403, raised.exception.code)

    def test_snapshot_upload_analysis_persists(self) -> None:
        snapshot = json.loads(
            (CASE_DIR / "09_quorum_lost.json").read_text(encoding="utf-8")
        )
        _, result = self.request_json(
            "/api/analyze/snapshot",
            {"snapshot": snapshot, "label": "quorum-lost-upload.json"},
        )
        self.assertEqual("critical", result["summary"]["status"])
        self.assertEqual("queue.quorum_lost", result["findings"][0]["rule_id"])
        self.assertEqual("upload", result["run"]["source_kind"])

    def test_compare_api_and_report(self) -> None:
        _, baseline = self.request_json(
            "/api/analyze/scenario", {"id": "memory_alarm", "persist": True}
        )
        _, current = self.request_json(
            "/api/analyze/scenario", {"id": "healthy_baseline", "persist": True}
        )
        path = "/api/compare/{}/{}".format(baseline["run"]["id"], current["run"]["id"])
        _, comparison = self.request_json(path)
        self.assertEqual("improved", comparison["outcome"])
        self.assertEqual(1, comparison["summary"]["resolved"])
        self.assertEqual(-8, comparison["summary"]["risk_score_delta"])
        with urlopen(self.base_url + path + "/report.md", timeout=3) as response:
            report = response.read().decode("utf-8")
        self.assertIn("整改复测报告", report)
        self.assertIn("已解决风险：1", report)

    def test_live_cannot_bind_public_interface(self) -> None:
        with self.assertRaisesRegex(ValueError, "回环地址"):
            create_server(
                "0.0.0.0",
                0,
                Path(self.temp_dir.name) / "public.db",
                CASE_DIR,
                live_enabled=True,
            )


if __name__ == "__main__":
    unittest.main()
