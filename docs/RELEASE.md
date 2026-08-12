# 发行与校验

## 用户安装

每个正式 GitHub Release 包含：

- `rabbitmq_guard-<version>-py3-none-any.whl`
- `rabbitmq_guard-<version>.tar.gz`
- `SHA256SUMS`
- GitHub build provenance attestation

下载三个文件后运行：

```bash
shasum -a 256 -c SHA256SUMS
gh attestation verify rabbitmq_guard-0.6.0-py3-none-any.whl \
  --repo fly1d/rabbitmq-guard
python3 -m pip install ./rabbitmq_guard-0.6.0-py3-none-any.whl
rabbitmq-guard --version
rabbitmq-guard demo memory_alarm
```

Windows 或 Linux 可以使用平台提供的 SHA-256 工具核对 `SHA256SUMS`。校验和只能发现下载内容是否变化；GitHub attestation 用于验证产物由本仓库的 Release workflow 构建。二者都不能替代客户自己的软件准入和代码审查。

## 维护者发布

1. 在短分支更新版本和 `CHANGELOG.md`，通过 PR review 与 required CI 合并。
2. 等待合并后的 `main` CI 成功，并确认本地 `main` 与 `origin/main` 一致且工作区干净。
3. 创建并推送与包版本完全一致的签名或带注释标签，例如 `v0.6.0`。
4. Release workflow 只接受指向当前 `main` 且与包版本一致的标签。
5. 等待发行工作流完成，下载产物并再次运行 `SHA256SUMS`、attestation 和安装冒烟。
6. 确认 GitHub Release 是 latest、仓库许可证已识别、试点申请表可打开。

不要手工上传未经 workflow 构建的同名产物，不要复用或移动已发布标签。发现发布错误时应发布新的补丁版本，并在 Release notes 中说明影响与迁移方式。
