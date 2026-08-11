#!/usr/bin/env python3
import json
import sys
import tempfile
import threading
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rabbitmq_guard.webapp import create_server  # noqa: E402


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
            ROOT / "data" / "scenarios",
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

            _, health = request_json(base_url, "/api/health")
            assert health == {"ok": True, "version": "0.2.0", "live_enabled": False}

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
