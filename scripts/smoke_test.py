#!/usr/bin/env python3
import json
import sys
import tempfile
import threading
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rabbitmq_guard import __version__  # noqa: E402
from rabbitmq_guard.webapp import create_server  # noqa: E402
from rabbitmq_guard.delivery import (  # noqa: E402
    verify_delivery_bundle,
    write_delivery_bundle,
    write_delivery_comparison,
)
from rabbitmq_guard.sanitizer import sanitize_snapshot  # noqa: E402


def request_json(base_url, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        base_url + path,
        data=data,
        method="GET" if payload is None else "POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=3) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        server, _ = create_server(
            "127.0.0.1",
            0,
            Path(temp_dir) / "smoke.db",
            ROOT / "src" / "rabbitmq_guard" / "scenarios",
            live_enabled=False,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = "http://127.0.0.1:{}".format(server.server_address[1])
        try:
            with urlopen(base_url + "/", timeout=3) as response:
                html = response.read().decode("utf-8")
            assert response.status == 200
            assert "RabbitMQ Guard" in html
            assert 'id="result-privacy"' in html

            _, health = request_json(base_url, "/api/health")
            assert health == {
                "ok": True,
                "version": __version__,
                "live_enabled": False,
            }

            _, result = request_json(
                base_url,
                "/api/analyze/scenario",
                {"id": "memory_alarm", "persist": True},
            )
            assert result["summary"]["status"] == "critical"
            assert result["findings"][0]["rule_id"] == "node.memory_alarm"

            run_id = result["run"]["id"]
            with urlopen(base_url + "/api/runs/{}/report.md".format(run_id), timeout=3) as response:
                report = response.read().decode("utf-8")
            assert "RabbitMQ Guard 诊断报告" in report
            assert "集群：lab" in report

            raw_snapshot = json.loads(
                (
                    ROOT
                    / "src"
                    / "rabbitmq_guard"
                    / "scenarios"
                    / "09_quorum_lost.json"
                ).read_text(
                    encoding="utf-8"
                )
            )
            sanitized = sanitize_snapshot(raw_snapshot, "smoke-test-key-2026")
            serialized = json.dumps(sanitized, ensure_ascii=False)
            assert "ledger.commands" not in serialized
            assert "rabbit@node1" not in serialized
            assert "scenario" not in sanitized
            _, sanitized_result = request_json(
                base_url,
                "/api/analyze/snapshot",
                {"snapshot": sanitized, "label": "sanitized-smoke.json"},
            )
            assert sanitized_result["capture"]["kind"] == "sanitized"
            assert sanitized_result["findings"][0]["rule_id"] == "queue.quorum_lost"

            delivery_path = Path(temp_dir) / "customer-delivery.zip"
            delivery = write_delivery_bundle(
                raw_snapshot, "smoke-test-key-2026", delivery_path
            )
            assert delivery["summary"]["status"] == "critical"
            assert len(delivery["bundle_sha256"]) == 64
            verified = verify_delivery_bundle(delivery_path)
            assert verified["bundle_sha256"] == delivery["bundle_sha256"]
            assert "ledger.commands" not in delivery_path.read_bytes().decode("latin-1")

            baseline_snapshot = json.loads(
                (
                    ROOT
                    / "src"
                    / "rabbitmq_guard"
                    / "scenarios"
                    / "05_memory_alarm.json"
                ).read_text(
                    encoding="utf-8"
                )
            )
            current_snapshot = json.loads(
                (
                    ROOT
                    / "src"
                    / "rabbitmq_guard"
                    / "scenarios"
                    / "00_healthy_baseline.json"
                ).read_text(
                    encoding="utf-8"
                )
            )
            baseline_snapshot["capture"]["captured_at"] = "2026-08-01T08:00:00Z"
            current_snapshot["capture"]["captured_at"] = "2026-08-08T08:00:00Z"
            baseline_delivery = Path(temp_dir) / "baseline-delivery.zip"
            current_delivery = Path(temp_dir) / "current-delivery.zip"
            comparison_output = Path(temp_dir) / "delivery-comparison.md"
            write_delivery_bundle(
                baseline_snapshot, "smoke-test-key-2026", baseline_delivery
            )
            write_delivery_bundle(
                current_snapshot, "smoke-test-key-2026", current_delivery
            )
            delivery_comparison = write_delivery_comparison(
                baseline_delivery, current_delivery, comparison_output
            )
            assert delivery_comparison["outcome"] == "improved"
            assert delivery_comparison["summary"]["resolved"] == 1
            assert "已验证输入" in comparison_output.read_text(encoding="utf-8")

            _, healthy = request_json(
                base_url,
                "/api/analyze/scenario",
                {"id": "healthy_baseline", "persist": True},
            )
            comparison_path = "/api/compare/{}/{}".format(run_id, healthy["run"]["id"])
            _, comparison = request_json(base_url, comparison_path)
            assert comparison["outcome"] == "improved"
            assert comparison["summary"]["resolved"] == 1
            with urlopen(base_url + comparison_path + "/report.md", timeout=3) as response:
                comparison_report = response.read().decode("utf-8")
            assert "RabbitMQ Guard 整改复测报告" in comparison_report
            assert "已解决风险：1" in comparison_report
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
    print("smoke test passed")


if __name__ == "__main__":
    main()
