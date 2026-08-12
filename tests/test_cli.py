import json
import os
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from rabbitmq_guard.cli import main


ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = ROOT / "data" / "scenarios"


class CliTests(unittest.TestCase):
    def test_sanitize_command_writes_diagnostic_snapshot(self) -> None:
        source = CASE_DIR / "09_quorum_lost.json"
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "sanitized.json"
            with patch.dict(
                os.environ,
                {"TEST_REDACTION_KEY": "correct-horse-battery-staple"},
                clear=False,
            ), redirect_stdout(StringIO()) as stdout:
                main(
                    [
                        "sanitize",
                        str(source),
                        "--output",
                        str(output),
                        "--redaction-key-env",
                        "TEST_REDACTION_KEY",
                    ]
                )

            snapshot = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("sanitized", snapshot["capture"]["kind"])
            self.assertNotIn("scenario", snapshot)
            self.assertNotIn("ledger.commands", output.read_text(encoding="utf-8"))
            self.assertIn("已写入脱敏快照", stdout.getvalue())

    def test_collect_can_write_only_a_sanitized_snapshot(self) -> None:
        snapshot = json.loads(
            (CASE_DIR / "02_growing_backlog.json").read_text(encoding="utf-8")
        )
        snapshot["capture"].update(
            {
                "kind": "live-read-only",
                "captured_at": "2026-08-11T12:00:00+00:00",
                "source": "https://mq.customer.internal:15672",
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "collected-sanitized.json"
            with patch.dict(
                os.environ,
                {"TEST_REDACTION_KEY": "correct-horse-battery-staple"},
                clear=False,
            ), patch(
                "rabbitmq_guard.cli.ManagementApiCollector.from_env"
            ) as from_env, redirect_stdout(StringIO()) as stdout:
                from_env.return_value.collect.return_value = snapshot
                main(
                    [
                        "collect",
                        "--url",
                        "https://mq.customer.internal:15672",
                        "--username",
                        "monitor",
                        "--sanitize",
                        "--redaction-key-env",
                        "TEST_REDACTION_KEY",
                        "--output",
                        str(output),
                    ]
                )

            serialized = output.read_text(encoding="utf-8")
            sanitized = json.loads(serialized)
            self.assertNotIn("mq.customer.internal", serialized)
            self.assertEqual("sanitized", sanitized["capture"]["kind"])
            self.assertEqual(
                "2026-08-11T12:00:00+00:00", sanitized["capture"]["captured_at"]
            )
            self.assertIn("已采集并脱敏快照", stdout.getvalue())

    def test_sanitize_command_reports_missing_key_without_writing(self) -> None:
        source = CASE_DIR / "00_healthy_baseline.json"
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "sanitized.json"
            with patch.dict(os.environ, {}, clear=True), redirect_stderr(
                StringIO()
            ) as stderr:
                with self.assertRaisesRegex(SystemExit, "2"):
                    main(["sanitize", str(source), "--output", str(output)])

            self.assertFalse(output.exists())
            self.assertIn("RABBITMQ_GUARD_REDACTION_KEY 未设置", stderr.getvalue())

    def test_collect_sanitize_requires_key_before_network_collection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "sanitized.json"
            with patch.dict(os.environ, {}, clear=True), patch(
                "rabbitmq_guard.cli.ManagementApiCollector.from_env"
            ) as from_env, redirect_stderr(StringIO()):
                with self.assertRaisesRegex(SystemExit, "2"):
                    main(["collect", "--sanitize", "--output", str(output)])

            from_env.assert_not_called()
            self.assertFalse(output.exists())

    def test_deliver_snapshot_and_verify_commands(self) -> None:
        source = CASE_DIR / "09_quorum_lost.json"
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "delivery.zip"
            with patch.dict(
                os.environ,
                {"TEST_REDACTION_KEY": "correct-horse-battery-staple"},
                clear=False,
            ), redirect_stdout(StringIO()) as deliver_stdout:
                main(
                    [
                        "deliver",
                        "--snapshot",
                        str(source),
                        "--redaction-key-env",
                        "TEST_REDACTION_KEY",
                        "--output",
                        str(output),
                    ]
                )
            with redirect_stdout(StringIO()) as verify_stdout:
                main(["verify-delivery", str(output)])

            self.assertTrue(output.is_file())
            self.assertIn("SHA-256:", deliver_stdout.getvalue())
            self.assertIn("交付包校验通过", verify_stdout.getvalue())
            with zipfile.ZipFile(output, "r") as archive:
                self.assertEqual("sanitized", json.loads(archive.read("snapshot.sanitized.json"))["capture"]["kind"])

    def test_deliver_collects_without_writing_raw_snapshot(self) -> None:
        snapshot = json.loads(
            (CASE_DIR / "02_growing_backlog.json").read_text(encoding="utf-8")
        )
        snapshot["capture"].update(
            {
                "kind": "live-read-only",
                "source": "https://mq.customer.internal:15672",
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "delivery.zip"
            with patch.dict(
                os.environ,
                {"TEST_REDACTION_KEY": "correct-horse-battery-staple"},
                clear=False,
            ), patch(
                "rabbitmq_guard.cli.ManagementApiCollector.from_env"
            ) as from_env, redirect_stdout(StringIO()):
                from_env.return_value.collect.return_value = snapshot
                main(
                    [
                        "deliver",
                        "--url",
                        "https://mq.customer.internal:15672",
                        "--username",
                        "monitor",
                        "--redaction-key-env",
                        "TEST_REDACTION_KEY",
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual([output], list(Path(temp_dir).iterdir()))
            self.assertNotIn("mq.customer.internal", output.read_bytes().decode("latin-1"))

    def test_deliver_requires_key_before_network_collection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "delivery.zip"
            with patch.dict(os.environ, {}, clear=True), patch(
                "rabbitmq_guard.cli.ManagementApiCollector.from_env"
            ) as from_env, redirect_stderr(StringIO()):
                with self.assertRaisesRegex(SystemExit, "2"):
                    main(
                        [
                            "deliver",
                            "--url",
                            "https://mq.customer.internal:15672",
                            "--output",
                            str(output),
                        ]
                    )

            from_env.assert_not_called()
            self.assertFalse(output.exists())

    def test_deliver_rejects_existing_output_before_network_collection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "delivery.zip"
            output.write_bytes(b"existing")
            with patch.dict(
                os.environ,
                {"RABBITMQ_GUARD_REDACTION_KEY": "correct-horse-battery-staple"},
                clear=False,
            ), patch(
                "rabbitmq_guard.cli.ManagementApiCollector.from_env"
            ) as from_env, redirect_stderr(StringIO()):
                with self.assertRaisesRegex(SystemExit, "2"):
                    main(
                        [
                            "deliver",
                            "--url",
                            "https://mq.customer.internal:15672",
                            "--output",
                            str(output),
                        ]
                    )

            from_env.assert_not_called()
            self.assertEqual(b"existing", output.read_bytes())


if __name__ == "__main__":
    unittest.main()
