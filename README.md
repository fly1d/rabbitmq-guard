# RabbitMQ Guard

[![CI](https://github.com/fly1d/rabbitmq-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/fly1d/rabbitmq-guard/actions/workflows/ci.yml)

一个案例驱动、完全只读的 RabbitMQ 诊断 MVP。它把 Management API 数据标准化后，通过透明规则输出“证据、判断、建议和官方依据”。当前不使用 LLM，也不执行任何修复动作。

## 已包含

- 10 类异常案例和 1 个健康基线，全部明确标注为合成数据
- 内存/磁盘告警、FD 压力、积压、低消费容量、unacked、重复投递、quorum 可用性和连接压力规则
- RabbitMQ Management API 只读采集器
- 使用本地密钥生成稳定伪名的快照脱敏器
- 一条命令生成可校验的客户诊断交付包
- 文本、JSON、Markdown 三种报告格式
- 历史诊断基线对比、整改复测结论和 Markdown 复测报告
- 可批量生成带标签 JSONL 变体的数据生成器
- 本地 RabbitMQ Docker 实验环境

案例依据和限制见 [docs/case-catalog.md](docs/case-catalog.md)。

## 直接试用

无需安装第三方 Python 包：

```bash
export PYTHONPATH=src

# 查看案例
python3 -m rabbitmq_guard cases

# 诊断内存告警案例
python3 -m rabbitmq_guard diagnose data/scenarios/05_memory_alarm.json

# 生成 Markdown 报告
python3 -m rabbitmq_guard diagnose \
  data/scenarios/04_redelivery_loop.json \
  --format markdown \
  --output report.md

# 每种基准案例生成 20 个数值扰动变体
python3 -m rabbitmq_guard generate \
  --count-per-case 20 \
  --output synthetic-dataset.jsonl
```

也可以安装为本地命令：

```bash
python3 -m pip install -e .
rabbitmq-guard cases
```

## 启动诊断工作台

```bash
export PYTHONPATH=src
python3 -m rabbitmq_guard serve --enable-live
```

打开 <http://127.0.0.1:8787>。工作台支持演示案例、JSON 快照上传、实时只读连接、历史诊断和 Markdown 报告下载。任意历史记录都可以设为基线；打开同一集群的另一条记录后，工作台会展示新增、已解决和持续风险，并生成整改复测报告。诊断记录保存在 `var/rabbitmq-guard.db`，实时连接密码只用于当次请求，不写入数据库。

为了避免把带有网络访问能力的接口暴露出去，启用实时连接时服务只允许绑定回环地址。当前版本是本地付费试点工具，不应直接作为公网 SaaS 部署。

## 脱敏后再交付快照

客户不希望共享生产标识符时，可以在本机生成稳定伪名快照。密钥只从环境变量读取，不进入命令参数或输出文件；基线和复测使用同一密钥时，集群、节点、vhost、队列和连接等伪名保持一致。

```bash
# 首次生成后存入客户自己的密钥管理系统，后续复测继续使用同一值
export RABBITMQ_GUARD_REDACTION_KEY="$(openssl rand -hex 32)"

# 脱敏已有标准化快照
python3 -m rabbitmq_guard sanitize snapshot.json --output sanitized.json

# 采集时只落盘脱敏结果
python3 -m rabbitmq_guard collect \
  --url http://localhost:15672 \
  --username monitoring-user \
  --sanitize \
  --output sanitized.json
```

脱敏器会删除案例说明、队列参数和未知字段，并伪名化直接标识符；诊断所需的数值、布尔状态、时间戳和拓扑关系仍会保留。它降低误传客户标识的风险，但不是匿名化，工作负载规模、速率、时间和拓扑数量仍可能敏感。完整边界和共享检查见 [docs/PRIVACY.md](docs/PRIVACY.md)。

## 一次生成客户交付包

付费试点不需要手工串联采集、脱敏、诊断和报告。客户可以直接从 RabbitMQ 生成一个固定格式 ZIP；原始快照只在进程内存中存在，不落盘：

```bash
export RABBITMQ_PASSWORD='your-password'
export RABBITMQ_GUARD_REDACTION_KEY="$(openssl rand -hex 32)"

python3 -m rabbitmq_guard deliver \
  --url http://localhost:15672 \
  --username monitoring-user \
  --output rabbitmq-guard-delivery.zip
```

也可以从尚未脱敏的标准化快照生成：

```bash
python3 -m rabbitmq_guard deliver \
  --snapshot snapshot.json \
  --output rabbitmq-guard-delivery.zip
```

接收方校验固定文件清单、SHA-256、脱敏格式、诊断结果和报告一致性：

```bash
python3 -m rabbitmq_guard verify-delivery rabbitmq-guard-delivery.zip
```

整改完成后，客户使用同一脱敏密钥和同一 RabbitMQ Guard 版本生成复测包。顾问可以一条命令校验并比较两次交付，得到新增、已解决和持续风险：

```bash
python3 -m rabbitmq_guard compare-deliveries \
  baseline-delivery.zip \
  followup-delivery.zip \
  --output remediation-review.md
```

需要接入内部流程时可增加 `--format json`。比较器会在写报告前完整校验两个 ZIP，拒绝相同交付包、不同伪名集群、不同脱敏采集源、不同脱敏密钥或采集时间倒置；报告不会记录输入文件名。

交付包只包含脱敏快照、机器可读诊断结果、Markdown 报告和 manifest，不包含密码、密钥或原始快照。命令输出的整个 ZIP SHA-256 应通过另一个可信渠道发送给接收方核对。该哈希不是数字签名，不能单独证明生成者身份。格式、流程和失败处理见 [docs/DELIVERY.md](docs/DELIVERY.md)。

## 连接本地或测试集群

密码只从环境变量读取，不写入快照或命令历史：

```bash
export RABBITMQ_PASSWORD='your-password'

# 只采集，保存标准化快照
python3 -m rabbitmq_guard collect \
  --url http://localhost:15672 \
  --username monitoring-user \
  --output snapshot.json

# 采集并立即诊断
python3 -m rabbitmq_guard live \
  --url http://localhost:15672 \
  --username monitoring-user
```

生产环境建议创建只具备监控权限的专用用户。Management API 适合开发和早期验证；大规模生产采集应迁移到 RabbitMQ 官方推荐的 Prometheus 接口。

## 本地制造一个真实积压

下面只操作 `lab` 目录创建的本地容器：

```bash
docker compose -f lab/docker-compose.yml up -d
python3 lab/create_no_consumer_backlog.py

export RABBITMQ_PASSWORD='guard-local-only'
python3 -m rabbitmq_guard live \
  --url http://localhost:15673 \
  --username guard
```

预期会发现 `guard.demo.no-consumers` 有待处理消息但没有消费者。管理界面地址为 <http://localhost:15673>。实验环境使用 `5673/15673/15693`，避免碰到本机已有 RabbitMQ 的默认端口。

## 测试

```bash
make verify
make smoke
```

项目使用 Pull Request、自动 CI、快速冒烟测试和定期真实 RabbitMQ 集成测试维护。维护规则见 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [docs/MAINTENANCE.md](docs/MAINTENANCE.md)。

## 设计边界

- 规则输出是诊断线索，不是自动修复授权。
- 单次 Management API 快照不能可靠判断趋势；积压规则因此标记为中等置信度。
- 固定阈值只是 MVP 默认值，必须根据队列业务基线校准。
- 合成案例用于开发和演示，不能替代真实环境的误报/漏报评估。
- 稳定脱敏输出可跨时间关联，不能当作匿名数据公开发布。

付费试点的客户画像、交付范围、价格假设和停止条件见 [docs/paid-pilot.md](docs/paid-pilot.md)。
