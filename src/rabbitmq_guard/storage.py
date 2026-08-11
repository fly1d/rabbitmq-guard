import json
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Finding


class RunStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnostic_runs (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    label TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    cluster_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    critical_count INTEGER NOT NULL,
                    high_count INTEGER NOT NULL,
                    medium_count INTEGER NOT NULL,
                    low_count INTEGER NOT NULL,
                    findings_json TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_diagnostic_runs_created_at "
                "ON diagnostic_runs(created_at DESC)"
            )

    @staticmethod
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

    def save(
        self,
        snapshot: Dict[str, Any],
        findings: List[Finding],
        label: str,
        source_kind: str,
    ) -> Dict[str, Any]:
        run_id = secrets.token_hex(8)
        created_at = datetime.now(timezone.utc).isoformat()
        summary = self._summary(findings)
        cluster_name = str((snapshot.get("cluster") or {}).get("name", "unknown"))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO diagnostic_runs (
                    id, created_at, label, source_kind, cluster_name, status,
                    critical_count, high_count, medium_count, low_count,
                    findings_json, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    created_at,
                    label[:120],
                    source_kind[:40],
                    cluster_name[:120],
                    summary["status"],
                    summary["counts"]["critical"],
                    summary["counts"]["high"],
                    summary["counts"]["medium"],
                    summary["counts"]["low"],
                    json.dumps([finding.to_dict() for finding in findings], ensure_ascii=False),
                    json.dumps(snapshot, ensure_ascii=False),
                ),
            )
        return self.get(run_id) or {}

    def list(self, limit: int = 30) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(limit, 100))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, created_at, label, source_kind, cluster_name, status,
                       critical_count, high_count, medium_count, low_count
                FROM diagnostic_runs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [self._row_summary(row) for row in rows]

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM diagnostic_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        result = self._row_summary(row)
        result["findings"] = json.loads(row["findings_json"])
        result["snapshot"] = json.loads(row["snapshot_json"])
        return result

    @staticmethod
    def _row_summary(row: sqlite3.Row) -> Dict[str, Any]:
        counts = {
            "critical": row["critical_count"],
            "high": row["high_count"],
            "medium": row["medium_count"],
            "low": row["low_count"],
        }
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "label": row["label"],
            "source_kind": row["source_kind"],
            "cluster_name": row["cluster_name"],
            "status": row["status"],
            "counts": counts,
            "total": sum(counts.values()),
        }
