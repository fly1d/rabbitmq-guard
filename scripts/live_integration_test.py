#!/usr/bin/env python3
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rabbitmq_guard.collector import ManagementApiCollector  # noqa: E402
from rabbitmq_guard.diagnostics import diagnose  # noqa: E402


def main():
    collector = ManagementApiCollector.from_env(
        os.environ.get("RABBITMQ_URL", "http://127.0.0.1:15672"),
        "guard",
        "RABBITMQ_PASSWORD",
    )
    snapshot = collector.collect()
    findings = diagnose(snapshot)
    rule_ids = {finding.rule_id for finding in findings}
    if "queue.no_consumers" not in rule_ids:
        raise SystemExit("expected queue.no_consumers, got {}".format(sorted(rule_ids)))
    if os.environ.get("RABBITMQ_PASSWORD") in str(snapshot):
        raise SystemExit("password leaked into normalized snapshot")
    print("live integration test passed")


if __name__ == "__main__":
    main()
