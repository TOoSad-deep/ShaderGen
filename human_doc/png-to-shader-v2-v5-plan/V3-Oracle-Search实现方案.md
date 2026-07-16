# V3：Oracle V2 与 Deterministic Search 实现方案

> 状态：已完成正式 Review
> 前置：[V2–V5 实施总纲](./PNG转无贴图Shader-Agent-V2-V5实施总纲.md)、[V2 实现方案](./V2-IntentIR-Genome-Compiler实现方案.md)
> 对应功能：F04 Oracle V2；拆分后的 Deterministic Search 功能

## 1. 目标与非目标

V3 在固定 Genome 拓扑下自动优化参数，并建立完整 AI-off 路径：

```text
MeasurementsV2 → Rule Intent → Template Seeds
→ Compiler → Oracle V2 → Search → Objective Best
```

必须交付：

- Diagnostic RenderBundle 和 cheap renderer；
- EvaluationProfile/EvidenceKey/SelectionKey；
- ScoreBreakdownV2、ConstraintEvaluationRecord；
- SearchParameterManifest、flatten/unflatten；
- CandidateEvaluator、SearchJournalStore、Coordinate Descent；
- Render/Metric Cache；
- Search Graph、AI-off benchmark 和 objective-best 单调门禁。

不做：

- 结构 Patch；
- MAP-Elites；
- VLM 控制参数搜索；
- 每次参数扰动作为 LangGraph Node；
- 用 cheap score 直接替换真实 WebGL objective best。

## 2. 推荐目录

```text
src/shaderforge/
├── rendering/
│   ├── cheap_renderer.py
│   ├── diagnostic_programs.py
│   └── render_bundle.py
├── evaluation/
│   ├── models_v2.py
│   ├── profiles.py
│   ├── masks.py
│   ├── color_lab.py
│   ├── geometry.py
│   ├── topology.py
│   ├── edges.py
│   ├── regions.py
│   ├── coverage.py
│   ├── complexity.py
│   └── residuals.py
├── search/
│   ├── models.py
│   ├── vectorize.py
│   ├── evaluator.py
│   ├── journal.py
│   ├── engine.py
│   ├── coordinate_descent.py
│   ├── cmaes.py
│   ├── archive.py
│   ├── stopping.py
│   └── ARCHITECTURE.md
└── store/
    ├── evaluation_cache.py
    └── search_journal_store.py
```

## 3. EvaluationProfile 与 EvidenceKey

```python
class EvaluationProfileV2:
    profile_id: str
    profile_version: str
    color_space: str
    normalization_rules: dict[str, str]
    metric_weights: dict[str, float]
    roi_weight_floor: float
    topology_thresholds: dict[str, float]
    protected_region_policy: dict[str, float]
    problem_domain_mapping: dict[str, tuple[str, ...]]
    aggregate_formula_version: str

class EvaluationEvidenceKey:
    contract_id: str
    render_size: tuple[int, int]
    fidelity: str
    renderer_version: str
    renderer_environment_id: str
    metric_version: str
    evaluation_profile_hash: str
    evaluation_scope: Literal["hypothesis_bound", "hypothesis_neutral"]
    target_reference_hash: str
    target_hypothesis_hash: str | None

class SelectionKeyV1:
    schema_version: Literal["selection_key_v1"]
    target_sha256: str
    evaluation_evidence_key_hash: str
    evaluation_profile_hash: str
    constraint_set_hash: str
    evaluation_revision: int
    selector_policy_version: str
```

只有 EvidenceKey 完全一致的 Score 才能普通比较。`hypothesis_bound` 证据必须携带 target hypothesis hash；`hypothesis_neutral` 证据必须令其为 `None`，并由 target PNG、neutral mask/ROI policy 和 profile 共同形成 `target_reference_hash`。

只有 `SelectionKeyV1` 完全一致的 Candidate 才能竞争同一个 `objective_best_id`。Constraint、ROI、权重、Profile、Renderer 环境、目标假设或 Selector policy 任一变化，都必须产生新的 SelectionKey；Constraint、ROI、权重或 Profile 变化时同时递增 `evaluation_revision`。旧 SelectionKey 下的 objective best 保留为历史证据，不能与新 SelectionKey 的候选继续比较。

Candidate 可以有多个 EvaluationRecord：cheap、WebGL preview、WebGL target-size、diagnostic passes。数据库和 Artifact 不得将它们压成一个模糊分数。

## 4. Score 与约束分离

```python
class ScoreBreakdownV2:
    metric_version: str
    target_sha256: str
    candidate_render_sha256: str
    evaluation_profile_hash: str
    evaluation_evidence_key: EvaluationEvidenceKey
    total_loss: float
    global_metrics: GlobalMetrics
    geometry: GeometryMetrics
    topology: TopologyMetrics
    color: ColorMetrics
    edge: EdgeMetrics
    coverage: CoverageMetrics
    regions: dict[str, RegionMetrics]
    probes: tuple[ProbeMetric, ...]
    complexity: ComplexityMetrics
    diagnostics: tuple[Diagnostic, ...]

class ConstraintEvaluationRecord:
    constraint_set_hash: str
    evaluation_revision: int
    evaluation_evidence_key: EvaluationEvidenceKey
    hard_constraints_passed: bool
    violations: tuple[ConstraintViolation, ...]
```

Score 不内嵌会随用户反馈变化的 hard constraint 状态。

## 5. 指标

全局与颜色：

- RGB RMSE/MAE；
- 多尺度结构/颜色差异；
- Lab Delta E；
- 区域均值、方差和主色分布。

结构与几何：

- mask IoU；
- bbox、center、area、axes；
- instance/component/hole count；
- fill topology 和 object relations；
- contour distance/Chamfer。

边缘与语义层：

- edge 位置、强度、宽度、方向；
- background、subject、shadow、highlight、rim、outline 独立 ROI；
- target/candidate mask 并集内颜色误差；
- false-positive/false-negative coverage；
- 小高光、小对象和薄 rim 最低权重。

复杂度：

- Node count；
- estimated ops；
- Shader chars；
- numerical risks。

输出 Lab、edge、mask 和 semantic residual Artifact。

### 5.1 Oracle Perturbation Protocol

Oracle 性质测试由版本化 `oracle_perturbation_manifest_v1` 驱动。每个 Case 固定：fixture/target hash、baseline Genome、单一扰动、扰动幅度、目标 metric path、期望方向、最小 effect、数值容差、保护指标和 EvidenceKey。方向只允许 `loss_increase`、`loss_decrease`、`equivalent_within_tolerance`。

测试规则：

- baseline 与 perturbed candidate 使用同一目标尺寸、Renderer 环境和 EvaluationProfile；
- 一次只改变一个已声明变量；未完成单变量隔离的样本不进入方向正确率分母；
- delta 小于冻结 noise tolerance 时只能判为 equivalent，不能记作方向正确；
- instance/hole/required-layer 删除 Case 必须触发对应 hard constraint failure，结构类 Case 要求 100% 通过；
- 颜色、几何、edge、coverage、semantic ROI 分域报告方向正确率；总正确率采用 manifest 中冻结的 macro 聚合，不能由样本量大的单一域主导；
- 缺失 Artifact、metric NaN/Inf、Renderer 失败和未达到 `min_effect` 均计失败，不从分母移除。

manifest、容差和阈值在正式 benchmark 前冻结并保存 hash，运行后不得移动。

## 6. Selector

V3 明确区分局部搜索接受和全局 objective-best 选择，避免把依赖当前参数块的 pairwise 规则误当成全局单调顺序。

BlockAcceptancePolicy：

```text
1. runtime/WebGL/no-texture
2. ConstraintEvaluationRecord hard constraints
3. target problem-domain gain
4. protected-region regression
5. total objective gain
6. complexity growth
```

它只决定当前参数块的 challenger 是否替换 block incumbent。Challenger 替换 incumbent 前，必须在同一真实 WebGL `renderer_environment_id` 和目标尺寸下重渲、重评二者。Cheap score 只做内层排序。

ObjectiveBestOrderPolicy 使用与搜索路径无关的稳定全序：

```text
仅 hard constraints 全部通过的候选可参加
→ quantized total_loss 升序
→ quantized complexity penalty 升序
→ semantic_genome_hash 字典序
```

量化精度和字段顺序进入 `selector_policy_version`。同一 SelectionKey 内，仅当 challenger 的 `ObjectiveBestOrderKey` 严格小于 incumbent 时，才能提交 `objective_best.updated`。事件必须绑定 old/new candidate、SelectionKey hash、两个 order key 和 Evidence refs。相同 SelectionKey 下 objective best 的 order key 因此严格单调不增；revision 或 SelectionKey 改变时开始新的选择序列，而不是覆盖旧序列。

protected-region regression、topology、instance 和 required-layer gate 必须编译进当前 ConstraintSet；超过阈值的候选不能仅因 total loss 较低而参加全局 ObjectiveBestOrder。

V2 起统一使用 `objective_best`；V3 文档、State、事件和测试不再保留旧版最佳指针别名。

拓扑、实例数量和 required layers 不得只是低权重总分；结构失败直接拒绝。

### 6.1 多 TargetHypothesis 选择

参数搜索在每个 V2 hypothesis branch 内独立进行，branch-local SelectionKey 使用 `hypothesis_bound` EvidenceKey，并产生 `branch_objective_best_id`。不同 hypothesis 的结构分数禁止直接比较。

所有 branch winner 再以同一目标 PNG、目标尺寸、Renderer 环境和版本化 `hypothesis_neutral_profile_v1` 重渲、重评；neutral profile 不读取任一 hypothesis 派生的 mask、ROI 或结构阈值，只使用原始 PNG 可共同定义的视觉损失、runtime hard gate 和 complexity。它生成 `hypothesis_neutral` EvidenceKey 与独立 SelectionKey，按同一 ObjectiveBestOrderPolicy 选择 run-level `objective_best_id`。只有 neutral loss 落入冻结 epsilon 时，才可使用 hypothesis confidence 和 semantic hash 依次 tie-break。

分支未完成或 neutral Evidence 缺失的候选不能进入 run-level 选择；新增 hypothesis 或 neutral profile 变化会创建新的 cross-hypothesis SelectionKey，不能续用旧 objective-best 序列。

## 7. Diagnostic Render

```python
class DiagnosticProgramSet:
    beauty_shader: str
    object_mask_shaders: dict[str, str]
    layer_mask_shaders: dict[str, str]
```

多保真策略：

- 高频搜索：96/128 px cheap renderer；
- block winner：目标尺寸 WebGL beauty + 必要诊断 pass；
- final/objective best：最终尺寸完整 WebGL 评分。

WebGL1 无通用 MRT，诊断层采用多个受控 pass。当前 `alpha:false` 契约不使用 PNG alpha MAE；Parity 使用 beauty RGB MAE、诊断 mask RGB MAE 和 mask IoU。

### 7.1 RendererRequestHash

任何 Render/Parity/Cache 证据都绑定正式请求哈希：

```python
class RendererRequestV1:
    schema_version: Literal["renderer_request_v1"]
    canonicalization_version: str
    renderer_kind: Literal["cheap", "webgl"]
    semantic_genome_hash: str
    program_bundle_hash: str
    glsl_sha256: str | None
    contract_id: str
    compiler_version: str
    renderer_version: str
    renderer_environment_id: str
    render_size: tuple[int, int]
    device_pixel_ratio: float
    fidelity: str
    pass_set: tuple[str, ...]
    uniform_bindings_hash: str
    capture_profile_hash: str

renderer_request_hash = sha256(
    canonical_json(RendererRequestV1, canonicalization_version)
)
```

`capture_profile_hash` 必须覆盖颜色空间/transfer、alpha mode、Y flip、抗锯齿、背景、像素读取和输出编码；`uniform_bindings_hash` 覆盖 `u_time=0` 及所有运行时 uniform。字段、浮点和集合排序规则由 `canonicalization_version` 冻结。Render result 必须回绑 request hash、program/render SHA 和实际环境，不接受仅按 candidate id 命中的缓存。

### 7.2 Cheap/WebGL Parity Protocol

Parity 使用版本化 `renderer_parity_manifest_v1`。每个 Case 固定 Genome hash、pass set、匹配分辨率、capture profile、cheap renderer version 和 WebGL RendererRequest。Cheap 与 WebGL 必须在相同尺寸、坐标/Y flip、背景、颜色变换和 mask 编码下比较，不允许把 96 px cheap render 直接与目标尺寸 WebGL render 计算 MAE。

每个冻结 Case 独立满足：

- beauty RGB MAE `≤ 3/255`；
- 每个 required diagnostic mask RGB MAE `≤ 3/255`；
- 每个 required mask IoU `≥ 0.98`；
- topology/component/hole count 不发生差异。

任何 required pass 缺失、NaN/Inf、尺寸不一致或请求哈希不完整均计失败。报告逐 Case 数值、最大值和失败分母，不允许只用数据集均值掩盖局部失败。

## 8. SearchParameterManifest

```python
class SearchParameterManifest:
    schema_version: Literal["search_parameter_manifest_v1"]
    genome_schema_version: str
    base_topology_hash: str
    node_registry_version: str
    contract_id: str
    canonicalization_version: str
    parameter_layout_hash: str
    entries: tuple[ParameterEntry, ...]
    manifest_hash: str
```

每个 Entry：

- stable path；
- dtype/unit/space；
- 原始上下界；
- `[0,1]` 归一化；
- problem block；
- affected regions；
- semantic role；
- step hint；
- frozen/lock source。

```python
flatten(genome, manifest) -> NormalizedVector
unflatten(vector, manifest) -> EffectGenome
```

排序固定为 problem-domain priority + path。Manifest 绑定 Genome schema、base topology、Node registry、RenderContract、canonicalization 和 parameter layout，不绑定随值变化的完整 semantic Genome hash。

`manifest_hash` 覆盖上述绑定字段和全部 Entry。`flatten/unflatten` 必须校验输入 Genome 的 schema、topology、contract、canonicalization 与 Manifest 完全一致；任一不一致返回 typed error，禁止隐式重排、补参数或跨拓扑复用。V3 Search 只修改参数值，任何导致 `topology_hash` 或 `parameter_layout_hash` 改变的结果都直接拒绝。

## 9. Search Engine

```python
async def optimize_block(
    request: SearchRequest,
    evaluator: CandidateEvaluator,
) -> SearchBlockResult:
    ...
```

CandidateEvaluator：

```text
Genome → Hash → Compile/Cache → Render → Raw Metrics
       → Constraint Evaluation → CandidateRecordV2
```

参数块顺序：geometry、background_shadow、base_color_field、rim_edge、highlight、fine_detail、global_balance。

Coordinate Descent：

- 每维尝试正负 step；
- 每轮接受最佳合法候选；
- 无改善时 step 减半；
- step 低于阈值或连续 sweep 停滞时停止；
- tie-break 使用 semantic Genome hash；
- 随机扰动保存固定 seed。

CMA-ES 仅在 Coordinate Descent、Oracle 性质测试和可复现 gate 通过后加入。

## 10. SearchJournal

LangGraph 通常只在 Node 返回后提交 State，因此 Node 内恢复不能依赖 State 自动保存。

```python
class SearchJournalEntry:
    schema_version: Literal["search_journal_entry_v1"]
    journal_key: str
    run_id: str
    seed_id: str
    block: str
    search_policy_version: str
    search_parameter_manifest_hash: str
    selection_key_hash: str
    cursor_revision: int
    normalized_vector_ref: ArtifactRefV2
    incumbent_candidate_id: str
    incumbent_evaluation_id: str
    budget_reserved: int
    evaluations_completed: int
    status: str

class SearchEvaluationRecord:
    schema_version: Literal["search_evaluation_record_v1"]
    evaluation_id: str
    journal_key: str
    cursor_revision: int
    normalized_vector_hash: str
    semantic_genome_hash: str
    renderer_request_hash: str
    selection_key_hash: str
    candidate_ref: ArtifactRefV2
    score_ref: ArtifactRefV2
    constraint_evaluation_ref: ArtifactRefV2
    budget_units: int
    status: str

class SearchJournalStore(Protocol):
    def load_head(self, journal_key: str) -> SearchJournalEntry | None: ...
    def get_evaluation(self, evaluation_id: str) -> SearchEvaluationRecord | None: ...
    def put_evaluation_if_absent(
        self, record: SearchEvaluationRecord
    ) -> SearchEvaluationRecord: ...
    def cas_append(
        self,
        expected_cursor_revision: int,
        next_entry: SearchJournalEntry,
    ) -> SearchJournalEntry: ...
```

`journal_key` 由 run/seed/block/search policy/Manifest/SelectionKey 派生。`evaluation_id` 由 journal key、cursor revision、normalized vector hash 和 RendererRequestHash 确定性派生；相同 evaluation id 只能形成一条已确认 EvaluationRecord。

提交协议：

```text
derive evaluation_id
→ get_evaluation / cache lookup
→ 若不存在，执行 compile/render/evaluate
→ put_evaluation_if_absent
→ CAS append(expected cursor_revision)
→ 更新 incumbent、预算和 evaluations_completed
→ Graph State 只引用已确认的 journal head
```

CAS loser 读取新 head 后重放决策，不重复累计预算或覆盖 incumbent。预算只在 EvaluationRecord 首次确认时计入；缓存命中记录 cache hit，但不伪造新的完成评估。

恢复协议：

1. 从 Graph State 的 `search_journal_cursor_ref` 读取 journal key/revision，再由 Store 加载已确认 head；
2. 校验 search policy、Manifest、SelectionKey、Compiler/Renderer/Metric 版本；不一致时停止当前 block，不猜测迁移；
3. 恢复 normalized vector、incumbent、evaluation count、budget 和停滞状态；
4. 已存在 EvaluationRecord 的 evaluation id 直接复用；只有完全不存在结果时才允许重新评估；
5. 重新评估沿用同一 evaluation id，`put_evaluation_if_absent` 保证只提交、只计费一次；
6. journal head 与 Graph cursor 不一致时以 Store 已确认 head 为事实源，并提交新的小型 State checkpoint；
7. 缺失或损坏的必需 Artifact 终止 block，保留上一个已确认 objective best。

V3 首期可提供本地持久实现，但接口、CAS 和恢复测试必须与 V5 数据库实现保持同一语义。

## 11. Cache

```text
RenderCacheKey
= RendererRequestHash

RawMetricCacheKey
= RendererRequestHash + render_hash
  + target/mask/ROI hypothesis
  + metric implementation + EvaluationProfile

SelectionResult
= 默认不缓存；若缓存必须含 SelectionKey
  + ObjectiveBestOrderPolicy version
```

Constraint、ROI、权重或保护区变化后，旧 SelectionResult 立即失效。

Cache hit 返回的 Artifact 必须重新校验 SHA、request hash、版本和 EvidenceKey。RendererRequestHash 不同，即使 semantic Genome hash 相同也不得复用 Render；RawMetricCacheKey 不同不得复用 Score。

## 12. Graph

```text
V2 hypothesis branches
→ dequeue_hypothesis_branch → select_seed_best → prepare_search
→ optimize_parameter_block
→ materialize_block_winner
→ confirm_with_webgl
→ evaluate_score_and_constraints
→ select_objective_best_v3
→ decide_after_search
   ├─ continue block
   ├─ next block
   ├─ next seed
   └─ finalize_branch_objective_best
→ next_hypothesis/select_cross_hypothesis_objective_best
→ promote_or_skip_memory_v3
```

失败路由：

| 条件 | 动作 |
|---|---|
| Cheap evaluation 失败 | 记录失败分母并继续 |
| WebGL winner 复核失败 | 拒绝 winner，保留 incumbent |
| Renderer transient | 同一 RendererRequest hash 重放一次 |
| EvidenceKey/Profile 不一致 | 重新评估或终止 block |
| 数值越界 | 拒绝并记录 parameter path |
| Budget/time 耗尽 | 保存 Journal/objective best 后 finalize |
| Cancel | 停止新评估并安全结束 |
| Model unavailable | 进入 AI-off，而非失败 Run |

Memory 仅晋升已验证模板、参数先验和失败模式。

## 13. State

V3 State 继承 V2 的 run/project、Measurements/Intent/Seed refs、seed cursor、candidate summaries、`objective_best_id`、Budget 和 StopReason，只增加 Oracle/Search 恢复所需的小型字段：

```python
PngToShaderV3Phase = PngToShaderV2Phase | Literal[
    "oracle_preparing", "search_preparing", "searching",
    "confirming_webgl", "selecting_branch", "selecting_cross_hypothesis",
]

class PngToShaderV3State(PngToShaderV2State):
    state_schema_version: Literal["state_v3"]
    graph_id: Literal["png_to_shader_v3"]
    graph_version: str
    checkpoint_schema_version: str
    checkpoint_namespace: str
    phase: PngToShaderV3Phase
    evaluation_revision: int
    evaluation_profile_ref: ArtifactRefV2
    selection_key_ref: ArtifactRefV2
    cross_hypothesis_selection_key_ref: ArtifactRefV2 | None
    objective_best_order_key_ref: ArtifactRefV2 | None
    renderer_environment_id: str
    search_policy_version: str
    active_seed_id: str | None
    active_parameter_block: str | None
    parameter_manifest_refs: tuple[ArtifactRefV2, ...]
    active_parameter_manifest_ref: ArtifactRefV2 | None
    block_incumbent_candidate_id: str | None
    hypothesis_branch_objective_best_ids: tuple[str | None, ...]
    search_journal_key: str | None
    search_journal_cursor_ref: ArtifactRefV2 | None
    search_cursor_revision: int
    evaluations_completed: int
    render_cache_hits: int
    metric_cache_hits: int
    stagnation_count: int
```

State 不保存完整 vector、Genome、Render、Score 或逐步搜索历史。`checkpoint_namespace` 必须等于 `png-to-shader-v3:{run_id}`；`search_journal_cursor_ref` 只能指向 JournalStore 已确认的 head；branch best 只能绑定 hypothesis-bound SelectionKey，run-level `objective_best_id` 只能指向完成 hypothesis-neutral、真实 WebGL 目标尺寸复核的 branch winner。SelectionKey 改变时先清空对应 best/order-key 指针并重评可行候选，禁止把旧指针带入新序列。恢复时 JournalStore 是 Node 内搜索游标的事实源，Graph Checkpoint 是跨 Node phase 的事实源。

## 14. AI-off

```text
MeasurementsV2
→ Rule Intent
→ Deterministic Template Seeds
→ Compiler
→ Oracle V2
→ Search
→ Genome/GLSL/PNG/Score/Manifest
```

AI-off 和 AI-on 必须复用同一 Compiler、Renderer、Oracle、Search、Selector 和 gate。

### 14.1 AI-on/off 消融协议

V3 的 AI-on/off 只评估 VisualInterpretation/SeedPlan 是否带来收益，不混入 V4 的 GenomePatch、VLM Pairwise、HITL 或 Memory 写入。

Primary fixed-evaluation-budget lane：

- 使用同一版本化 BenchmarkManifest、target、RenderContract、EvaluationProfile、SelectionKey policy 和初始模板集合；
- 两组固定相同 seed 数、每 seed Search evaluation limit、WebGL confirmation limit、wall timeout、Search random seed 和 Cache policy；
- AI-off 使用 Rule Intent/Deterministic Template Seeds；AI-on 仅允许替换 VisualInterpretation/SeedPlan，后续 Compiler/Renderer/Oracle/Search/Selector 完全相同；
- 模型快照、Prompt、generation config 和重复次数预先冻结；AI-off 每 target 运行一次，AI-on 每个冻结 inference seed 运行，按 target 聚合后做 paired comparison；
- 模型 tokens、费用和 latency 单独报告，不偷偷兑换为额外 Search evaluation。另设 fixed-cost lane 时必须使用冻结价格快照，不能与 primary lane 混报。

令 `Loff(t)`、`Lon(t)` 为相同 SelectionKey 语义下两组最终 objective-best loss：

```text
ai_gain(t) = (Loff(t) - Lon(t)) / max(Loff(t), epsilon)
```

Primary endpoint 是 target-level median `ai_gain`，按 target bootstrap 95% CI。AI-off 完整性是 V3 发布阻塞门禁；AI-on 只有在完整输出率和 hard-constraint pass rate 不低于 AI-off、median gain `≥ 0` 且 CI 下界 `≥ -0.02` 时才可默认启用，否则保持 experimental/disabled。所有阈值、epsilon、样本和重复次数在运行前写入 BenchmarkManifest。

## 15. 实施增量

### V3.0：F04 Oracle

- Cheap renderer 和 diagnostic programs；
- EvaluationProfile/EvidenceKey/SelectionKey；
- RendererRequestHash 和 parity manifest；
- 颜色、几何、拓扑、edge、coverage、complexity、residual；
- 版本化单变量扰动性质测试；
- Cheap/WebGL parity。

### V3.1：Search Contracts

- 绑定 topology/schema/contract/canonicalization 的 SearchParameterManifest；
- flatten/unflatten；
- CandidateEvaluator；
- SearchJournalStore/CAS/evaluation id/恢复协议；
- Cache 分层。

### V3.2：Coordinate Descent 与 Graph

- 分块优化；
- Budget/Stop/Cancel；
- Winner WebGL recheck；
- ObjectiveBestOrder 单调选择；
- 继承 V2 的完整 V3 State、Graph/Routing/NodeProvider。

### V3.3：AI-off 与 Benchmark

- Rule Intent/Template Seeds；
- AI-off manifest/gate/report；
- AI-on/off fixed-evaluation-budget 消融；
- objective-best、保护区、质量和成本回归。

## 16. 验收门槛

所有门槛由版本化 `v3_benchmark_manifest_v1` 驱动，至少绑定 dataset/split hash、eligible target ids、Oracle perturbation/parity manifest、EvaluationProfile、SelectionKey/Selector policy、Compiler/Renderer/Metric/Search 版本、硬件环境、seed/budget、epsilon、统计方法和阈值。运行后不得修改 manifest；缺失输出、异常、NaN/Inf 和非预声明排除都计失败。

Oracle：

- `oracle_perturbation_manifest_v1` macro 方向正确率 `≥ 95%`，且每个问题域 `≥ 90%`；
- instance/hole/required-layer 删除 Case 的对应 hard constraint failure 命中率 `100%`；highlight 删除按 manifest 中冻结的 ROI/coverage `min_effect` 判定，不使用主观文字判断；
- 所有保护指标先由 EvaluationProfile 转换为 `[0,1]`、lower-is-better 的 `ProtectedRegionLoss`。定义 `reg_abs=max(0,Lcandidate-Lincumbent)`、`reg_rel=reg_abs/max(Lincumbent,0.01)`；
- 只改背景时 subject、只改高光时 background 的 `reg_rel ≤ 2%`，并同时通过 Profile 中冻结的 absolute-delta gate。

Search：

- flatten/unflatten 100% value/hash round-trip，且 topology/layout/contract 不变；
- normalized value 全部在 `[0,1]`；
- Manifest 与 Genome binding 任一不一致都返回 typed error；
- 固定 seed/budget/version/环境得到相同 objective-best Genome hash 和 ObjectiveBestOrderKey；
- timeout/eval limit/stagnation/cancel 的冻结状态机 Case `100%` 通过；
- SearchJournal 在 evaluation 完成前后、EvaluationRecord 后/CAS 前和 CAS 冲突 Failpoint 下均可恢复；同 evaluation id 只提交一次、预算只累计一次；
- Cache hit 与原始计算的 Artifact SHA、Score 和 Constraint record 一致，错误 RendererRequestHash/EvidenceKey 命中数为 0；
- 同一 SelectionKey 内 objective-best 违反 ObjectiveBestOrder 单调性的运行数为 0；SelectionKey 变化后不得续用旧序列。
- 多 hypothesis Case 中每个 branch 只比较 hypothesis-bound Evidence；run-level objective best 只比较 hypothesis-neutral Evidence，跨 scope/hash 的直接比较数为 0；

Benchmark：

- compile/static/traceability 100%；
- 当前冻结 10 例作为 regression lane，AI-off 10/10 完整输出 Genome/GLSL/PNG/Score/Manifest；release quality 使用 manifest 中独立封存的 release-held-out lane，且只在 release candidate 冻结后运行；
- 对每个 target，将全部合法 V2 seed 在目标尺寸真实 WebGL、同一 SelectionKey 下重新评估，按 ObjectiveBestOrder 选 search 前 baseline。不得沿用 V2 Basic Oracle 的 best 指针；
- 令 `Lseed` 为 baseline total loss，`Lfinal` 为 Search 后 objective-best total loss，`delta_abs=Lseed-Lfinal`，`delta_rel=delta_abs/max(Lseed,epsilon)`；epsilon 在 manifest 中冻结；
- 若 release-held-out lane 有 `N` 个预声明 eligible target，至少 `ceil(0.70*N)` 个同时满足 hard constraints 且 `delta_abs ≥ 0.005`；运行期失败计未改善，不动态缩小分母；
- target-level median `delta_rel ≥ 5%`，并报告按 target bootstrap 95% CI，CI 下界必须 `> 0`；
- 所有 protected-region Case 满足上述相对和绝对退化 gate；
- `renderer_parity_manifest_v1` 的每个 Case 独立满足 beauty RGB MAE `≤3/255`、每个诊断 mask RGB MAE `≤3/255`、每个 mask IoU `≥0.98` 和 topology 一致；
- AI-on/off 消融按 14.1 执行；未通过 non-inferiority gate 时 AI-on 不得默认启用，但不阻塞 AI-off V3 发布；
- 现有 pink-gel 专项门禁继续通过；
- 独立人工门禁由版本化 HumanEvaluationProtocol 绑定 target/candidate 集、盲测 rubric、每 target rater 数、tie policy、primary metric、阈值和 CI；协议 Artifact 通过后才切产品默认。
