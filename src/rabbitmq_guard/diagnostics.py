import json
from typing import Any, Dict, Iterable, List, Optional

from .models import Finding, SEVERITY_ORDER


ALARMS_SOURCE = "https://www.rabbitmq.com/docs/alarms"
CONFIRMS_SOURCE = "https://www.rabbitmq.com/docs/confirms"
CONSUMERS_SOURCE = "https://www.rabbitmq.com/docs/consumers"
MONITORING_SOURCE = "https://www.rabbitmq.com/docs/monitoring"
QUORUM_SOURCE = "https://www.rabbitmq.com/docs/quorum-queues"


DEFAULT_THRESHOLDS: Dict[str, float] = {
    "backlog_messages": 10_000,
    "backlog_rate_gap": 5.0,
    "consumer_capacity": 0.50,
    "unacked_messages": 1_000,
    "unacked_fraction": 0.70,
    "redelivery_min_rate": 10.0,
    "redelivery_ratio": 0.25,
    "fd_usage": 0.85,
    "channel_per_connection": 50.0,
    "connection_open_rate": 25.0,
}


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _target(queue: Dict[str, Any]) -> str:
    return "queue:vhost={!r},name={!r}".format(
        queue.get("vhost", "/"), queue.get("name", "unknown")
    )


def _finding(
    rule_id: str,
    severity: str,
    title: str,
    target: str,
    evidence: Iterable[str],
    explanation: str,
    actions: Iterable[str],
    sources: Iterable[str],
    confidence: str = "high",
) -> Finding:
    return Finding(
        rule_id=rule_id,
        severity=severity,
        title=title,
        target=target,
        confidence=confidence,
        evidence=list(evidence),
        explanation=explanation,
        actions=list(actions),
        sources=list(sources),
    )


def diagnose(
    snapshot: Dict[str, Any], thresholds: Optional[Dict[str, float]] = None
) -> List[Finding]:
    config = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        config.update(thresholds)

    findings: List[Finding] = []
    nodes = snapshot.get("nodes") or []
    queues = snapshot.get("queues") or []
    connections = snapshot.get("connections") or []
    activity = snapshot.get("activity") or {}

    blocked_connections = sum(
        1 for connection in connections if connection.get("state") in {"blocked", "blocking"}
    )

    for node in nodes:
        node_name = str(node.get("name", "unknown"))
        if node.get("mem_alarm") is True:
            findings.append(
                _finding(
                    "node.memory_alarm",
                    "critical",
                    "节点触发内存告警，发布连接可能被阻塞",
                    "node:{}".format(node_name),
                    [
                        "mem_alarm=true",
                        "mem_used={} bytes".format(node.get("mem_used", "unknown")),
                        "mem_limit={} bytes".format(node.get("mem_limit", "unknown")),
                        "blocked_connections={}".format(blocked_connections),
                    ],
                    "RabbitMQ 达到内存水位后会暂停读取发布连接；集群中一个节点告警会影响整个集群的发布连接。",
                    [
                        "确认增长来自队列消息、连接、插件还是 Erlang 进程，不要先盲目提高水位",
                        "保持消费者运行以排空积压，并检查客户端是否正确处理 connection.blocked",
                        "确认发布端启用 publisher confirms，避免把超时误判为已成功写入",
                    ],
                    [ALARMS_SOURCE, MONITORING_SOURCE],
                )
            )
        if node.get("disk_free_alarm") is True:
            findings.append(
                _finding(
                    "node.disk_alarm",
                    "critical",
                    "节点触发磁盘空间告警，发布连接可能被阻塞",
                    "node:{}".format(node_name),
                    [
                        "disk_free_alarm=true",
                        "disk_free={} bytes".format(node.get("disk_free", "unknown")),
                        "disk_free_limit={} bytes".format(node.get("disk_free_limit", "unknown")),
                        "blocked_connections={}".format(blocked_connections),
                    ],
                    "RabbitMQ 在可用磁盘低于水位时阻塞发布连接，以避免耗尽磁盘。",
                    [
                        "确认 RabbitMQ 数据目录所在挂载点的真实可用空间和磁盘 I/O",
                        "查找异常积压、未确认消息、日志或其他进程占用，不要直接删除队列数据文件",
                        "恢复空间后验证告警解除以及发布确认恢复",
                    ],
                    [ALARMS_SOURCE, MONITORING_SOURCE],
                )
            )

        fd_used = _number(node.get("fd_used"))
        fd_total = _number(node.get("fd_total"))
        if fd_total > 0 and fd_used / fd_total >= config["fd_usage"]:
            findings.append(
                _finding(
                    "node.fd_pressure",
                    "high",
                    "文件描述符接近上限，新的客户端连接可能被拒绝",
                    "node:{}".format(node_name),
                    [
                        "fd_used={}".format(int(fd_used)),
                        "fd_total={}".format(int(fd_total)),
                        "usage={:.1%}".format(fd_used / fd_total),
                    ],
                    "RabbitMQ 接近文件描述符上限时会拒绝新的连接；连接泄漏或过低的系统限制都可能造成该信号。",
                    [
                        "按客户端名称和来源地址检查连接数量与连接创建速率",
                        "确认应用复用长连接和 channel，而不是为每条消息新建连接",
                        "在排除泄漏后再评估提高系统和 RabbitMQ 的描述符限制",
                    ],
                    [ALARMS_SOURCE, MONITORING_SOURCE],
                )
            )

    for queue in queues:
        target = _target(queue)
        ready = _number(queue.get("messages_ready"))
        unacked = _number(queue.get("messages_unacknowledged"))
        total = max(_number(queue.get("messages")), ready + unacked)
        consumers = int(_number(queue.get("consumers")))
        publish_rate = _number(queue.get("publish_rate"))
        deliver_rate = _number(queue.get("deliver_rate"))
        ack_rate = _number(queue.get("ack_rate"))
        redeliver_rate = _number(queue.get("redeliver_rate"))
        capacity = queue.get("consumer_capacity")

        if ready > 0 and consumers == 0:
            findings.append(
                _finding(
                    "queue.no_consumers",
                    "high",
                    "队列有待处理消息但没有消费者",
                    target,
                    [
                        "messages_ready={}".format(int(ready)),
                        "consumers=0",
                        "publish_rate={:.2f}/s".format(publish_rate),
                    ],
                    "没有在线消费者时，consumer capacity 为 0，待处理消息只能继续积累或等待消费者恢复。",
                    [
                        "检查消费进程、部署副本、订阅队列名和 vhost 是否正确",
                        "核对消费者取消、权限、网络与认证错误",
                        "恢复后观察排空速率，避免一次性扩容压垮下游服务",
                    ],
                    [CONSUMERS_SOURCE, MONITORING_SOURCE],
                )
            )

        rate_gap = publish_rate - deliver_rate
        if (
            ready >= config["backlog_messages"]
            and rate_gap >= config["backlog_rate_gap"]
        ):
            findings.append(
                _finding(
                    "queue.growing_backlog",
                    "high",
                    "生产速率持续高于交付速率，队列正在形成积压",
                    target,
                    [
                        "messages_ready={}".format(int(ready)),
                        "publish_rate={:.2f}/s".format(publish_rate),
                        "deliver_rate={:.2f}/s".format(deliver_rate),
                        "rate_gap={:.2f}/s".format(rate_gap),
                    ],
                    "单点快照只能证明当前存在速率缺口；结合连续快照可确认积压是否持续增长。",
                    [
                        "检查消费者处理延迟和下游依赖，再决定扩容消费者还是限制生产速率",
                        "连续采集至少四个监控周期，确认不是短时突发",
                        "为业务定义可接受积压时长，而不是只使用固定消息数阈值",
                    ],
                    [CONSUMERS_SOURCE, MONITORING_SOURCE],
                    confidence="medium",
                )
            )

        if (
            ready > 0
            and consumers > 0
            and isinstance(capacity, (int, float))
            and float(capacity) < config["consumer_capacity"]
        ):
            findings.append(
                _finding(
                    "queue.low_consumer_capacity",
                    "medium",
                    "消费者容量偏低，队列无法持续立即交付消息",
                    target,
                    [
                        "consumer_capacity={:.1%}".format(float(capacity)),
                        "consumers={}".format(consumers),
                        "messages_ready={}".format(int(ready)),
                    ],
                    "consumer capacity 低于 100% 表示增加消费者、缩短处理时间或调整 prefetch 可能提高交付能力；该指标只是线索。",
                    [
                        "结合消费端 P95 处理耗时、下游延迟和 prefetch 判断真正瓶颈",
                        "小步增加消费者副本并观察吞吐，不要仅凭该指标自动扩容",
                    ],
                    [CONSUMERS_SOURCE],
                )
            )

        if (
            unacked >= config["unacked_messages"]
            and total > 0
            and unacked / total >= config["unacked_fraction"]
        ):
            findings.append(
                _finding(
                    "queue.unacked_saturation",
                    "high",
                    "大量消息处于未确认状态，消费者可能卡住或 prefetch 过大",
                    target,
                    [
                        "messages_unacknowledged={}".format(int(unacked)),
                        "messages_total={}".format(int(total)),
                        "unacked_fraction={:.1%}".format(unacked / total),
                        "ack_rate={:.2f}/s".format(ack_rate),
                    ],
                    "未确认窗口由消费确认模式和 prefetch 控制。处理缓慢、下游阻塞或无限 prefetch 都可能推高该指标。",
                    [
                        "检查消费处理耗时、线程池、数据库或外部 API 延迟",
                        "核对 manual ack 的成功路径、异常路径和 channel 使用方式",
                        "评估有界 prefetch；不要直接重启所有消费者，否则未确认消息会集中重入队",
                    ],
                    [CONFIRMS_SOURCE, CONSUMERS_SOURCE],
                )
            )

        if (
            deliver_rate >= config["redelivery_min_rate"]
            and redeliver_rate / max(deliver_rate, 0.0001) >= config["redelivery_ratio"]
        ):
            findings.append(
                _finding(
                    "queue.redelivery_loop",
                    "high",
                    "重复投递比例过高，可能存在立即重入队循环",
                    target,
                    [
                        "deliver_rate={:.2f}/s".format(deliver_rate),
                        "redeliver_rate={:.2f}/s".format(redeliver_rate),
                        "redelivery_ratio={:.1%}".format(redeliver_rate / deliver_rate),
                    ],
                    "当多个消费者因同一临时条件不断 nack/reject 并 requeue 时，消息可能立即再次投递，消耗 CPU 和网络。",
                    [
                        "检查失败原因是否对所有消费者都成立，并定位 poison message",
                        "使用有上限的重试次数和延迟重试，超过上限后路由到 DLQ",
                        "确保消费者幂等，并区分可重试错误与永久错误",
                    ],
                    [CONFIRMS_SOURCE, QUORUM_SOURCE],
                )
            )

        if queue.get("type") == "quorum":
            members = list(queue.get("members") or [])
            online = list(queue.get("online_members") or [])
            if members:
                required = len(members) // 2 + 1
                if len(online) < required:
                    findings.append(
                        _finding(
                            "queue.quorum_lost",
                            "critical",
                            "Quorum queue 在线成员不足多数，队列不可用",
                            target,
                            [
                                "members={}".format(len(members)),
                                "online_members={}".format(len(online)),
                                "required_majority={}".format(required),
                                "leader={}".format(queue.get("leader", "unavailable")),
                            ],
                            "Quorum queue 必须有超过半数的已声明成员在线才能工作；一致性优先于可用性。",
                            [
                                "优先恢复原成员或网络连通性，不要在信息不足时 force delete",
                                "核对节点是否永久丢失以及是否存在可恢复的数据目录",
                                "恢复多数后验证 leader、publisher confirms 和消费者进度",
                            ],
                            [QUORUM_SOURCE],
                        )
                    )
                elif len(online) < len(members):
                    findings.append(
                        _finding(
                            "queue.quorum_degraded",
                            "high",
                            "Quorum queue 仍可用，但已失去部分容错能力",
                            target,
                            [
                                "members={}".format(len(members)),
                                "online_members={}".format(len(online)),
                                "required_majority={}".format(required),
                            ],
                            "当前仍有多数成员在线，但继续丢失成员可能使队列不可用。成员失败本身不会自动触发成员关系修复。",
                            [
                                "恢复离线成员并确认其日志追赶完成",
                                "若节点永久移除，按官方成员管理流程替换成员",
                                "检查维护流程是否始终保留多数成员在线",
                            ],
                            [QUORUM_SOURCE],
                        )
                    )

    cluster = snapshot.get("cluster") or {}
    connections_total = _number(cluster.get("connections"))
    channels_total = _number(cluster.get("channels"))
    if (
        connections_total > 0
        and channels_total / connections_total >= config["channel_per_connection"]
    ):
        findings.append(
            _finding(
                "cluster.channel_pressure",
                "medium",
                "平均每个连接的 channel 数量异常偏高",
                "cluster:{}".format(cluster.get("name", "unknown")),
                [
                    "connections={}".format(int(connections_total)),
                    "channels={}".format(int(channels_total)),
                    "channels_per_connection={:.1f}".format(channels_total / connections_total),
                ],
                "这是可配置的启发式阈值，不等于故障；channel 泄漏会增加节点资源消耗。",
                [
                    "按客户端连接查看 channel 分布，定位少数异常连接",
                    "检查 channel 是否在异常和超时路径正确关闭",
                    "根据实际并发模型调整阈值，避免把合法多路复用误报为泄漏",
                ],
                [MONITORING_SOURCE],
                confidence="medium",
            )
        )

    connection_open_rate = _number(activity.get("connection_open_rate"))
    if connection_open_rate >= config["connection_open_rate"]:
        findings.append(
            _finding(
                "cluster.connection_churn",
                "medium",
                "客户端连接创建速率过高，可能没有正确复用长连接",
                "cluster:{}".format(cluster.get("name", "unknown")),
                ["connection_open_rate={:.2f}/s".format(connection_open_rate)],
                "频繁创建 TCP/AMQP 连接会增加 broker 和操作系统开销；该阈值必须结合业务基线调整。",
                [
                    "按客户端名称、来源地址和部署版本定位连接创建来源",
                    "检查连接池、自动恢复配置和网络抖动",
                    "优先修复连接生命周期，再考虑提高资源限制",
                ],
                [MONITORING_SOURCE],
                confidence="medium",
            )
        )

    return sorted(findings, key=lambda item: (SEVERITY_ORDER[item.severity], item.rule_id, item.target))


def render_text(findings: List[Finding]) -> str:
    if not findings:
        return "未发现达到当前阈值的异常。注意：单次只读快照不能证明系统完全健康。"

    lines = ["发现 {} 个诊断结果：".format(len(findings))]
    for index, finding in enumerate(findings, 1):
        lines.extend(
            [
                "",
                "{}. [{}] {}".format(index, finding.severity.upper(), finding.title),
                "   目标: {} | 置信度: {}".format(finding.target, finding.confidence),
                "   证据: {}".format("; ".join(finding.evidence)),
                "   判断: {}".format(finding.explanation),
                "   建议:",
            ]
        )
        lines.extend("   - {}".format(action) for action in finding.actions)
    return "\n".join(lines)


def render_markdown(
    findings: List[Finding], context: Optional[Dict[str, Any]] = None
) -> str:
    lines = ["# RabbitMQ Guard 诊断报告", ""]
    if context:
        counts = context.get("counts") or {}
        metadata = [
            ("记录", context.get("label", "-")),
            ("集群", context.get("cluster_name", "-")),
            ("时间", context.get("created_at", "-")),
            ("来源", context.get("source_kind", "-")),
            (
                "风险",
                "严重 {} / 高 {} / 中 {} / 低 {}".format(
                    counts.get("critical", 0),
                    counts.get("high", 0),
                    counts.get("medium", 0),
                    counts.get("low", 0),
                ),
            ),
        ]
        for key, value in metadata:
            safe_value = str(value).replace("\n", " ").replace("\r", " ")
            lines.append("- {}：{}".format(key, safe_value))
        lines.extend(["", "> 本报告来自只读快照和透明规则，不构成自动执行生产变更的授权。", ""])

    if not findings:
        lines.append("未发现达到当前阈值的异常。单次快照不能证明系统完全健康。")
        return "\n".join(lines) + "\n"

    lines.append("共发现 {} 个诊断结果。".format(len(findings)))
    for finding in findings:
        lines.extend(
            [
                "",
                "## [{}] {}".format(finding.severity.upper(), finding.title),
                "",
                "- 目标：`{}`".format(finding.target),
                "- 置信度：`{}`".format(finding.confidence),
                "- 证据：{}".format("；".join(finding.evidence)),
                "",
                finding.explanation,
                "",
                "建议：",
                "",
            ]
        )
        lines.extend("- {}".format(action) for action in finding.actions)
        lines.extend(["", "依据：", ""])
        lines.extend("- {}".format(source) for source in finding.sources)
    return "\n".join(lines) + "\n"


def render_json(findings: List[Finding]) -> str:
    return json.dumps(
        {"findings": [finding.to_dict() for finding in findings]},
        ensure_ascii=False,
        indent=2,
    )
