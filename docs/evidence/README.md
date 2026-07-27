# 验收证据注册表

本目录保存可以随仓库审计的脱敏证据索引。它不替代 benchmark 原始 Artifact，也不把被 `.gitignore` 排除的本地 `output/` 视为持久归档。

## 证据等级

- `durable`：注册表引用的关键文件已经进入 Git、Git LFS、Release 或具备不可变保留策略的对象存储，并记录 URI、字节数和 SHA-256。
- `partial`：仓库只持有脱敏摘要或部分公开包，且其余完整报告仍可从已知位置取得。该等级可以支持交接和定位，但不能单独作为发布 gate 的可复验证据。
- `missing`：只存在文档结论，没有可获得的原始文件或摘要；不得据此宣称 gate 通过。

普通 GitHub Actions Artifact 受有限保留期约束，即使 workflow 上传成功也仍按 `partial` 处理；只有迁入不可变 Release 或其他满足长期保留策略的介质后，才能改为 `durable`。

## 维护规则

- `registry.json` 只追加新的 evidence id；冻结 run 的 hash、结果和人工选择不得原地改写。
- 不登记 API key、Authorization、reasoning 原文、私有 Prompt 内容或未脱敏环境变量。
- 每个 Artifact 记录相对路径或不可变 URI、字节数、SHA-256 和可获得性；本地文件存在时，`make docs-check` 会复验大小与 hash。
- 发布 gate 只有在对应 evidence 达到 `durable` 且功能验证命令通过后才能作为 `passing` 证据；`partial` 必须继续显示为缺口。
- 大型 PNG、逐候选 Artifact 和浏览器包优先使用 Git LFS、Release 或不可变对象存储；registry 不复制二进制内容。

## Direct GLSL promotion entry

`PromotionAuthorizationV1` 不能仅凭 policy 中的 `durability_status: durable`
获得生产权限。用于 `canary/direct_default` 的 registry entry 必须使用
`kind: layerplan_glsl_promotion_evidence`，并满足以下附加契约：

```json
{
  "evidence_id": "<与授权 durable_registry_entry_id 完全一致>",
  "kind": "layerplan_glsl_promotion_evidence",
  "suite_run_id": "shadow-suite-<content-id>",
  "durability_status": "durable",
  "gate_status": "passed",
  "summary": {
    "target_stage": "canary",
    "d090_suite_report_sha256": "<sha256>",
    "automatic_gate_outcome": "supported",
    "recursive_verifier_version": "promotion-evidence-verifier-v1",
    "recursive_verification_result": "verified",
    "human_blind_review_manifest_sha256": "<sha256>",
    "human_blind_review_result_sha256": "<sha256>",
    "human_blind_review_b_preference": 0.625,
    "human_gate_outcome": "supported",
    "direct_implementation_identity": "<sha256>"
  },
  "artifacts": [
    {
      "role": "promotion_evidence_bundle",
      "path": "s3://<immutable-object-or-release-uri>",
      "availability": "object_store",
      "size_bytes": 1700000,
      "sha256": "<uploaded artifact sha256>",
      "immutability_status": "immutable"
    }
  ],
  "limitations": []
}
```

entry 的所有 summary 字段、bundle URI/hash 和目标 stage 都必须与授权逐字段完全
一致；implementation identity 还必须匹配 Backend 启动时从当前 direct 代码重算的
身份。`availability` 只接受 `release` 或 `object_store`，且 bundle Artifact 必须
且只能有一个。registry 可登记其他 supporting Artifact，但 role 不得重复且都必须
是不可变 URI。当前仓库尚无此类 durable entry；D096 本地 bundle 不得按此模板
补写或冒充 durable。

2026-07-23 用户明确授权删除整个本地 `output/`，包括正式 M5、旧 Node Lab real-model report、公开盲评 zip 和其他历史运行产物。对应 registry 条目已降为 `missing`，只保留审计摘要、原路径、字节数和 SHA-256；这些信息不能恢复文件、复验 gate 或证明当前版本质量。

合并自 mvp 的 acceptance live A/B 与 maturity budget replay 报告仍存在于来源 worktree 的本地忽略目录，因此登记为 `partial`；当前工作树没有复制这些报告。两者只解释旧 MinScene Feature/typed Patch 候选空间，不是冻结 benchmark 或发布 gate，也不能外推为当前 ShaderGraph 产品结论。持久存储落地前，不得把 registry 本身解释成这些缺口已经关闭。
