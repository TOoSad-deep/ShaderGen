# ShaderForge Evaluation 架构

`evaluation/` 对参考图与候选渲染图执行确定性、可解释的基础评分。

## 当前能力

- sRGB `[0, 1]` 全局 RMSE / MAE；
- 灰度 Sobel 边缘损失；
- 基于 TargetMeasurements 的 bbox、中心和面积几何损失；
- 代表像素颜色损失；
- ROI RMSE 与 protection region loss；
- 按前景 mask 置信度衰减 geometry 权重并重新归一化总分；
- 比较两轮 protection loss 的最大退化。
- `CandidateRecord` 强绑定 GLSL、Author/provenance、compile、render、metrics、review 与父候选引用，并把来源收紧为 `model | deterministic`；确定性来源必须由调用方同时保存 generator version；
- `admission.py` 冻结与 benchmark case id 解耦的 `target_structure_facts_v1`、`deterministic_generator_capability_v2` 和 `measurement_seed_admission_v1`。`measurement_affine_seed_v1` 只声明 solid、单实例、零孔和 `base_fill` 能力；V2.3 为 `effect_genome_expander_v2` 增加内容寻址的 `effect_genome_expander_v2_capability_v1` 声明（SHA-256 `8177827bbedd0d346634683a9894fd896780444f5544c7d21760500c6f4cc696`），能力仍严格限制为 solid、单实例、零孔，以及 `shadow/base_fill/color_lobe/haze/rim/outline/highlight/detail/glow`。`background` 不在能力表：当前 Expander 为它生成 `gaussian_color_lobe`，typed evaluator 的 background receipt 只认可 `solid_fill/linear_gradient`；结构事实拒绝 solid/有孔、ring 或 hollow/零孔等自相矛盾组合；admission evidence 绑定 target source、normalized reference、candidate id、GLSL/render hash、generator 与 `offline_replay | runtime_verified` scope；
- `select_current_best()` 只接受硬约束通过、总损失达到最小改善且保护区最大退化不超阈值的候选；缺失既有保护证据直接拒绝。它另提供 keyword-only 的版本化 admission policy/evidence 集成点：显式启用时只对有效、已评分的 deterministic 候选执行 supported/unsupported/unknown fail-closed，model 候选保持原规则；缺省 `None` 保持现有生产调用完全不变。
- V2.0 在 `models_v2.py` 冻结一次写入的 `CandidateRecordV2` 与失败 `CandidateAttemptRecord`；V2.4 的 `candidate_record_v3` 对应 `candidate_record_hash_v3`，旧 hash 版本严格拒绝。`attempt_artifacts.py` 已把失败 Attempt 接入 Graph：strict loader 重验 run、attempt、target hypothesis、semantic genome、证据 ref/bytes/schema 与交叉 identity，错 run/hash/evidence/tamper 一律拒绝。
- 完成态 Candidate provenance 可选绑定 `attempt_id`、稳定 Renderer request receipt 与全部 renderer call evidence；typed Candidate loader 从 Candidate root 恢复这些 refs，校验同一 request hash、唯一 call ordinal、最后一次调用成功及 compilation/GLSL/target/genome identity。首次 transient 后第二次成功的两份 evidence 不得成为无根 orphan。崩溃边界允许 `unknown` renderer evidence，但它只能进入失败 attempt closure 或占用一次 call slot 后由下一 ordinal 成功闭合，不能被当作未调用。
- Production promotion 使用独立 `PromotionOperationV1` outbox 与 `PromotionReceiptV1` commit receipt；operation id 绑定 candidate、GLSL/render/provenance、structure envelope 与 admission policy。Artifact loader 严格校验元数据、SHA、重复 JSON key、run/operation identity；sink 未能证明结果时不得伪造 receipt 或重放。
- V2.1 `runtime_structure.py` 冻结 runtime Target structure evidence/verification Schema，并通过内容寻址 Artifact 读取原始 source、`TargetMeasurementsV2`、规范化参考图、subject/instance 二值 mask 与 required-layer mask。验证器从 source 逐字节重放确定性 normalization，重算 4 邻域 component/hole，复核 Artifact 契约、假设身份、尺寸、实例连通/互斥/覆盖和 base-fill 一致性。
- required-layer 完整性不信任调用方给出的 mask 列表。Evidence 必须同时绑定成功的 VisualInterpretation audit、规范 RequestConstraintSet、IntentBuildContext 和最终 Intent Artifact；Verifier 重放 Prompt/输入/raw/Parser audit、约束合并策略及四输入 Intent 重建。共享 taxonomy 的十项 assessment 必须形成无 `unknown` 的闭集；最终集合是 `assessment.required ∪ hard required constraints ∪ policy base_fill`，hard required 与 assessment `not_required` 冲突即拒绝，且该集合必须与 Intent required roles 和 runtime layer masks 精确相等。全部通过才返回 `structure_verified` 和 `TargetStructureFacts`，任一缺失、篡改或集合不一致都返回 rejected。
- `runtime_structure_artifacts.py` 把 v2 evidence、verification 和恢复 envelope 分别写入 Catalog。恢复入口严格拒绝 duplicate/non-finite JSON、错误 run/ref/kind/schema/content-type、size/SHA 和交叉身份漂移，并用 resolver 重新执行 v2 verifier；重算结论必须与持久结论逐字段相等。该切片不接 Selector，也不改变 production admission。
- `rendered_structure.py` 冻结 breaking 的 `RenderedStructureEvidenceV4` / `RenderedStructureVerificationV4`：V4 除实际 `RendererEnvironmentReceiptV3` 外，还强制绑定 diagnostic v3 与 `stable_instance_ordinal_first_match_v1` ownership policy。beauty 与每个 diagnostic pass 都有独立 request/environment/source/render identity；Verifier 逐 ref 重读 bytes，拒绝 duplicate/non-finite JSON，并把 Intent、Genome、Compilation、diagnostic bundle、policy 与持久 typed bytes 精确闭合。旧 Evidence V3、Verification V3、无 policy 或仅重签 hash 的 payload 均 fail closed。
- `rendered_structure_metric_v3_2` 保持 union IoU `0.90`、byte `8` 和原结构阈值不变；aggregate 与 instance union 都来自同一 subject-visible 域，逐实例 mask 是唯一 ownership partition。relation row 明示 `measurement_basis=owned_visible_partition_v1`，当前仅 `touches/disjoint` 可从 owner masks 证明；`overlap/contains/subtracts` 仍需未来 raw-instance/方向化 subtraction 证据。`project_visible_delta_mask_v3()` 继续输出不可变 packed-bit identity。
- layer visible delta 使用独立 byte 阈值 `8`，不复用二值 SDF 的 `128`；最长边 64 等比低分辨率 pass 至少同时满足 4 pixels 与画布面积 0.1%，单像素噪声保持失败分母。shadow/glow/haze/background 可在主体外；`centered_outline_band_v1` 的可见半圈也允许位于 subject 外，但仍必须满足相同像素/面积下限；其余层必须与 subject 相交。Verification 始终按 `REQUIRED_LAYER_ORDER` 输出全部十项 taxonomy rows：启用层记录真实 delta，未启用层也必须保留 `enabled_in_genome=false/predicted_visible=false` 的全零 negative row，不能通过省略躲避 FP/FN 分母。required subset 不可见仍 fail closed，后续 Gate 可直接从十项闭集计算 TP/FP/FN。
- `IntentConstraintEvaluationV3` 是完成态 Candidate 唯一接受的 hard-constraint closure。它显式绑定 target measurements、Intent、Candidate、Genome、Compilation、`RenderedStructureEvidenceV4` 与 `RenderedStructureVerificationV4` 的 ArtifactRef/SHA 和 receipt record hash；Candidate/Evaluation schema 本次不升版，但 strict loader 只接受 V4 ref，旧 V3 receipt 无法恢复。
- `runtime_admission.py` 是 `runtime_verified` 的唯一正向 API 路径：它从 structure envelope root ref 经 Resolver 恢复并重跑 verifier，再恢复 Candidate/provenance 全证据闭包，交叉绑定 run、target hypothesis、candidate id、GLSL/render/provenance refs 与 bytes hash、origin 和 generator version，最后才生成进程内 sealed Selector capability。调用方手工构造同值 evidence、直接构造 capability、缺失/篡改/版本或身份错配均 fail closed；module-private token 只定义受支持的 API capability 边界，不宣称抵抗同进程恶意 Python 反射。

## 边界

- 参考图与候选图尺寸必须一致，不在评分时静默 resize；
- V1 在 sRGB 空间评分，Lab、CIEDE2000、SSIM 和多尺度指标属于后续增强；
- 总分只是优化输入，调用方仍需保留完整评分向量；
- `ScoreBreakdownV1` 内部用不可变 pair tuple 保存有序映射，但写入 metrics Artifact 或 API 前必须调用 `to_dict()`；不得直接依赖 dataclass `asdict()`，否则 JSON 会把映射编码为 pair-list；
- Oracle 不调用 VLM、不选择问题域；`current_best` 更新由同包独立的纯 Selector 完成，Graph 只消费其决定；
- 当前 V1 Graph 没有可验证的 topology/instance/hole/required-layer runtime evidence，因此不传 admission policy，不得把 V2 development Manifest、case/golden/gate 标签或未验证的模型描述接入生产 Selector。`evidence_sha256` 是调用方提供的内容锚点元数据，只有 CLI/replay 同时复验实际文件 bytes 时才能作为内容证明；只传 evidence、不传 policy 属于调用错误并立即拒绝；
- `offline_replay` policy 只允许 counterfactual 验证旧选择点。裸 `runtime_verified` evidence 继续固定为 unknown；只有 resolver-aware adapter 的 sealed output 才能走 Selector trusted 分支。Typed Candidate 已可通过完整 Compiler/constraint/evaluation 重放生成 sealed input，runtime promotion 仍必须再次执行唯一 admission decision。`effect_genome_expander_v2` 在上述窄 capability 内可得到 `admitted`，未声明 generator、复杂 topology、多实例、有孔、background 或任何 identity mismatch 继续 fail closed；production admission 配置仍默认关闭，V1/Backend/`langgraph.json` 均未启用该路径；
- Oracle 只评分调用方显式提供的 ROI；生产 Graph 可以把严格 `VisualAnalysis` 的语义 ROI 追加到确定性测量 ROI，但不得用同名语义区域覆盖测量事实；
- 指标公式变化必须升级 `metric_version` 并重新校准 benchmark。
- V2.4 Graph 已按 immutable plan 执行五次 beauty 与全部 diagnostic requests，Candidate v3 strict loader/正文重算 projection 和 Selector admission 已加载 Evidence/Verification V3；actual Chromium replay gate 也独立重放完整 plan。产品开关仍关闭，release-held-out 数据与正式 release run 未完成前不得写成 production ready。
