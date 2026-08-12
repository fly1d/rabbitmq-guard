# 安全策略

## 支持范围

当前仅维护最新的 `main` 和最新发布版本。RabbitMQ Guard 仍是本地优先的付费试点产品，不应直接部署为公网服务。

## 报告漏洞

请使用 GitHub Private Vulnerability Reporting：

https://github.com/fly1d/rabbitmq-guard/security/advisories/new

不要在公开 Issue 中提交密码、API 凭据、内部地址、客户名称、完整连接列表或消息内容。报告中请包含影响、复现条件、受影响版本和建议缓解措施。

## 当前安全边界

- 实时连接只允许在回环地址启动的工作台中启用。
- RabbitMQ 密码只用于当次 Management API 请求，不写入 SQLite。
- 诊断器只读，不执行 RabbitMQ 配置或修复动作。
- 上传快照限制为 5MB；原始快照及上传文件名仍可能包含客户标识，并会随诊断记录保存在本地 SQLite。
- `sanitize` 和 `collect --sanitize` 使用环境变量中的 HMAC 密钥生成稳定伪名，密钥不写入输出；至少使用 16 字节密钥，推荐使用 32 个随机字节并存入客户自己的密钥管理系统。
- 脱敏输出保留时间、工作负载规模、速率和拓扑数量，且同一密钥生成的伪名可关联，因此属于伪名化数据而不是匿名数据。
- `deliver` 只写入规范化脱敏 ZIP，拒绝覆盖已有文件；`verify-delivery` 校验固定条目、容器格式、哈希和内部诊断一致性，但不是数字签名或来源证明。
- `compare-deliveries` 先完整校验两个交付包，再比较同一伪名集群和采集源；拒绝相同包、不同脱敏映射、采集时间倒置和覆盖已有报告。比较结果仍是伪名化客户数据；伪名一致与采集时间都不是物理集群身份或可信时间证明。

脱敏威胁模型、残余风险和交付检查见 [docs/PRIVACY.md](docs/PRIVACY.md)，交付包格式见 [docs/DELIVERY.md](docs/DELIVERY.md)。
