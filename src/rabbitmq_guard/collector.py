import base64
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List
from urllib.request import Request, urlopen


class CollectionError(RuntimeError):
    pass


def _rate(stats: Dict[str, Any], key: str) -> float:
    details = stats.get("{}_details".format(key)) or {}
    value = details.get("rate", 0.0)
    return float(value) if isinstance(value, (int, float)) else 0.0


def normalize_payloads(
    base_url: str,
    overview: Dict[str, Any],
    nodes: List[Dict[str, Any]],
    queues: List[Dict[str, Any]],
    connections: List[Dict[str, Any]],
) -> Dict[str, Any]:
    object_totals = overview.get("object_totals") or {}
    normalized_queues = []
    for queue in queues:
        stats = queue.get("message_stats") or {}
        normalized_queues.append(
            {
                "vhost": queue.get("vhost", "/"),
                "name": queue.get("name", "unknown"),
                "type": queue.get("type", "classic"),
                "state": queue.get("state", "unknown"),
                "durable": bool(queue.get("durable", False)),
                "messages": queue.get("messages", 0),
                "messages_ready": queue.get("messages_ready", 0),
                "messages_unacknowledged": queue.get("messages_unacknowledged", 0),
                "consumers": queue.get("consumers", 0),
                "consumer_capacity": queue.get(
                    "consumer_capacity", queue.get("consumer_utilisation")
                ),
                "publish_rate": _rate(stats, "publish"),
                "deliver_rate": _rate(stats, "deliver_get"),
                "ack_rate": _rate(stats, "ack"),
                "redeliver_rate": _rate(stats, "redeliver"),
                "members": queue.get("members") or [],
                "online_members": queue.get("online") or [],
                "leader": queue.get("leader"),
                "arguments": queue.get("arguments") or {},
            }
        )

    return {
        "schema_version": "1.0",
        "capture": {
            "kind": "live-read-only",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "source": base_url,
        },
        "cluster": {
            "name": overview.get("cluster_name", "unknown"),
            "connections": object_totals.get("connections", len(connections)),
            "channels": object_totals.get("channels", 0),
            "queues": object_totals.get("queues", len(queues)),
            "consumers": object_totals.get("consumers", 0),
        },
        "nodes": [
            {
                "name": node.get("name", "unknown"),
                "running": bool(node.get("running", True)),
                "mem_alarm": bool(node.get("mem_alarm", False)),
                "mem_used": node.get("mem_used"),
                "mem_limit": node.get("mem_limit"),
                "disk_free_alarm": bool(node.get("disk_free_alarm", False)),
                "disk_free": node.get("disk_free"),
                "disk_free_limit": node.get("disk_free_limit"),
                "fd_used": node.get("fd_used"),
                "fd_total": node.get("fd_total"),
            }
            for node in nodes
        ],
        "queues": normalized_queues,
        "connections": [
            {
                "name": connection.get("name", "unknown"),
                "state": connection.get("state", "unknown"),
                "channels": connection.get("channels", 0),
                "user": connection.get("user", "unknown"),
                "client": (connection.get("client_properties") or {}).get(
                    "connection_name",
                    (connection.get("client_properties") or {}).get("product", "unknown"),
                ),
            }
            for connection in connections
        ],
        "activity": {},
    }


class ManagementApiCollector:
    def __init__(self, base_url: str, username: str, password: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        credentials = "{}:{}".format(username, password).encode("utf-8")
        self.authorization = "Basic " + base64.b64encode(credentials).decode("ascii")
        self.timeout = timeout

    @classmethod
    def from_env(
        cls,
        base_url: str,
        username: str,
        password_env: str = "RABBITMQ_PASSWORD",
        timeout: float = 10.0,
    ) -> "ManagementApiCollector":
        password = os.environ.get(password_env)
        if password is None:
            raise CollectionError("环境变量 {} 未设置".format(password_env))
        return cls(base_url, username, password, timeout)

    def _get(self, path: str) -> Any:
        request = Request(
            self.base_url + path,
            headers={"Authorization": self.authorization, "Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise CollectionError("读取 {} 失败: {}".format(path, exc)) from exc

    def collect(self) -> Dict[str, Any]:
        overview = self._get("/api/overview")
        nodes = self._get("/api/nodes")
        queues = self._get("/api/queues?disable_stats=false&enable_queue_totals=true")
        connections = self._get("/api/connections")
        if not isinstance(nodes, list) or not isinstance(queues, list) or not isinstance(connections, list):
            raise CollectionError("Management API 返回了非预期的数据结构")
        return normalize_payloads(self.base_url, overview, nodes, queues, connections)
