import hashlib
import io
import json
import math
import os
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import __version__
from .comparison import compare_runs, render_comparison_markdown
from .diagnostics import diagnose, render_markdown
from .models import Finding
from .sanitizer import sanitize_snapshot


BUNDLE_FORMAT = "rabbitmq-guard-delivery"
BUNDLE_FORMAT_VERSION = "1.0"
SNAPSHOT_NAME = "snapshot.sanitized.json"
FINDINGS_NAME = "findings.json"
REPORT_NAME = "report.md"
MANIFEST_NAME = "manifest.json"
BUNDLE_FILES = {SNAPSHOT_NAME, FINDINGS_NAME, REPORT_NAME, MANIFEST_NAME}
MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
PSEUDONYM_PATTERN = re.compile(
    r"^(cluster|node|vhost|queue|connection|user|client|source)-[0-9a-f]{16}$"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
MANIFEST_KEYS = {"format", "format_version", "generator", "privacy", "files"}
CAPTURE_KEYS = {"kind", "original_kind", "captured_at", "source"}
PRIVACY_KEYS = {
    "method",
    "identifier_length",
    "stable_across_runs",
    "removed_fields",
}
CLUSTER_KEYS = {"name", "connections", "channels", "queues", "consumers"}
NODE_KEYS = {
    "name",
    "running",
    "mem_alarm",
    "mem_used",
    "mem_limit",
    "disk_free_alarm",
    "disk_free",
    "disk_free_limit",
    "fd_used",
    "fd_total",
}
QUEUE_KEYS = {
    "vhost",
    "name",
    "type",
    "state",
    "durable",
    "messages",
    "messages_ready",
    "messages_unacknowledged",
    "consumers",
    "consumer_capacity",
    "publish_rate",
    "deliver_rate",
    "ack_rate",
    "redeliver_rate",
    "members",
    "online_members",
    "leader",
}
CONNECTION_KEYS = {"name", "state", "channels", "user", "client"}
ACTIVITY_KEYS = {"connection_open_rate"}


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _require_keys(value: Any, expected: set, name: str) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("{} 字段不符合脱敏格式".format(name))
    return value


def _require_metric(value: Any, name: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("{} 必须是有限数值或 null".format(name))
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("{} 必须是有限数值或 null".format(name))


def _require_boolean(value: Any, name: str) -> None:
    if not isinstance(value, bool):
        raise ValueError("{} 必须是 boolean".format(name))


def _require_pseudonym(value: Any, category: str, name: str) -> None:
    if not isinstance(value, str) or not PSEUDONYM_PATTERN.fullmatch(value):
        raise ValueError("{} 不是有效伪名".format(name))
    if not value.startswith(category + "-"):
        raise ValueError("{} 伪名类别无效".format(name))


def _valid_timestamp(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _validate_source_snapshot(snapshot: Any) -> Dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ValueError("快照必须是 JSON object")
    if not isinstance(snapshot.get("cluster"), dict):
        raise ValueError("快照缺少 cluster object")
    for name in ("nodes", "queues", "connections"):
        if name in snapshot and not isinstance(snapshot[name], list):
            raise ValueError("{} 必须是 array".format(name))
    for name in ("capture", "activity"):
        if name in snapshot and not isinstance(snapshot[name], dict):
            raise ValueError("{} 必须是 object".format(name))
    return snapshot


def _validate_sanitized_snapshot(snapshot: Dict[str, Any]) -> None:
    _require_keys(
        snapshot,
        {"schema_version", "capture", "privacy", "cluster", "nodes", "queues", "connections", "activity"},
        "snapshot",
    )
    if snapshot.get("schema_version") != "1.0":
        raise ValueError("快照 schema_version 无效")
    capture = _require_keys(snapshot["capture"], CAPTURE_KEYS, "capture")
    if capture["kind"] != "sanitized":
        raise ValueError("交付包快照未标记为脱敏")
    if capture["original_kind"] not in {
        "live-read-only",
        "synthetic-baseline",
        "synthetic-variant",
        "unknown",
    }:
        raise ValueError("capture.original_kind 无效")
    if not _valid_timestamp(capture["captured_at"]):
        raise ValueError("capture.captured_at 无效")
    _require_pseudonym(capture["source"], "source", "capture.source")

    privacy = _require_keys(snapshot["privacy"], PRIVACY_KEYS, "privacy")
    if privacy != {
        "method": "hmac-sha256",
        "identifier_length": 16,
        "stable_across_runs": True,
        "removed_fields": ["scenario", "queues[].arguments", "unknown fields"],
    }:
        raise ValueError("交付包快照脱敏元数据无效")

    cluster = _require_keys(snapshot["cluster"], CLUSTER_KEYS, "cluster")
    _require_pseudonym(cluster["name"], "cluster", "cluster.name")
    for name in CLUSTER_KEYS - {"name"}:
        _require_metric(cluster[name], "cluster.{}".format(name))

    nodes = snapshot["nodes"]
    if not isinstance(nodes, list):
        raise ValueError("nodes 必须是 array")
    for index, node_value in enumerate(nodes):
        node = _require_keys(node_value, NODE_KEYS, "nodes[{}]".format(index))
        _require_pseudonym(node["name"], "node", "nodes[{}].name".format(index))
        for name in ("running", "mem_alarm", "disk_free_alarm"):
            _require_boolean(node[name], "nodes[{}].{}".format(index, name))
        for name in NODE_KEYS - {"name", "running", "mem_alarm", "disk_free_alarm"}:
            _require_metric(node[name], "nodes[{}].{}".format(index, name))

    queues = snapshot["queues"]
    if not isinstance(queues, list):
        raise ValueError("queues 必须是 array")
    for index, queue_value in enumerate(queues):
        queue = _require_keys(queue_value, QUEUE_KEYS, "queues[{}]".format(index))
        _require_pseudonym(queue["vhost"], "vhost", "queues[{}].vhost".format(index))
        _require_pseudonym(queue["name"], "queue", "queues[{}].name".format(index))
        if queue["type"] not in {"classic", "quorum", "stream", "unknown"}:
            raise ValueError("queues[{}].type 无效".format(index))
        if queue["state"] not in {
            "running",
            "idle",
            "down",
            "crashed",
            "flow",
            "syncing",
            "unknown",
        }:
            raise ValueError("queues[{}].state 无效".format(index))
        _require_boolean(queue["durable"], "queues[{}].durable".format(index))
        for name in QUEUE_KEYS - {
            "vhost",
            "name",
            "type",
            "state",
            "durable",
            "members",
            "online_members",
            "leader",
        }:
            _require_metric(queue[name], "queues[{}].{}".format(index, name))
        for name in ("members", "online_members"):
            members = queue[name]
            if not isinstance(members, list):
                raise ValueError("queues[{}].{} 必须是 array".format(index, name))
            for member_index, member in enumerate(members):
                _require_pseudonym(
                    member,
                    "node",
                    "queues[{}].{}[{}]".format(index, name, member_index),
                )
        if queue["leader"] is not None:
            _require_pseudonym(queue["leader"], "node", "queues[{}].leader".format(index))

    connections = snapshot["connections"]
    if not isinstance(connections, list):
        raise ValueError("connections 必须是 array")
    for index, connection_value in enumerate(connections):
        connection = _require_keys(
            connection_value, CONNECTION_KEYS, "connections[{}]".format(index)
        )
        for name in ("name", "user", "client"):
            _require_pseudonym(
                connection[name],
                "connection" if name == "name" else name,
                "connections[{}].{}".format(index, name),
            )
        if connection["state"] not in {
            "running",
            "blocked",
            "blocking",
            "flow",
            "closing",
            "closed",
            "starting",
            "tuning",
            "opening",
            "unknown",
        }:
            raise ValueError("connections[{}].state 无效".format(index))
        _require_metric(connection["channels"], "connections[{}].channels".format(index))

    activity = _require_keys(snapshot["activity"], ACTIVITY_KEYS, "activity")
    _require_metric(activity["connection_open_rate"], "activity.connection_open_rate")


def _summary(findings: List[Finding]) -> Dict[str, Any]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for finding in findings:
        counts[finding.severity] += 1
    if counts["critical"]:
        status = "critical"
    elif counts["high"]:
        status = "high"
    elif counts["medium"] or counts["low"]:
        status = "attention"
    else:
        status = "healthy"
    return {"status": status, "counts": counts, "total": sum(counts.values())}


def _report_context(snapshot: Dict[str, Any], summary: Dict[str, Any]) -> Dict[str, Any]:
    capture = snapshot.get("capture") or {}
    cluster = snapshot.get("cluster") or {}
    return {
        "label": "客户脱敏快照",
        "cluster_name": cluster.get("name", "unknown"),
        "created_at": capture.get("captured_at") or "-",
        "source_kind": "sanitized-delivery",
        "counts": summary["counts"],
    }


def build_delivery_files(snapshot: Dict[str, Any], key: str) -> Dict[str, bytes]:
    sanitized = sanitize_snapshot(_validate_source_snapshot(snapshot), key)
    findings = diagnose(sanitized)
    summary = _summary(findings)
    results = {
        "schema_version": "1.0",
        "summary": summary,
        "findings": [finding.to_dict() for finding in findings],
    }
    report = render_markdown(findings, _report_context(sanitized, summary))
    payloads = {
        SNAPSHOT_NAME: _json_bytes(sanitized),
        FINDINGS_NAME: _json_bytes(results),
        REPORT_NAME: report.encode("utf-8"),
    }
    manifest = {
        "format": BUNDLE_FORMAT,
        "format_version": BUNDLE_FORMAT_VERSION,
        "generator": {"name": "rabbitmq-guard", "version": __version__},
        "privacy": {
            "snapshot_kind": "sanitized",
            "method": "hmac-sha256",
            "anonymous": False,
        },
        "files": {
            name: {"sha256": _sha256(content), "bytes": len(content)}
            for name, content in sorted(payloads.items())
        },
    }
    payloads[MANIFEST_NAME] = _json_bytes(manifest)
    return payloads


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o600 << 16
    return info


def _zip_bytes(payloads: Dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(payloads):
            archive.writestr(_zip_info(name), payloads[name])
    return output.getvalue()


def write_delivery_bundle(
    snapshot: Dict[str, Any], key: str, output: Path
) -> Dict[str, Any]:
    if output.exists():
        raise ValueError("输出文件已存在，不会覆盖: {}".format(output))
    payloads = build_delivery_files(snapshot, key)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".rabbitmq-guard-delivery-",
            suffix=".zip",
            dir=str(output.parent),
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        temporary_path.write_bytes(_zip_bytes(payloads))
        verification = verify_delivery_bundle(temporary_path)
        try:
            os.link(str(temporary_path), str(output))
        except FileExistsError as exc:
            raise ValueError("输出文件已存在，不会覆盖: {}".format(output)) from exc
        temporary_path.unlink()
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
    return verification


def _read_json(content: bytes, name: str) -> Dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("{} 不是有效 UTF-8 JSON".format(name)) from exc
    if not isinstance(value, dict):
        raise ValueError("{} 必须是 JSON object".format(name))
    return value


def _read_bundle(path: Path) -> Tuple[Dict[str, bytes], bytes]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_ARCHIVE_BYTES + 1)
    except (FileNotFoundError, IsADirectoryError) as exc:
        raise ValueError("交付包不存在或不是文件: {}".format(path)) from exc
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError("交付包超过 10MB 限制")
    try:
        with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
            entries = archive.infolist()
            if archive.comment:
                raise ValueError("交付包不能包含 ZIP 注释")
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)):
                raise ValueError("交付包包含重复文件名")
            if set(names) != BUNDLE_FILES:
                raise ValueError("交付包文件清单不符合固定格式")
            if any(entry.is_dir() for entry in entries):
                raise ValueError("交付包不能包含目录")
            if sum(entry.file_size for entry in entries) > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("交付包解压内容超过 20MB 限制")
            payloads = {entry.filename: archive.read(entry) for entry in entries}
        if raw != _zip_bytes(payloads):
            raise ValueError("交付包 ZIP 容器不符合规范格式")
        return payloads, raw
    except (RuntimeError, zipfile.BadZipFile) as exc:
        raise ValueError("交付包不是有效 ZIP 文件") from exc


def load_verified_delivery_bundle(path: Path) -> Dict[str, Any]:
    payloads, bundle_content = _read_bundle(path)
    manifest = _read_json(payloads[MANIFEST_NAME], MANIFEST_NAME)
    _require_keys(manifest, MANIFEST_KEYS, "manifest")
    if manifest.get("format") != BUNDLE_FORMAT:
        raise ValueError("交付包格式标识无效")
    if manifest.get("format_version") != BUNDLE_FORMAT_VERSION:
        raise ValueError("不支持的交付包格式版本")
    generator = _require_keys(manifest.get("generator"), {"name", "version"}, "generator")
    if generator.get("name") != "rabbitmq-guard" or not isinstance(
        generator.get("version"), str
    ) or not VERSION_PATTERN.fullmatch(generator["version"]):
        raise ValueError("manifest 生成器信息无效")
    if generator["version"] != __version__:
        raise ValueError(
            "交付包由 v{} 生成，请使用相同版本校验".format(generator["version"])
        )
    manifest_privacy = _require_keys(
        manifest.get("privacy"), {"snapshot_kind", "method", "anonymous"}, "manifest.privacy"
    )
    if manifest_privacy != {
        "snapshot_kind": "sanitized",
        "method": "hmac-sha256",
        "anonymous": False,
    }:
        raise ValueError("manifest 隐私声明无效")
    files = manifest.get("files")
    expected_payload_names = BUNDLE_FILES - {MANIFEST_NAME}
    if not isinstance(files, dict) or set(files) != expected_payload_names:
        raise ValueError("manifest 文件清单无效")
    for name in sorted(expected_payload_names):
        metadata = files.get(name)
        if not isinstance(metadata, dict) or set(metadata) != {"bytes", "sha256"}:
            raise ValueError("manifest 缺少 {} 元数据".format(name))
        if (
            isinstance(metadata.get("bytes"), bool)
            or not isinstance(metadata.get("bytes"), int)
            or metadata["bytes"] < 0
            or not isinstance(metadata.get("sha256"), str)
            or not SHA256_PATTERN.fullmatch(metadata["sha256"])
        ):
            raise ValueError("manifest 的 {} 元数据无效".format(name))
        content = payloads[name]
        if metadata.get("bytes") != len(content) or metadata.get("sha256") != _sha256(
            content
        ):
            raise ValueError("{} 完整性校验失败".format(name))

    snapshot = _read_json(payloads[SNAPSHOT_NAME], SNAPSHOT_NAME)
    _validate_sanitized_snapshot(snapshot)

    results = _read_json(payloads[FINDINGS_NAME], FINDINGS_NAME)
    findings = diagnose(snapshot)
    summary = _summary(findings)
    expected_results = {
        "schema_version": "1.0",
        "summary": summary,
        "findings": [finding.to_dict() for finding in findings],
    }
    if results != expected_results:
        raise ValueError("诊断结果与脱敏快照不一致")
    expected_report = render_markdown(
        findings, _report_context(snapshot, summary)
    ).encode("utf-8")
    if payloads[REPORT_NAME] != expected_report:
        raise ValueError("Markdown 报告与诊断结果不一致")
    return {
        "valid": True,
        "format_version": BUNDLE_FORMAT_VERSION,
        "generator_version": generator["version"],
        "cluster_name": (snapshot.get("cluster") or {}).get("name", "unknown"),
        "summary": summary,
        "bundle_sha256": _sha256(bundle_content),
        "bundle_bytes": len(bundle_content),
        "snapshot": snapshot,
        "findings": expected_results["findings"],
    }


def verify_delivery_bundle(path: Path) -> Dict[str, Any]:
    delivery = load_verified_delivery_bundle(path)
    return {
        key: value
        for key, value in delivery.items()
        if key not in {"snapshot", "findings"}
    }


def _delivery_run(delivery: Dict[str, Any], label: str) -> Dict[str, Any]:
    snapshot = delivery["snapshot"]
    capture = snapshot.get("capture") or {}
    summary = delivery["summary"]
    return {
        "id": delivery["bundle_sha256"],
        "created_at": capture.get("captured_at") or "-",
        "label": label,
        "source_kind": "sanitized-delivery",
        "cluster_name": delivery["cluster_name"],
        "status": summary["status"],
        "counts": summary["counts"],
        "total": summary["total"],
        "snapshot": snapshot,
        "findings": delivery["findings"],
    }


def _normalized_capture_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def compare_delivery_bundles(
    baseline_path: Path, current_path: Path
) -> Dict[str, Any]:
    baseline = load_verified_delivery_bundle(baseline_path)
    current = load_verified_delivery_bundle(current_path)
    if baseline["bundle_sha256"] == current["bundle_sha256"]:
        raise ValueError("基线和复测交付包不能相同")
    if baseline["cluster_name"] != current["cluster_name"]:
        raise ValueError("只能比较使用同一脱敏密钥生成的同一 RabbitMQ 集群交付包")
    baseline_source = (baseline["snapshot"].get("capture") or {}).get("source")
    current_source = (current["snapshot"].get("capture") or {}).get("source")
    if baseline_source != current_source:
        raise ValueError("只能比较来自同一脱敏采集源的交付包")

    baseline_time = _normalized_capture_time(
        (baseline["snapshot"].get("capture") or {}).get("captured_at")
    )
    current_time = _normalized_capture_time(
        (current["snapshot"].get("capture") or {}).get("captured_at")
    )
    if (
        baseline_time is not None
        and current_time is not None
        and current_time < baseline_time
    ):
        raise ValueError("复测交付包的采集时间早于基线交付包")

    comparison = compare_runs(
        _delivery_run(baseline, "客户基线交付包"),
        _delivery_run(current, "客户复测交付包"),
    )
    comparison["schema_version"] = "1.0"
    comparison["delivery_bundles"] = {
        "baseline": {
            "sha256": baseline["bundle_sha256"],
            "bytes": baseline["bundle_bytes"],
            "generator_version": baseline["generator_version"],
        },
        "current": {
            "sha256": current["bundle_sha256"],
            "bytes": current["bundle_bytes"],
            "generator_version": current["generator_version"],
        },
    }
    return comparison


def write_delivery_comparison(
    baseline_path: Path,
    current_path: Path,
    output: Path,
    output_format: str = "markdown",
) -> Dict[str, Any]:
    if output.exists():
        raise ValueError("输出文件已存在，不会覆盖: {}".format(output))
    if output_format not in {"json", "markdown"}:
        raise ValueError("复测输出格式必须是 json 或 markdown")

    comparison = compare_delivery_bundles(baseline_path, current_path)
    if output_format == "json":
        content = _json_bytes(comparison)
    else:
        content = render_comparison_markdown(comparison).encode("utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".rabbitmq-guard-comparison-",
            suffix=".tmp",
            dir=str(output.parent),
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        try:
            os.link(str(temporary_path), str(output))
        except FileExistsError as exc:
            raise ValueError("输出文件已存在，不会覆盖: {}".format(output)) from exc
        temporary_path.unlink()
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
    return comparison
