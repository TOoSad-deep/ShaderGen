# V2 严格门禁与实施记录

最后更新：2026-07-21

本文是 V2 数据门禁、实施状态和解除条件的专用记录。版本设计仍以 `human_doc/png-to-shader-v2-v5-plan/` 为准，功能状态以 `docs/FEATURES.md` 为准，长期取舍以 `docs/DECISIONS.md` 为准。

## 冻结原则

- `validation` 是开发可见、可重复调试的数据集；V2.1 Intent 和 V2.2 Genome/Compiler 只读取它的 readiness。
- `release-held-out` 是发布测试集。开发人员不得浏览样本、标签或逐例结果；只有 V2.3 release-candidate 完成代码、配置和阈值冻结后，才由独立保管人执行一次发布评估。
- `V2DatasetReadiness.ready` 继续表示 validation 与 release-held-out 都已就绪的完整审计结果，不能用作所有阶段的统一启动条件。
- 实施代码必须用相同 `gate_stage` 调用 `load_v2_dataset_manifest()` 与 `evaluate_v2_dataset_stage_gate()`；gate 只接受完成文件、图片、taxonomy 及内容寻址来源/许可记录复验的 stage-scoped 加载结果，并绑定 dataset version、Manifest/taxonomy SHA-256。禁止从裸 Manifest 或完整 readiness 自行推断阶段准入。
- development、validation、release-held-out 之间的图片 SHA-256、`visual_family` 和 `hash_group` 都不得交叉。

## 分阶段门禁

| 阶段 | 必需 split | 当前状态 | 解除条件 |
|---|---|---|---|
| V2.1 Intent | validation | ready | 保持六个关键类别各至少 10 个正例，并通过 Manifest、来源、许可、哈希和 taxonomy 校验。 |
| V2.2 Genome/Compiler | validation | ready | V2.1 通过功能门禁后，复用同一 validation readiness；不得读取 release-held-out。 |
| V2.3 release candidate | validation + release-held-out | blocked | 代码、配置和阈值先冻结；独立保管人再提供封存 release package 并运行一次发布评估。 |

当前 validation 有 41 张可审计 CC0 实图，六类分母依次为 `11/20/10/16/26/36`。release-held-out 保持 `not_populated` 和 `0/10`；这只阻塞 V2.3 发布候选，不阻塞 V2.1/V2.2 实施。

## V2.1 实施清单

| 工作项 | 状态 | 当前证据或剩余条件 |
|---|---|---|
| 分阶段数据门禁 | completed | `V2DatasetStageGate` 明确冻结 V2.1/V2.2 仅依赖 validation，V2.3 同时依赖 validation 与 release-held-out。 |
| runtime Target structure evidence Schema | completed | 已冻结 evidence、required-layer mask 和 verification 结果 Schema，并使用 Artifact 内容身份绑定。 |
| runtime structure verifier | completed | v2 verifier 从真实 source 重放 normalization，重算 4 邻域 component/hole、实例互斥/覆盖，并重放 Interpretation audit、ConstraintSet、Context 与 Intent。只有闭集 required-layer receipt、Intent 和 masks 精确一致才返回 `structure_verified` 与 `TargetStructureFacts`。 |
| MeasurementsV2 生产算法 | completed | 从真实 source/alpha 或规范化 RGB 确定性生成 subject/instance masks、多结构假设、区域统计和 evidence index；opaque RGB 高光孔洞与重叠色模态只形成低置信竞争假设，不按 case/标签硬编码。current 10 结构候选 10/10；validation producer、instance exact、full structure 均为 41/41，multi 11/11、hole 30/30、ring 20/20、hollow 10/10。 |
| RequestConstraintSet | completed | 已实现严格归一化、来源优先级、hard/soft 隔离、冲突重建、stable semantic hash、revision CAS 和旧 CAS/手工 winner 绕过防护；model hard 与无独立证据的 measurement hard 均 fail closed。 |
| VisualInterpretation Parser | completed | 已冻结版本化 Prompt、单 JSON object 严格 Parser、模型调用审计封套和 Artifact 恢复复验；模型结果只产生带 provenance 的推断。当前未运行真实 VLM，不宣称视觉判断质量。 |
| Intent Builder/Validator | completed | 唯一合并入口已覆盖完整 TargetHypothesis partition、结构化 rejection、typed soft preference、catalog/context 绑定，并从 Measurements、Interpretation、ConstraintSet、Context 四个冻结输入精确重建验证。 |
| validation Intent/structure 聚合门禁 | completed | `fixture/no-model` runner 完整执行 development 10 + validation 41：Intent 51/51 合法、validation instance exact 41/41，六个关键类 recall/F1 与 macro 均为 1.0；失败保留分母，StageGate 后统一冻结并复验 51 张 source SHA-256，拒绝 release outcome。该结果是 conformance 证据，不是 VLM 质量证据。 |
| 持久化与恢复 | completed | Measurements、Interpretation、Intent、runtime verification、Candidate 与 provenance 均已内容寻址；恢复拒绝重复 JSON key，复验 run/ref/size/SHA、typed Intent/Genome identity 和跨对象谱系。opaque payload 永久不可准入；typed V2.2 路径会重放 Compiler 与约束闭包。 |
| required-layer 完整性 verifier | completed | `visual_interpretation_v2_1` 对共享十项 taxonomy 逐项记录 `required/not_required/unknown`、confidence、model provenance 和 evidence；unknown、hard required 与 not_required 冲突、mask/Intent 缺失或多余均 fail closed。完整性是相对冻结 Interpretation/Constraint 的可审计闭集，不冒充客观视觉真值。 |
| 端到端准入门禁 | completed | resolver-aware adapter 重放 structure envelope 并恢复 Candidate 全闭包，只向 Selector 交付 token-sealed capability；缺失、篡改、跨 run、裸 `runtime_verified` evidence、伪造 capability 和当前 opaque V2.2 语义均 fail closed。默认三参数 Selector 与 model 路径保持不变。 |
| production admission | locally_completed / product_blocked | Graph 2.4 已从 ArtifactRefV2 恢复 Candidate v3 全闭包；`IntentConstraintEvaluationV3` 只在 actual RenderedStructure Evidence/Verification V3 对 aggregate、逐实例、relation 和 required layers 全部 exact 时形成 sealed 准入。无 receipt、旧 Evaluation V2、混合成功/失败 attempt 和篁改仍 fail closed。Backend、V1 与 `langgraph.json` 仍未启用该能力。 |

## V2.2 实施清单

| 工作项 | 状态 | 严格完成条件 |
|---|---|---|
| typed Effect Genome | completed | 16 类 sealed/discriminated node、exact binding contract、显式 SDF→mask AA、Mask algebra、DAG/port/parameter 闭包和四类 hash 已通过正反门禁。 |
| SeedPlan 与三候选展开 | completed | 每个 Intent 固定生成最低复杂度、语义增强、备选结构三个计划；三个 semantic hash 必须唯一且至少两个结构签名不同，否则保留 diversity exception 并失败。 |
| Deterministic Compiler | completed | stable topo、typed AST、safe stdlib、GLSL ES 100、SourceMap、parameter table 与 CompilationBundle 已覆盖全部 16 NodeKind；自产非法 GLSL 直接报 compiler defect。 |
| typed Candidate 下游语义 | completed | typed loader 恢复全部 nested refs 并重编译逐字段比较，重算 required-layer/constraint closure 与 BasicEvaluation identity；Candidate v3 只接受绑定 Measurements/Intent/Genome/Compilation/EvidenceV3/VerificationV3 的 Evaluation V3，opaque 和旧 V2 语义均不可升级。 |
| WebGL1 conformance | completed | 三个 seed 共 15 次真实 Chromium render 全部 compile/link/draw 成功；每 seed 五次 capture 两两 RGB MAE ≤1/255，二次编译的 GLSL/hash/SourceMap/parameter table 一致。 |
| production admission | locally_completed / product_blocked | V2.2 typed Candidate 已在 V2.3 Graph 中接入 sealed admission；这只表示本地技术闭包完成，不表示产品启用或 release gate 通过。 |

V2.2 静态聚合实跑：51/51 Intent、153/153 TypedEffectGenome、51/51 semantic hash 唯一、51/51 结构多样、153/153 双次 deterministic compile 和 static validate 通过。当前 Evaluation/Candidate 语义下 outcomes SHA-256 为 `42cdc8390b5fb32248c9be9fe23f6fb28d30a9a2d57305aeb43c03f4a1317402`；旧 `3cf35d...` 仅作 superseded 审计。该 run 明确 `webgl_requested=false`；真实 WebGL 重复性由独立集成门禁覆盖，不能把两者合并伪报为全量 153 WebGL。

## V2.3 实施清单

| 工作项 | 状态 | 严格完成条件 |
|---|---|---|
| V2 Graph/State/Routing | completed | development-only Builder 现有 22 个 production Node，覆盖恢复入口、context/measurement/interpretation、hypothesis/三 Seed、compile/render/evaluate/materialize、两级选择和 promote/finalize；ASCII、Mermaid、路由表与 AST 门禁一致。State 只保存版本、游标、小字段和 ArtifactRefV2。 |
| Graph 持久化恢复 | completed | 当前冻结为 `state_v4`、`checkpoint_v4`、Graph `2.4` 和 namespace `png-to-shader-v2.4:{run_id}`。每个 RenderPlan item 是一次独立 physical call，以 progress/ref、reservation/evidence/commit 和 self-loop 恢复；五次 beauty capture 不去重，孤立或未确认结果保守结算。旧 State/Checkpoint 明确拒绝。 |
| Renderer/attempt 事实闭包 | completed | 调用前持久化 request 与 ordinal；孤立 reservation 恢复为 `unknown` evidence 并消费对应调用槽位，因此同一 request 包含未知结果在内最多两次真实调用。transient 只剩一次 replay；每次调用均独立计费且 evidence 可从 Candidate root 到达。成功和失败都只落正式 `CandidateAttemptRecord`，公开 loader 重验 run/hypothesis/Genome/evidence/内容身份。 |
| V2 Application Service | completed | source→Measurements→22-node Graph 组合根已实现单机文件 journal、wall ledger、Artifact byte 实际计费、bootstrap/result 崩溃窗口恢复和 terminal 幂等 resume；不依赖进程内缓存。真实 Chromium 集成以 A 红→无效请求→B 绿证明无陈旧帧复用。该 Service 仍 development-only，未接 Backend。 |
| V2 Harness/NodeProvider | locally_completed / real_run_not_authorized | fixture/no-model 复用 production Builder/Node runtime，Node Lab 已迁移 State v4、多 capture 与 Candidate v3 恢复；真实模型仍需显式双开关、完整 token/cost/output 预算和 typed receipt。当前模型调用为 0。 |
| production admission 技术闭包 | completed_for_current_capability | Graph 只从 typed Candidate refs 与 runtime structure envelope 构造 sealed Selector input；promotion 先持久化 stable outbox，再要求 sink 按 operation id 实现可信 `execute/recover`，完成后物化 typed receipt。写入后崩溃通过 recover 收口，未知结果 fail closed 且不重放；这里只承诺 at-most-once execute，不宣称跨系统 exactly-once。当前 capability 仍只覆盖 solid、单实例、零孔和九项已证明 layer，待 RenderedStructureEvidence 扩展。 |
| production admission 产品启用 | blocked | 默认关闭；Backend、V1 和 `langgraph.json` 未改。必须先冻结 RC、通过独立 release-held-out readiness/评估，再决定显式产品开关与 V1/V2 并存策略。 |
| V2.3 受限能力 conformance | completed / superseded_pending | strict v2 runner 曾完整执行 development 10 + validation 41：51/51 case、162/162 attempt 与正式闭包、20/20 supported branch best、18/18 objective best；33/33 复杂结构 case 和 102/102 attempt 被明确记录为 unsupported。该结果证明受限能力和恢复契约，不再作为“完整 V2.3 validation gate”使用；runtime 副作用修复后必须生成新 run，旧 hash 只保留审计。 |
| RenderedStructureEvidence/Verification V4 | completed | 固定五次 beauty、subject/逐实例/逐启用 layer diagnostic；重测 aggregate 和逐实例 component/hole/topology、relation、required-layer contribution 及 actual renderer environment。当前 metric 为 `rendered_structure_metric_v3_2`，并强制绑定 ownership policy；旧 Evidence V3/metric v3.1 不可混用。centered outline 允许主体外半圈贡献，但 byte 8、至少 4px 与画布 0.1% 阈值未放宽。 |
| State→actual Chromium→formal gate | completed | strict collector 仅从 confirmed State v4 枚举全部 attempt/Candidate，对每个 Candidate 的全 RenderPlan 独立重放，生成不可序列化 capability 和可持久 receipts。失败 case 保留分母；普通 Outcome、伪 ref、环境漂移和像素/mask 不一致均不能进入正式统计。 |
| Deterministic ownership partition | completed / quality_not_rebenchmarked | `stable_instance_ordinal_first_match_v1` 从同一 subject visible delta 按稳定 instance ordinal 唯一分配 overlap pixels；owner masks 互斥且 union 继续与 subject 比较。Diagnostic source/product/bundle/GLSL、RenderPlan/render receipt 均升 V3，Rendered Evidence/Verification 升 V4，metric 升为 `rendered_structure_metric_v3_2`；byte 8、union IoU 0.90、失败分母和 relation fail-closed 均未放宽。 |
| Segmented-ring raw structure evidence | completed / compiler_fallback | `RadialSegmentStructureEvidenceV1` 从 source alpha 重放 raw subject、12/18 个 raw segment、semantic subject、ownership partition、共同 radial frame、内外径、跨 2π 角中心/跨度、raw topology 与全 pair disjoint closure。TargetHypothesis/hash、Measurements/producer/bundle、Intent/Builder 同步升版；runtime verifier、Candidate loader、Service journal/resume 均读取正文重建，不能只信 ref。Compiler 当前仍使用 ownership bbox fallback，能运行/恢复但可能 `no_valid_candidate`，后续由 typed evidence 接入 segment primitive 优化效果。 |
| V2.3 完整 visible validation gate | in_progress / rerun_pending | breaking schema 后尚未重跑 51 例 strict actual-visible。旧 strict-v3 的 development 8/10、validation 11/41 只保留为变更前质量诊断，不得准入当前 V4 receipt。冻结阈值仍要求 development/validation case 与 instance exact 100%，validation 六类 recall/F1 及 macro 至少 0.90；下一阶段先实现 segment primitive 和 seed 质量优化，再生成全新 exclusive run。 |
| release candidate | blocked | 代码、Prompt、模板、配置和阈值冻结后，才接收独立保管的 release-held-out 并一次性评估。 |

变更前可见基线 `v2-3-actual-visible-20260721-strict-v3` 的 config/outcomes/report SHA-256 分别为 `2b6666c209fc9e12895ad69ac0e315240539e4ea41f71e84d69b7fa91cdbddd6`、`a8a5433ce98b34baf314d56dcee075b2b989eb80d14d39f0727f7e48fd01ab92`、`4e58a58661dc823b44925e467cf11abf023e17778d7e84d14aee35a018adda1d`。它完成 51/51 case、2016 次 Graph render API 调用和 669 条 replay receipt，但使用旧 metric v3.1/Evidence V3，只能作为 V4 前历史诊断。当前要素闭包以聚焦 smoke 和单例 actual Chromium 为准；尚无新 51 例质量结论。

## release-held-out 交接清单

独立保管人应在 V2.3 冻结点后提供以下内容；开发侧当前不应代为挑选或查看发布样本：

- 六个关键类别各至少 10 个正例；
- 图片原件、来源 URL、许可文本或许可快照；
- `visual_family`、`hash_group`、topology、instance/hole 和 required layers 标签；
- 每个文件的 SHA-256、冻结数据版本和 Manifest；
- 独立封存目录或对象存储位置，以及只向发布执行者开放的访问方式；
- 与 development/validation 的 SHA-256、visual family 和 hash group 交叉污染检查；
- 一次性 release evaluation 的 run id、配置哈希和聚合结果。逐例发布结果不得回流为开发调参材料。

## 当前验证命令

```bash
uv run python -m compileall -q src/shaderforge src/agent/app/nodes/png_to_shader_v2 src/agent/app/services/png_to_shader_v2
uv run pytest -q tests/unit_tests/test_target_measurements_v2_pipeline.py tests/unit_tests/test_intent_ir.py tests/unit_tests/test_deterministic_compiler_v2.py tests/unit_tests/test_rendered_structure_evidence_v2.py
uv run pytest -q tests/unit_tests/test_candidate_artifact_recovery.py tests/unit_tests/test_v2_typed_candidate_semantics.py tests/unit_tests/test_png_to_shader_v2_graph_runtime.py tests/unit_tests/test_png_to_shader_v2_service.py
uv run pytest -q tests/unit_tests/test_v2_rendered_gate_collector.py tests/unit_tests/test_v2_3_actual_chromium_replay.py tests/unit_tests/test_run_v2_3_rendered_structure_benchmark.py tests/unit_tests/test_png_to_shader_v2_node_lab_provider.py
uv run pytest -q tests/integration_tests/test_png_to_shader_v2_chromium_graph.py::test_v2_development_only_graph_closes_candidates_with_real_chromium
uv run mypy --strict src/shaderforge/analysis src/shaderforge/intent src/shaderforge/compiler src/shaderforge/evaluation src/agent/app/nodes/png_to_shader_v2 src/agent/app/services/png_to_shader_v2
uv run ruff check src/shaderforge/analysis src/shaderforge/intent src/shaderforge/compiler src/shaderforge/evaluation src/agent/app/nodes/png_to_shader_v2 src/agent/app/services/png_to_shader_v2
make docs-check
```

2026-07-21 ownership/radial 增量按用户要求只执行分层聚焦 smoke：38 + 112 + 31 + 60 项 Python 回归、相关 strict mypy/Ruff/compileall、docs-check、LangGraph validate 与一个真实 Chromium production Graph 用例通过。另有 segmented Service invoke/restart 手工 smoke 成功。未运行全量测试、51 例 strict actual-visible、真实模型或 release-held-out；完整当前基线见 `PROGRESS.md`。
