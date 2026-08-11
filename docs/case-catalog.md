# RabbitMQ Guard 案例目录

## 数据性质

当前仓库没有客户生产数据。`data/scenarios` 中的记录全部是合成快照，不是公开事故原文，也不是虚构的客户案例。每个快照只模拟 RabbitMQ 官方文档已经明确描述的故障机制。

这些数据适合：

- 验证规则能否识别预期信号
- 演示诊断报告格式
- 生成更多有标签的测试变体
- 在获得真实数据前固定采集字段和接口契约

这些数据不适合：

- 计算误报率、漏报率或商业效果
- 训练可直接用于生产的机器学习模型
- 证明某个固定阈值适合所有 RabbitMQ 集群

## 基准案例

| ID | 故障机制 | 主要信号 | 预期规则 |
| --- | --- | --- | --- |
| `healthy_baseline` | 健康对照 | 速率平衡、资源充足、成员全在线 | 无 |
| `no_consumers` | 消费者离线 | `messages_ready > 0`、`consumers = 0` | `queue.no_consumers` |
| `growing_backlog` | 消费能力不足 | ready 很高、publish rate 大于 deliver rate | `queue.growing_backlog` |
| `unacked_saturation` | ack 过慢或 prefetch 过大 | 大量 `messages_unacknowledged` | `queue.unacked_saturation` |
| `redelivery_loop` | 立即重入队循环 | redeliver/deliver 比例很高 | `queue.redelivery_loop` |
| `memory_alarm` | 内存水位告警 | `mem_alarm = true`、连接 blocked | `node.memory_alarm` |
| `disk_alarm` | 磁盘水位告警 | `disk_free_alarm = true` | `node.disk_alarm` |
| `fd_pressure` | 文件描述符压力 | `fd_used / fd_total` 超阈值 | `node.fd_pressure` |
| `quorum_degraded` | 少数成员离线 | 仍有多数，但不是全部成员在线 | `queue.quorum_degraded` |
| `quorum_lost` | 多数成员丢失 | 在线成员少于多数 | `queue.quorum_lost` |
| `connection_churn` | 连接抖动/channel 压力 | 连接创建速率、channel/connection 高 | 两条启发式规则 |

## 官方依据

资料核对日期：2026-08-11。页面当时显示 RabbitMQ 文档版本 4.3。

- [Memory and Disk Alarms](https://www.rabbitmq.com/docs/alarms)：资源水位、发布连接阻塞、集群级告警和文件描述符耗尽行为。
- [Consumer Acknowledgements and Publisher Confirms](https://www.rabbitmq.com/docs/confirms)：manual ack、prefetch、自动重入队以及 requeue/redelivery loop。
- [Consumers](https://www.rabbitmq.com/docs/consumers)：consumer capacity 的含义、无消费者时为 0、确认超时。
- [Quorum Queues](https://www.rabbitmq.com/docs/quorum-queues)：多数派要求、成员故障、可用性和数据安全边界。
- [Monitoring](https://www.rabbitmq.com/docs/monitoring)：官方推荐的节点、队列和应用指标，以及 Management API 字段。

## 仍缺少的验证

下一阶段需要真实但可脱敏的数据来验证：

1. 每个规则在不同业务基线下的阈值。
2. 单次快照与连续时间序列之间的差异。
3. 应用端处理耗时、nack 原因和下游依赖指标。
4. 规则结论是否帮助操作者缩短定位时间。
