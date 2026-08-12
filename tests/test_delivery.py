import hashlib
import json
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

from rabbitmq_guard.delivery import (
    BUNDLE_FILES,
    FINDINGS_NAME,
    MANIFEST_NAME,
    REPORT_NAME,
    SNAPSHOT_NAME,
    _zip_bytes,
    verify_delivery_bundle,
    write_delivery_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = ROOT / "data" / "scenarios"
KEY = "correct-horse-battery-staple"


class DeliveryTests(unittest.TestCase):
    def _snapshot(self):
        return json.loads(
            (CASE_DIR / "09_quorum_lost.json").read_text(encoding="utf-8")
        )

    def test_bundle_contains_only_sanitized_diagnostic_artifacts(self) -> None:
        snapshot = self._snapshot()
        snapshot["capture"]["source"] = "https://mq.customer.internal:15672"
        snapshot["queues"][0]["arguments"] = {
            "x-dead-letter-exchange": "customer.billing.retry"
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "customer-delivery.zip"
            verification = write_delivery_bundle(snapshot, KEY, output)
            serialized = output.read_bytes()
            with zipfile.ZipFile(output, "r") as archive:
                self.assertEqual(BUNDLE_FILES, set(archive.namelist()))
                snapshot_text = archive.read(SNAPSHOT_NAME).decode("utf-8")
                findings = json.loads(archive.read(FINDINGS_NAME))
                report = archive.read(REPORT_NAME).decode("utf-8")
                manifest = json.loads(archive.read(MANIFEST_NAME))

        self.assertEqual("critical", verification["summary"]["status"])
        self.assertEqual("sanitized", json.loads(snapshot_text)["capture"]["kind"])
        self.assertEqual("queue.quorum_lost", findings["findings"][0]["rule_id"])
        self.assertIn("RabbitMQ Guard 诊断报告", report)
        self.assertFalse(manifest["privacy"]["anonymous"])
        for secret in (
            "mq.customer.internal",
            "ledger.commands",
            "rabbit@node1",
            "customer.billing.retry",
            KEY,
        ):
            self.assertNotIn(secret.encode("utf-8"), serialized)

    def test_all_scenarios_keep_expected_findings_after_delivery(self) -> None:
        for case_path in sorted(CASE_DIR.glob("*.json")):
            with self.subTest(case=case_path.name), tempfile.TemporaryDirectory() as temp_dir:
                snapshot = json.loads(case_path.read_text(encoding="utf-8"))
                output = Path(temp_dir) / "delivery.zip"
                verification = write_delivery_bundle(snapshot, KEY, output)
                with zipfile.ZipFile(output, "r") as archive:
                    results = json.loads(archive.read(FINDINGS_NAME))
                self.assertEqual(
                    set(snapshot["scenario"]["expected_findings"]),
                    {finding["rule_id"] for finding in results["findings"]},
                )
                self.assertEqual(len(results["findings"]), verification["summary"]["total"])

    def test_bundle_is_reproducible_and_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.zip"
            second = Path(temp_dir) / "second.zip"
            snapshot = self._snapshot()
            write_delivery_bundle(snapshot, KEY, first)
            write_delivery_bundle(snapshot, KEY, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with self.assertRaisesRegex(ValueError, "不会覆盖"):
                write_delivery_bundle(snapshot, KEY, first)

    def test_self_verification_failure_leaves_no_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "delivery.zip"
            with patch(
                "rabbitmq_guard.delivery.verify_delivery_bundle",
                side_effect=ValueError("verification failed"),
            ):
                with self.assertRaisesRegex(ValueError, "verification failed"):
                    write_delivery_bundle(self._snapshot(), KEY, output)

            self.assertEqual([], list(Path(temp_dir).iterdir()))

    def test_verifier_rejects_changed_payload_even_with_valid_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "delivery.zip"
            changed = Path(temp_dir) / "changed.zip"
            write_delivery_bundle(self._snapshot(), KEY, output)
            with zipfile.ZipFile(output, "r") as source:
                payloads = {name: source.read(name) for name in source.namelist()}
            payloads[REPORT_NAME] += b"\nchanged\n"
            changed.write_bytes(_zip_bytes(payloads))
            with self.assertRaisesRegex(ValueError, "完整性校验失败"):
                verify_delivery_bundle(changed)

    def test_verifier_rejects_extra_or_duplicate_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "delivery.zip"
            extra = Path(temp_dir) / "extra.zip"
            duplicate = Path(temp_dir) / "duplicate.zip"
            write_delivery_bundle(self._snapshot(), KEY, output)
            with zipfile.ZipFile(output, "r") as source, zipfile.ZipFile(
                extra, "w"
            ) as target:
                for name in source.namelist():
                    target.writestr(name, source.read(name))
                target.writestr("raw-snapshot.json", b"secret")
            with zipfile.ZipFile(output, "r") as source, zipfile.ZipFile(
                duplicate, "w"
            ) as target:
                for name in source.namelist():
                    target.writestr(name, source.read(name))
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    target.writestr(REPORT_NAME, source.read(REPORT_NAME))

            with self.assertRaisesRegex(ValueError, "固定格式"):
                verify_delivery_bundle(extra)
            with self.assertRaisesRegex(ValueError, "重复文件名"):
                verify_delivery_bundle(duplicate)

    def test_verifier_rejects_hidden_zip_metadata_and_trailing_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "delivery.zip"
            commented = Path(temp_dir) / "commented.zip"
            trailing = Path(temp_dir) / "trailing.zip"
            write_delivery_bundle(self._snapshot(), KEY, output)
            with zipfile.ZipFile(output, "r") as source, zipfile.ZipFile(
                commented, "w"
            ) as target:
                target.comment = b"hidden customer data"
                for name in source.namelist():
                    target.writestr(name, source.read(name))
            trailing.write_bytes(output.read_bytes() + b"hidden customer data")

            with self.assertRaisesRegex(ValueError, "ZIP 注释"):
                verify_delivery_bundle(commented)
            with self.assertRaisesRegex(ValueError, "规范格式"):
                verify_delivery_bundle(trailing)

    def test_verifier_rejects_raw_identifier_disguised_as_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "delivery.zip"
            forged = Path(temp_dir) / "forged.zip"
            write_delivery_bundle(self._snapshot(), KEY, output)
            with zipfile.ZipFile(output, "r") as source:
                payloads = {name: source.read(name) for name in source.namelist()}
            snapshot = json.loads(payloads[SNAPSHOT_NAME])
            snapshot["cluster"]["name"] = "customer-production"
            payloads[SNAPSHOT_NAME] = (
                json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
            manifest = json.loads(payloads[MANIFEST_NAME])
            manifest["files"][SNAPSHOT_NAME] = {
                "bytes": len(payloads[SNAPSHOT_NAME]),
                "sha256": hashlib.sha256(payloads[SNAPSHOT_NAME]).hexdigest(),
            }
            payloads[MANIFEST_NAME] = (
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
            forged.write_bytes(_zip_bytes(payloads))

            with self.assertRaisesRegex(ValueError, "不是有效伪名"):
                verify_delivery_bundle(forged)

    def test_verifier_rejects_unsupported_generator_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "delivery.zip"
            changed = Path(temp_dir) / "changed.zip"
            write_delivery_bundle(self._snapshot(), KEY, output)
            with zipfile.ZipFile(output, "r") as source:
                payloads = {name: source.read(name) for name in source.namelist()}
            manifest = json.loads(payloads[MANIFEST_NAME])
            manifest["generator"]["version"] = "9.9.9"
            payloads[MANIFEST_NAME] = (
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
            changed.write_bytes(_zip_bytes(payloads))

            with self.assertRaisesRegex(ValueError, "使用相同版本校验"):
                verify_delivery_bundle(changed)


if __name__ == "__main__":
    unittest.main()
