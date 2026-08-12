# 客户诊断交付包

RabbitMQ Guard 交付包用于把付费试点的采集、脱敏、诊断和报告收敛成一个客户本机命令。目标是减少操作错误和原始快照误传，不是建立数字签名或长期归档格式。

## 客户生成

客户应在能够访问 RabbitMQ Management API 的可信环境运行：

```bash
export RABBITMQ_PASSWORD='your-password'
export RABBITMQ_GUARD_REDACTION_KEY="$(openssl rand -hex 32)"

python3 -m rabbitmq_guard deliver \
  --url http://localhost:15672 \
  --username monitoring-user \
  --output rabbitmq-guard-delivery.zip
```

程序先验证脱敏密钥，再发起只读采集。原始标准化快照只在进程内存中存在；磁盘只写临时交付包，完整自校验通过后才以不覆盖方式发布为目标文件。目标路径已存在时命令失败，不会替换旧包。

如果客户已经在可信环境保存了尚未脱敏的标准化快照：

```bash
python3 -m rabbitmq_guard deliver \
  --snapshot snapshot.json \
  --output rabbitmq-guard-delivery.zip
```

不要把已经脱敏的快照再次传给 `deliver`。二次脱敏会破坏跨次对象对应关系，因此程序会拒绝。

## 固定内容

ZIP 只允许包含四个根目录文件：

- `snapshot.sanitized.json`：字段白名单构造的稳定伪名快照
- `findings.json`：风险汇总和机器可读诊断结果
- `report.md`：可阅读的诊断报告
- `manifest.json`：格式版本、隐私声明、文件长度和 SHA-256

生成器固定条目顺序、时间戳、权限和 ZIP 编码。交付包不允许目录、重复文件、额外文件、ZIP 注释、扩展元数据或尾随数据。

## 接收和校验

客户把 ZIP 交给指定接收方，并通过另一个可信渠道发送命令打印的整个 ZIP SHA-256。接收方先比较 SHA-256，再运行：

```bash
python3 -m rabbitmq_guard verify-delivery rabbitmq-guard-delivery.zip
```

接收方必须使用 manifest 中记录的同一 RabbitMQ Guard 版本校验。诊断规则或报告格式变化后，其他版本会拒绝重建旧包，以免把版本差异误报为内容篡改。

校验器会：

1. 限制压缩包和解压后的总大小。
2. 要求 ZIP 字节符合规范化容器格式。
3. 校验 manifest schema、每个内容文件的长度和 SHA-256。
4. 校验快照字段白名单、伪名格式、数据类型和脱敏声明。
5. 从快照重新运行诊断，并与 `findings.json` 比较。
6. 重新生成 Markdown 报告，并与 `report.md` 逐字节比较。

通过只表示文件符合当前 RabbitMQ Guard 格式且内部一致。攻击者如果可以同时替换 ZIP 和独立渠道中的 SHA-256，仍可伪造整套内容；需要不可抵赖来源时，应在组织现有签名或制品系统中对整个 ZIP 进行签名。

## 失败处理

- 缺少密钥：不会连接 RabbitMQ，也不会创建输出文件。
- Management API 失败：不会发布交付包。
- 脱敏、诊断或自校验失败：临时文件被删除，不发布目标文件。
- 目标文件已存在：拒绝覆盖，使用新的明确文件名重试。
- 接收方校验失败：不要打开或继续分发包内文件，要求客户重新生成并重新核对 SHA-256。

交付包仍保留时间、规模、速率、资源状态、拓扑数量和同一密钥下的跨次关联，属于伪名化数据。处理要求见 [PRIVACY.md](PRIVACY.md)。
