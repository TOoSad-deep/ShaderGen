# 验收证据注册表

本目录保存可以随仓库审计的脱敏证据索引。它不替代 benchmark 原始 Artifact，也不把被 `.gitignore` 排除的本地 `output/` 视为持久归档。

## 证据等级

- `durable`：注册表引用的关键文件已经进入 Git、Git LFS、Release 或具备不可变保留策略的对象存储，并记录 URI、字节数和 SHA-256。
- `partial`：仓库只持有脱敏摘要或部分公开包；完整报告仍位于本地忽略目录。该等级可以支持交接和定位，但不能单独作为发布 gate 的可复验证据。
- `missing`：只存在文档结论，没有可获得的原始文件或摘要；不得据此宣称 gate 通过。

普通 GitHub Actions Artifact 受有限保留期约束，即使 workflow 上传成功也仍按 `partial` 处理；只有迁入不可变 Release 或其他满足长期保留策略的介质后，才能改为 `durable`。

## 维护规则

- `registry.json` 只追加新的 evidence id；冻结 run 的 hash、结果和人工选择不得原地改写。
- 不登记 API key、Authorization、reasoning 原文、私有 Prompt 内容或未脱敏环境变量。
- 每个 Artifact 记录相对路径或不可变 URI、字节数、SHA-256 和可获得性；本地文件存在时，`make docs-check` 会复验大小与 hash。
- 发布 gate 只有在对应 evidence 达到 `durable` 且功能验证命令通过后才能作为 `passing` 证据；`partial` 必须继续显示为缺口。
- 大型 PNG、逐候选 Artifact 和浏览器包优先使用 Git LFS、Release 或不可变对象存储；registry 不复制二进制内容。

当前正式 M5 和 Node Lab real-model 证据仍为 `partial`：公开盲评 zip 已受版本控制，但完整 gate report、私有映射和 real-model report 仍只在本地 `output/benchmarks/`。`scene_mvp` acceptance live A/B 同样仅登记本地忽略的诊断报告；它不是冻结 benchmark 或发布 gate，且搜索契约不同于生产。持久存储落地前，不得把 registry 本身解释成这些缺口已经关闭。
