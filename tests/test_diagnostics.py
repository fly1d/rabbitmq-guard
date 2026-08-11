import json
import tempfile
import unittest
from pathlib import Path

from rabbitmq_guard.collector import normalize_payloads
from rabbitmq_guard.diagnostics import diagnose, render_json, render_markdown
from rabbitmq_guard.generator import generate_variants, load_cases, write_jsonl


ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = ROOT / "data" / "scenarios"


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


if __name__ == "__main__":
    unittest.main()
