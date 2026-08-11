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
- 上传快照限制为 5MB，但仍可能包含客户标识；操作者负责脱敏和数据保留策略。
