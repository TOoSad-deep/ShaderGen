# ShaderForge Intent 架构

`intent/` 保存事实测量、用户约束和模型推断之间的稳定 IR 边界。

## V2.0/V2.1 当前边界

- `models.py` 定义 strict/frozen 的 `RequestConstraintSet`、`ConstraintConflict` 和按 `kind` 判别的 sealed payload union；未知 kind、未知字段或 payload/kind 不匹配均 fail closed。`required_layer` 与数据 taxonomy 统一包含 `glow`。
- `canonical.py` 定义 constraint id、集合级 semantic hash、旧 revision CAS 和进入 Intent 前的基础拒绝；旧 CAS 不是可信合并证明，产物进入 Intent 前仍必须由严格策略重建复验。
- `constraints_builder.py` 是 RequestConstraintSet 的规范化入口：重算 constraint id、按内容语义去重证据、冻结来源优先级、把 hard/soft 分层裁决冲突，并用 revision CAS 产生完整新快照。`validate_request_constraint_set_policy()` 会独立重建，拒绝手工 conflict winner、model hard、无证据的 measurement hard 和旧 CAS 绕过。
- identity 投影排除 set/record id、request/source revision、时间戳和存储位置；证据只绑定内容 hash、kind 与 schema。`RegionLock.mask_ref` 同样只绑定 mask 的 SHA-256、kind 和 schema，不把 run-local artifact id 写入 constraint id 或集合 hash。
- `source=measurement` 的 hard constraint 只有在 `verification_status=verified` 时可进入 Intent；`verified` 表示独立确认或显式策略晋升结果，confidence 无论高低都不能自行把 measurement 晋升为 hard。
- `parsing.py` 只接受一个严格 JSON object，拒绝 duplicate key、NaN/Infinity、未知字段和确定性 target 字段；视觉层假设仍使用正式 V2.1 的九层集合，`glow` 不进入 `layer_hypotheses`。`visual_interpretation_v2_1` 另要求 `required_layer_assessments` 按共享十项 taxonomy 顺序逐项给出 `required | not_required | unknown`、confidence、model provenance 和内容寻址 evidence，禁止用省略代替负判断或不确定性。
- `interpretation_artifacts.py` 以 `visual_interpretation_call_audit_v2` / `visual_interpretation_parser_v2_2` 把 Prompt 快照、模型身份、输入 Artifact、原始响应、attempt/repair、Parser 结果和 Interpretation 输出分别内容寻址物化；逐项 required-layer assessment evidence 必须来自调用输入。失败解析不创建成功 Interpretation，重启加载会重新验证全部 bytes/hash、Parser 结果和闭集 evidence。
- `builder.py` 是 Measurements、Interpretation、ConstraintSet 与 Context 的唯一合并入口。Context 冻结 RenderContract、stage 已验证的 primitive/template catalog version+SHA、规范排序 allowlist 和可引用 evidence；evidence 授权与 receipt hash 统一使用内容语义，不依赖 run-local artifact id。required 集按 `assessment.required ∪ hard required constraints ∪ policy base_fill` 合并；assessment `unknown` fail closed，hard required 与 `not_required` 冲突也 fail closed。仅由 assessment 要求的层以 model provenance、confidence 和 evidence 保留到 Intent。`intent_builder_v3` 对每个 hypothesis 生成完整 variant 或结构化 rejection；`InstanceIntent.instance_intent_v2` 继续逐字段继承 ownership mask 几何，`ObjectIntent.radial_segment_evidence_ref` 则显式继承分段环的 raw/ownership typed evidence 接入点，relations 逐条原样继承像素证据。
- `IntentBuildResult.intent_build_result_v3` 保存 builder version、四输入语义 hash 和输入 hypothesis 完整 partition；`IntentIR.intent_v3` 的 id 绑定 radial evidence 内容语义。`validate_intent_ir()`/`validate_intent_build_result()` 从四个冻结输入精确重建并比较，不把可重算的 `intent_id` 当作授权证明。soft preference 保留 typed payload、scope、source、verification 与 evidence；rejected soft 不影响 Intent。

F02/V2.1 仍是当前唯一 active 功能。真实 `fixture/no-model` conformance 已完整执行 development 10 + validation 41，Intent 51/51 合法、validation 结构和六个关键类门禁通过；闭集 Schema 改动后同一 51 例仍通过。该结果只证明确定性测量、约束、Parser fixture 和 Intent 合并契约，不证明真实 VLM 视觉判断质量。runtime required-layer 独立 verifier 已形成严格闭环，但尚未接入生产 Selector；Candidate/provenance 完整恢复和端到端 admission 也仍未完成，因此 F02 暂不标记为 passing。V2 Graph 属于 V2.3；当前 `langgraph.json` 继续只注册 V1。release-held-out 要到 V2.3 release-candidate 冻结后才参与门禁。
