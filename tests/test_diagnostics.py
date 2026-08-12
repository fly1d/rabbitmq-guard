import json
import math
import tempfile
import unittest
from pathlib import Path

from rabbitmq_guard.collector import normalize_payloads
from rabbitmq_guard.diagnostics import diagnose, render_json, render_markdown
from rabbitmq_guard.generator import generate_variants, load_cases, write_jsonl
from rabbitmq_guard.sanitizer import sanitize_snapshot


ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = ROOT / "src" / "rabbitmq_guard" / "scenarios"


class DiagnosticsTests(unittest.TestCase):
    def test_all_scenarios_include_expected_findings(self) -> None:
        for path in sorted(CASE_DIR.glob("*.json")):
            with self.subTest(path=path.name):
                snapshot = json.loads(path.read_text(encoding="utf-8"))
                actual = {finding.rule_id for finding in diagnose(snapshot)}
                expected = set(snapshot["scenario"]["expected_findings"])
                self.assertTrue(
                    expected.issubset(actual),
                    "{} expected {}, got {}".format(path.name, expected, actual),
                )

    def test_healthy_baseline_has_no_findings(self) -> None:
        snapshot = json.loads(
            (CASE_DIR / "00_healthy_baseline.json").read_text(encoding="utf-8")
        )
        self.assertEqual([], diagnose(snapshot))

    def test_reports_are_machine_and_human_readable(self) -> None:
        snapshot = json.loads(
            (CASE_DIR / "05_memory_alarm.json").read_text(encoding="utf-8")
        )
        findings = diagnose(snapshot)
        machine_report = json.loads(render_json(findings))
        human_report = render_markdown(findings)
        self.assertEqual("node.memory_alarm", machine_report["findings"][0]["rule_id"])
        self.assertIn("RabbitMQ Guard 诊断报告", human_report)
        self.assertIn("https://www.rabbitmq.com/docs/alarms", human_report)

    def test_collector_normalizes_management_api_fields(self) -> None:
        snapshot = normalize_payloads(
            "http://localhost:15672",
            {
                "cluster_name": "test",
                "object_totals": {"connections": 1, "channels": 2, "queues": 1, "consumers": 1},
            },
            [
                {
                    "name": "rabbit@node1",
                    "mem_alarm": False,
                    "disk_free_alarm": False,
                    "fd_used": 100,
                    "fd_total": 4096,
                }
            ],
            [
                {
                    "vhost": "/",
                    "name": "jobs",
                    "type": "quorum",
                    "messages": 3,
                    "messages_ready": 2,
                    "messages_unacknowledged": 1,
                    "consumers": 1,
                    "consumer_capacity": 0.75,
                    "members": ["rabbit@node1", "rabbit@node2", "rabbit@node3"],
                    "online": ["rabbit@node1", "rabbit@node2", "rabbit@node3"],
                    "message_stats": {
                        "publish_details": {"rate": 5.0},
                        "deliver_get_details": {"rate": 4.0},
                        "ack_details": {"rate": 3.9},
                    },
                }
            ],
            [{"name": "client", "state": "running", "channels": 2}],
        )
        queue = snapshot["queues"][0]
        self.assertEqual(5.0, queue["publish_rate"])
        self.assertEqual(3, len(queue["online_members"]))
        self.assertEqual("live-read-only", snapshot["capture"]["kind"])

    def test_synthetic_generator_is_deterministic(self) -> None:
        cases = load_cases(CASE_DIR)[:2]
        first = generate_variants(cases, 2, 42)
        second = generate_variants(cases, 2, 42)
        self.assertEqual(first, second)
        self.assertEqual(4, len(first))
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "dataset.jsonl"
            self.assertEqual(4, write_jsonl(output, first))
            self.assertEqual(4, len(output.read_text(encoding="utf-8").splitlines()))

    def test_sanitizer_removes_identifiers_and_preserves_diagnostics(self) -> None:
        snapshot = json.loads(
            (CASE_DIR / "09_quorum_lost.json").read_text(encoding="utf-8")
        )
        snapshot["capture"]["source"] = "https://mq.internal.example:15672"
        snapshot["capture"]["kind"] = "Acme production capture"
        snapshot["capture"]["captured_at"] = "customer launch window"
        snapshot["queues"][0]["arguments"] = {
            "x-dead-letter-exchange": "customer.billing.retry"
        }
        snapshot["queues"][0]["leader"] = "rabbit@node2"
        snapshot["connections"] = [
            {
                "name": "10.0.1.5:51234 -> 10.0.2.8:5672",
                "state": "blocked",
                "channels": 3,
                "user": "payments-service",
                "client": "billing-worker-prod",
            }
        ]
        snapshot["internal_note"] = "Acme production cluster"

        sanitized = sanitize_snapshot(snapshot, "correct-horse-battery-staple")
        serialized = json.dumps(sanitized, ensure_ascii=False)
        for secret in (
            "mq.internal.example",
            "ledger.commands",
            "rabbit@node1",
            "customer.billing.retry",
            "payments-service",
            "billing-worker-prod",
            "Acme production cluster",
            "Acme production capture",
            "customer launch window",
        ):
            self.assertNotIn(secret, serialized)
        self.assertNotIn("correct-horse-battery-staple", serialized)

        self.assertEqual("sanitized", sanitized["capture"]["kind"])
        self.assertNotIn("arguments", sanitized["queues"][0])
        self.assertNotIn("internal_note", sanitized)
        self.assertEqual("unknown", sanitized["capture"]["original_kind"])
        self.assertIsNone(sanitized["capture"]["captured_at"])
        self.assertTrue(sanitized["capture"]["source"].startswith("source-"))
        self.assertEqual(
            sanitized["nodes"][0]["name"], sanitized["queues"][0]["members"][0]
        )
        self.assertEqual(sanitized["nodes"][1]["name"], sanitized["queues"][0]["leader"])
        self.assertEqual(
            {item.rule_id for item in diagnose(snapshot)},
            {item.rule_id for item in diagnose(sanitized)},
        )

    def test_sanitizer_is_stable_for_the_same_key(self) -> None:
        snapshot = json.loads(
            (CASE_DIR / "02_growing_backlog.json").read_text(encoding="utf-8")
        )
        first = sanitize_snapshot(snapshot, "0123456789abcdef")
        second = sanitize_snapshot(snapshot, "0123456789abcdef")
        different = sanitize_snapshot(snapshot, "fedcba9876543210")
        self.assertEqual(first, second)
        self.assertNotEqual(first["cluster"]["name"], different["cluster"]["name"])

    def test_sanitizer_rejects_short_keys(self) -> None:
        with self.assertRaisesRegex(ValueError, "至少需要 16"):
            sanitize_snapshot({"cluster": {}}, "too-short")

    def test_sanitizer_rejects_an_already_sanitized_snapshot(self) -> None:
        with self.assertRaisesRegex(ValueError, "已经脱敏"):
            sanitize_snapshot(
                {"capture": {"kind": "sanitized"}}, "0123456789abcdef"
            )

    def test_sanitizer_rejects_non_finite_metrics(self) -> None:
        snapshot = {
            "cluster": {"name": "production", "connections": math.inf},
            "nodes": [{"running": "false", "mem_alarm": "true"}],
            "queues": [{"durable": "false", "messages": True}],
            "activity": {"connection_open_rate": math.nan},
        }
        sanitized = sanitize_snapshot(snapshot, "0123456789abcdef")
        self.assertIsNone(sanitized["cluster"]["connections"])
        self.assertIsNone(sanitized["activity"]["connection_open_rate"])
        self.assertTrue(sanitized["nodes"][0]["running"])
        self.assertFalse(sanitized["nodes"][0]["mem_alarm"])
        self.assertFalse(sanitized["queues"][0]["durable"])
        self.assertIsNone(sanitized["queues"][0]["messages"])

    def test_sanitizer_normalizes_invalid_relationship_lists(self) -> None:
        snapshot = {
            "cluster": {"name": "production"},
            "queues": [
                {
                    "name": "orders",
                    "members": "rabbit@node1",
                    "online_members": {"customer": "secret"},
                    "leader": ["customer-secret"],
                }
            ],
        }
        sanitized = sanitize_snapshot(snapshot, "0123456789abcdef")
        queue = sanitized["queues"][0]
        self.assertEqual([], queue["members"])
        self.assertEqual([], queue["online_members"])
        self.assertTrue(queue["leader"].startswith("node-"))
        self.assertNotIn("customer-secret", json.dumps(sanitized))


if __name__ == "__main__":
    unittest.main()
