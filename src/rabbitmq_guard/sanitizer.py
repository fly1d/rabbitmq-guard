import hashlib
import hmac
import math
import os
from datetime import datetime
from typing import Any, Dict, Iterable, List


DEFAULT_KEY_ENV = "RABBITMQ_GUARD_REDACTION_KEY"
MIN_KEY_BYTES = 16
PSEUDONYM_HEX_LENGTH = 16
QUEUE_TYPES = {"classic", "quorum", "stream"}
QUEUE_STATES = {"running", "idle", "down", "crashed", "flow", "syncing"}
CONNECTION_STATES = {
    "running",
    "blocked",
    "blocking",
    "flow",
    "closing",
    "closed",
    "starting",
    "tuning",
    "opening",
}
CAPTURE_KINDS = {"live-read-only", "synthetic-baseline", "synthetic-variant"}


def _mapping(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _items(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _identifiers(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _metric(value: Any) -> Any:
    if value is None:
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def _enum(value: Any, allowed: Iterable[str]) -> str:
    text = str(value or "unknown")
    return text if text in allowed else "unknown"


def _timestamp(value: Any) -> Any:
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _boolean(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


class SnapshotSanitizer:
    def __init__(self, key: str):
        key_bytes = key.encode("utf-8") if isinstance(key, str) else b""
        if len(key_bytes) < MIN_KEY_BYTES:
            raise ValueError("脱敏密钥至少需要 16 个 UTF-8 字节")
        self.key = key_bytes

    def _pseudonym(self, category: str, value: Any) -> str:
        normalized = "unknown" if value is None or value == "" else str(value)
        message = "{}\0{}".format(category, normalized)
        digest = hmac.new(self.key, message.encode("utf-8"), hashlib.sha256).hexdigest()[
            :PSEUDONYM_HEX_LENGTH
        ]
        return "{}-{}".format(category, digest)

    def _node_name(self, value: Any) -> str:
        return self._pseudonym("node", value)

    def sanitize(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(snapshot, dict):
            raise ValueError("快照必须是 JSON object")

        capture = _mapping(snapshot.get("capture"))
        if capture.get("kind") == "sanitized":
            raise ValueError("快照已经脱敏；请使用原始标准化快照")
        cluster = _mapping(snapshot.get("cluster"))
        nodes = _items(snapshot.get("nodes"))
        queues = _items(snapshot.get("queues"))
        connections = _items(snapshot.get("connections"))
        activity = _mapping(snapshot.get("activity"))

        sanitized_nodes = []
        for node in nodes:
            sanitized_nodes.append(
                {
                    "name": self._node_name(node.get("name")),
                    "running": _boolean(node.get("running"), True),
                    "mem_alarm": _boolean(node.get("mem_alarm"), False),
                    "mem_used": _metric(node.get("mem_used")),
                    "mem_limit": _metric(node.get("mem_limit")),
                    "disk_free_alarm": _boolean(
                        node.get("disk_free_alarm"), False
                    ),
                    "disk_free": _metric(node.get("disk_free")),
                    "disk_free_limit": _metric(node.get("disk_free_limit")),
                    "fd_used": _metric(node.get("fd_used")),
                    "fd_total": _metric(node.get("fd_total")),
                }
            )

        sanitized_queues = []
        for queue in queues:
            sanitized_queues.append(
                {
                    "vhost": self._pseudonym("vhost", queue.get("vhost")),
                    "name": self._pseudonym("queue", queue.get("name")),
                    "type": _enum(queue.get("type"), QUEUE_TYPES),
                    "state": _enum(queue.get("state"), QUEUE_STATES),
                    "durable": _boolean(queue.get("durable"), False),
                    "messages": _metric(queue.get("messages")),
                    "messages_ready": _metric(queue.get("messages_ready")),
                    "messages_unacknowledged": _metric(
                        queue.get("messages_unacknowledged")
                    ),
                    "consumers": _metric(queue.get("consumers")),
                    "consumer_capacity": _metric(queue.get("consumer_capacity")),
                    "publish_rate": _metric(queue.get("publish_rate")),
                    "deliver_rate": _metric(queue.get("deliver_rate")),
                    "ack_rate": _metric(queue.get("ack_rate")),
                    "redeliver_rate": _metric(queue.get("redeliver_rate")),
                    "members": [
                        self._node_name(item)
                        for item in _identifiers(queue.get("members"))
                    ],
                    "online_members": [
                        self._node_name(item)
                        for item in _identifiers(queue.get("online_members"))
                    ],
                    "leader": (
                        self._node_name(queue.get("leader"))
                        if queue.get("leader") is not None
                        and queue.get("leader") != ""
                        else None
                    ),
                }
            )

        sanitized_connections = []
        for connection in connections:
            sanitized_connections.append(
                {
                    "name": self._pseudonym("connection", connection.get("name")),
                    "state": _enum(connection.get("state"), CONNECTION_STATES),
                    "channels": _metric(connection.get("channels")),
                    "user": self._pseudonym("user", connection.get("user")),
                    "client": self._pseudonym("client", connection.get("client")),
                }
            )

        return {
            "schema_version": "1.0",
            "capture": {
                "kind": "sanitized",
                "original_kind": _enum(capture.get("kind"), CAPTURE_KINDS),
                "captured_at": _timestamp(capture.get("captured_at")),
                "source": self._pseudonym("source", capture.get("source")),
            },
            "privacy": {
                "method": "hmac-sha256",
                "identifier_length": PSEUDONYM_HEX_LENGTH,
                "stable_across_runs": True,
                "removed_fields": ["scenario", "queues[].arguments", "unknown fields"],
            },
            "cluster": {
                "name": self._pseudonym("cluster", cluster.get("name")),
                "connections": _metric(cluster.get("connections")),
                "channels": _metric(cluster.get("channels")),
                "queues": _metric(cluster.get("queues")),
                "consumers": _metric(cluster.get("consumers")),
            },
            "nodes": sanitized_nodes,
            "queues": sanitized_queues,
            "connections": sanitized_connections,
            "activity": {
                "connection_open_rate": _metric(activity.get("connection_open_rate"))
            },
        }


def redaction_key_from_env(env_name: str = DEFAULT_KEY_ENV) -> str:
    key = os.environ.get(env_name)
    if key is None:
        raise ValueError("环境变量 {} 未设置".format(env_name))
    return key


def sanitize_snapshot(snapshot: Dict[str, Any], key: str) -> Dict[str, Any]:
    return SnapshotSanitizer(key).sanitize(snapshot)
