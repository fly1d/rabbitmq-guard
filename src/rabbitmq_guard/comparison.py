from typing import Any, Dict, Iterable, List, Tuple

from .models import SEVERITY_ORDER


RISK_WEIGHTS = {"critical": 8, "high": 4, "medium": 2, "low": 1}
METRICS = (
    ("connections", "连接"),
    ("channels", "Channels"),
    ("queues", "队列"),
    ("consumers", "消费者"),
)


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _cluster_metric(snapshot: Dict[str, Any], key: str) -> float:
    cluster = snapshot.get("cluster") or {}
    if key == "queues" and key not in cluster:
        return float(len(snapshot.get("queues") or []))
    return _number(cluster.get(key))


def _finding_key(finding: Dict[str, Any]) -> Tuple[str, str]:
    return str(finding.get("rule_id", "")), str(finding.get("target", ""))


def _finding_sort_key(finding: Dict[str, Any]) -> Tuple[int, str, str]:
    return (
        SEVERITY_ORDER.get(str(finding.get("severity", "low")), 99),
        str(finding.get("rule_id", "")),
        str(finding.get("target", "")),
    )


def _risk_score(findings: Iterable[Dict[str, Any]]) -> int:
    return sum(RISK_WEIGHTS.get(str(finding.get("severity", "low")), 0) for finding in findings)


def _run_reference(run: Dict[str, Any], risk_score: int) -> Dict[str, Any]:
    return {
        "id": run["id"],
        "created_at": run["created_at"],
        "label": run["label"],
        "source_kind": run["source_kind"],
        "status": run["status"],
        "counts": run["counts"],
        "total": run["total"],
        "risk_score": risk_score,
    }


def compare_runs(baseline: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    if baseline["id"] == current["id"]:
        raise ValueError("基线和复测记录不能相同")
    if baseline["cluster_name"] != current["cluster_name"]:
        raise ValueError("只能比较同一 RabbitMQ 集群的诊断记录")

    baseline_findings = {
        _finding_key(finding): finding for finding in baseline.get("findings") or []
    }
    current_findings = {
        _finding_key(finding): finding for finding in current.get("findings") or []
    }
    baseline_keys = set(baseline_findings)
    current_keys = set(current_findings)

    new_findings = sorted(
        (current_findings[key] for key in current_keys - baseline_keys),
        key=_finding_sort_key,
    )
    resolved_findings = sorted(
        (baseline_findings[key] for key in baseline_keys - current_keys),
        key=_finding_sort_key,
    )
    persisting_findings = sorted(
        (current_findings[key] for key in current_keys & baseline_keys),
        key=_finding_sort_key,
    )

    baseline_score = _risk_score(baseline_findings.values())
    current_score = _risk_score(current_findings.values())
    if current_score < baseline_score:
        outcome = "improved"
    elif current_score > baseline_score:
        outcome = "worsened"
    elif new_findings or resolved_findings:
        outcome = "mixed"
    else:
        outcome = "unchanged"

    baseline_snapshot = baseline.get("snapshot") or {}
    current_snapshot = current.get("snapshot") or {}
    metric_changes: List[Dict[str, Any]] = []
    for key, label in METRICS:
        before = _cluster_metric(baseline_snapshot, key)
        after = _cluster_metric(current_snapshot, key)
        metric_changes.append(
            {
                "key": key,
                "label": label,
                "baseline": int(before) if before.is_integer() else before,
                "current": int(after) if after.is_integer() else after,
                "delta": int(after - before) if (after - before).is_integer() else after - before,
            }
        )

    return {
        "cluster_name": baseline["cluster_name"],
        "outcome": outcome,
        "baseline": _run_reference(baseline, baseline_score),
        "current": _run_reference(current, current_score),
        "summary": {
            "new": len(new_findings),
            "resolved": len(resolved_findings),
            "persisting": len(persisting_findings),
            "risk_score_delta": current_score - baseline_score,
        },
        "metric_changes": metric_changes,
        "findings": {
            "new": new_findings,
            "resolved": resolved_findings,
            "persisting": persisting_findings,
        },
    }


def _render_findings(title: str, findings: List[Dict[str, Any]]) -> List[str]:
    lines = ["## {}".format(title), ""]
    if not findings:
        return lines + ["无。", ""]
    for finding in findings:
        lines.extend(
            [
                "### [{}] {}".format(
                    str(finding.get("severity", "unknown")).upper(),
                    finding.get("title", "未命名风险"),
                ),
                "",
                "- 目标：`{}`".format(finding.get("target", "unknown")),
                "- 规则：`{}`".format(finding.get("rule_id", "unknown")),
                "- 证据：{}".format("；".join(finding.get("evidence") or [])),
                "",
            ]
        )
    return lines


def render_comparison_markdown(comparison: Dict[str, Any]) -> str:
    outcome_names = {
        "improved": "风险下降",
        "worsened": "风险上升",
        "mixed": "风险结构发生变化",
        "unchanged": "风险无变化",
    }
    baseline = comparison["baseline"]
    current = comparison["current"]
    summary = comparison["summary"]
    lines = [
        "# RabbitMQ Guard 整改复测报告",
        "",
        "- 集群：{}".format(comparison["cluster_name"]),
        "- 基线：{}（{}）".format(baseline["label"], baseline["created_at"]),
        "- 复测：{}（{}）".format(current["label"], current["created_at"]),
        "- 结论：{}".format(outcome_names[comparison["outcome"]]),
        "",
    ]
    delivery_bundles = comparison.get("delivery_bundles")
    if delivery_bundles:
        lines.extend(
            [
                "## 已验证输入",
                "",
                "- 基线交付包 SHA-256：`{}`".format(
                    delivery_bundles["baseline"]["sha256"]
                ),
                "- 复测交付包 SHA-256：`{}`".format(
                    delivery_bundles["current"]["sha256"]
                ),
                "- 生成器版本：v{}".format(
                    delivery_bundles["baseline"]["generator_version"]
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## 风险变化",
            "",
            "| 指标 | 基线 | 复测 | 变化 |",
            "| --- | ---: | ---: | ---: |",
            "| 加权风险分 | {} | {} | {:+d} |".format(
                baseline["risk_score"],
                current["risk_score"],
                summary["risk_score_delta"],
            ),
            "| 风险总数 | {} | {} | {:+d} |".format(
                baseline["total"],
                current["total"],
                current["total"] - baseline["total"],
            ),
            "",
            "- 新增风险：{}".format(summary["new"]),
            "- 已解决风险：{}".format(summary["resolved"]),
            "- 持续风险：{}".format(summary["persisting"]),
            "",
            "## 集群指标变化",
            "",
            "| 指标 | 基线 | 复测 | 变化 |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for metric in comparison["metric_changes"]:
        lines.append(
            "| {} | {} | {} | {:+g} |".format(
                metric["label"], metric["baseline"], metric["current"], metric["delta"]
            )
        )
    lines.append("")
    lines.extend(_render_findings("新增风险", comparison["findings"]["new"]))
    lines.extend(_render_findings("已解决风险", comparison["findings"]["resolved"]))
    lines.extend(_render_findings("持续风险", comparison["findings"]["persisting"]))
    lines.extend(
        [
            "---",
            "",
            "加权风险分按严重 8、高 4、中 2、低 1 计算，仅用于比较两次诊断结果，不代表业务损失。",
            "",
            "本报告仅比较两次只读快照达到诊断阈值的差异，不代表系统不存在其他风险。",
        ]
    )
    return "\n".join(lines)
