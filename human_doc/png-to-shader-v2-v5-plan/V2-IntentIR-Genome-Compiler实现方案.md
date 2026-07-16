# V2：Intent IR、Effect Genome 与 Deterministic Compiler 实现方案

> 状态：已完成正式 Review
> 前置：[V2–V5 实施总纲](./PNG转无贴图Shader-Agent-V2-V5实施总纲.md)
> 对应功能：F02 Intent IR；F03 Genome/Compiler/Graph

## 1. 目标与非目标

V2 将主路径从“模型生成完整 GLSL”迁移为：

```text
MeasurementsV2 + RequestConstraintSet + VisualInterpretation
  → Intent IR
  → SeedPlan
  → Deterministic Genome Expander
  → Effect Genome
  → Deterministic Compiler
  → WebGL Render
  → V2 Selector
```

必须交付：

- TargetMeasurementsV2；
- RequestConstraintSet、VisualInterpretationV2、IntentIR；
- EffectGenome v0 与 typed mask/SDF algebra；
- SeedPlan 和模板 Mapper；
- Genome Validator、Canonical Hash、Compiler；
- ArtifactRefV2、ArtifactCatalog/Resolver、CandidateRecordV2、结构约束证据；
- V2 Graph、State、NodeProvider 和 benchmark。

不做：

- 参数搜索和 CMA-ES；
- StructureEvolution 和 MAP-Elites；
- 在线 HITL；
- Raymarch、动画、自由噪声；
- 自由 GLSL 反解析；
- 模型修复 Compiler 生成的 GLSL。

## 2. 复用 V1

| V1 能力 | V2 策略 |
|---|---|
| RenderContract、Budget、StopReason | 直接复用 |
| PNG normalize | 直接复用 |
| `measure_target()` | 保留 V1，新增 V2 测量 |
| WebGL1 Renderer | 复用并增加批量调用 |
| Shader Validator | 复用并增加 Compiler 安全规则 |
| ArtifactStore | 增加 ArtifactRefV2、Local Catalog/Resolver 和 Intent/Genome/Compilation 类型 |
| Basic Oracle | 作为 V2 最小 Seed 排序依据 |
| LLMGateway、bounded node、Parser repair | 用于 VisualInterpretation/SeedPlan |
| Node Lab/Harness | 通过新生产 Provider 接入 |
| measurement affine | 改写为 Genome Template，不反解析 GLSL |

V1 的 compile-repair、visual-refine 和自由 GLSL Author 不进入 V2 主路径。

## 3. 推荐目录

```text
src/shaderforge/
├── intent/
│   ├── models.py
│   ├── builder.py
│   ├── validation.py
│   └── ARCHITECTURE.md
├── genome/
│   ├── models.py
│   ├── nodes.py
│   ├── templates.py
│   ├── canonical.py
│   ├── validation.py
│   └── ARCHITECTURE.md
├── compiler/
│   ├── models.py
│   ├── ast.py
│   ├── emitter.py
│   ├── stdlib.py
│   ├── complexity.py
│   └── ARCHITECTURE.md
├── analysis/
│   ├── segmentation.py
│   ├── geometry.py
│   ├── palette_lab.py
│   └── regions_v2.py
└── store/
    ├── artifacts_v2.py
    ├── artifact_catalog.py
    └── legacy_artifact_adapter.py

src/agent/app/
├── graphs/png_to_shader_v2_graph.py
├── graphs/png_to_shader_v2_routing.py
├── states/png_to_shader_v2_state.py
├── nodes/png_to_shader_v2/
├── prompts/analyze_visual_layers_v2.yaml
├── prompts/propose_seed_plans_v1.yaml
└── services/png_to_shader_v2.py
```

新增包同步更新 package discovery、lazy exports、`shaderforge.public`、import-boundary tests 和 wheel smoke。

## 4. TargetMeasurementsV2

```python
class TargetHypothesis:
    schema_version: Literal["target_hypothesis_v1"]
    hypothesis_id: str
    hypothesis_hash: str
    subject_mask_ref: ArtifactRefV2
    instance_mask_refs: tuple[ArtifactRefV2, ...]
    confidence: float
    bbox_uv: BBoxUv
    center_uv: tuple[float, float]
    area_ratio: float
    axes_uv: tuple[float, float]
    orientation_rad: float
    fill_topology: str
    component_count: int
    instance_count: int
    hole_count: int
    relations: tuple[MeasuredRelation, ...]
    evidence_refs: tuple[ArtifactRefV2, ...]

class TargetMeasurementsV2:
    schema_version: Literal["target_measurements_v2"]
    target_sha256: str
    image_size: tuple[int, int]
    target_hypotheses: tuple[TargetHypothesis, ...]
    palette_lab: tuple[LabSample, ...]
    region_statistics: tuple[RegionStatistics, ...]
    symmetry: SymmetryEvidence
    radiality: float
    gradient_evidence: tuple[GradientEvidence, ...]
    edge_refs: tuple[ArtifactRefV2, ...]
    evidence_index_ref: ArtifactRefV2
```

正交表达结构：

- `fill_topology: solid | hollow | ring | open`；
- `component_count`；
- `instance_count`；
- `hole_count`；
- `relations: overlap | contains | subtracts | touches | disjoint`。

`hypothesis_hash` 由目标 hash、mask/instance mask hash、量化 confidence、结构字段和 relation 的 canonical projection 计算，不包含 record id、时间戳或 Artifact URI。confidence 会影响跨分支 tie-break，因此变化时必须生成新 hypothesis hash。

低置信测量不能直接成为 hard constraint；保存多个假设并进入不同 Intent/Seed 分支。每个分支独立完成结构可行性判断，跨分支选择只使用相同原始 PNG、相同 RenderContract 和相同 BasicEvaluation Profile 的公共证据；hypothesis confidence 只在 objective/complexity 落入冻结 epsilon 时作为 tie-break。禁止直接比较绑定不同 hypothesis 的结构分数。

## 5. RequestConstraintSet

来源：

- RenderContract；
- 用户自然语言约束；
- 用户 mask、region/color lock；
- 质量、时间和复杂度预算；
- 已确认 Project Memory；
- 部署策略。

合并优先级：

```text
RenderContract
> 用户显式 hard constraint
> 用户 region/color lock
> 已确认 Project Memory
> 高置信确定性事实
> VLM inference
> 模板默认值
```

```python
ConstraintKind = Literal[
    "contract", "topology", "instance_count", "hole_count",
    "required_layer", "region_lock", "color_lock", "complexity", "budget",
]
ConstraintSource = Literal[
    "render_contract", "user", "project_memory", "measurement", "model", "deployment",
]

class Constraint:
    constraint_id: str
    kind: ConstraintKind
    strength: Literal["hard", "soft"]
    scope: Literal["global", "object", "region", "parameter"]
    scope_ref: str | None
    value: JsonValue
    source: ConstraintSource
    source_revision: int
    confidence: float
    verification_status: Literal["verified", "inferred", "unverified", "rejected"]
    evidence_refs: tuple[ArtifactRefV2, ...]

class ConstraintConflict:
    conflict_id: str
    constraint_ids: tuple[str, ...]
    status: Literal["resolved", "unresolved"]
    selected_constraint_id: str | None
    resolution_policy: str
    reason: str

class RequestConstraintSet:
    schema_version: Literal["request_constraint_set_v1"]
    constraint_set_id: str
    constraint_set_hash: str
    target_sha256: str
    request_revision: int
    constraints: tuple[Constraint, ...]
    conflicts: tuple[ConstraintConflict, ...]
    evidence_refs: tuple[ArtifactRefV2, ...]
```

`constraint_id` 由 kind、strength、scope、source 和 canonical value 确定性生成。`constraint_set_hash` 使用目标 hash、按 constraint id 排序的完整约束语义投影和已解决 conflict 结果计算；排除 set id、request/source revision、时间戳和存储位置。revision 只用于乐观并发与溯源，语义未变时不得导致缓存失效。存在 unresolved conflict 时不得生成 hard constraint Intent；只能返回结构化错误，或由冻结策略显式降级为 soft preference，并记录该决策。

`value` 在存储层可用 JsonValue 表达，但解析层必须按 `kind` 进入 sealed payload union；例如 count、topology、region/color lock、complexity 和 budget 各有独立 schema、单位与范围。未知 kind 或未校验 payload 不得进入 Intent、Hash 或 ConstraintEvaluator。

## 6. VisualInterpretationV2 与 IntentIR

VisualInterpretation 只保存推断：

```python
class VisualInterpretationV2:
    schema_version: Literal["visual_interpretation_v2"]
    summary: str
    layer_hypotheses: tuple[LayerHypothesis, ...]
    primitive_candidates: tuple[PrimitiveCandidate, ...]
    strategy_hypotheses: tuple[StrategyHypothesis, ...]
    uncertainties: tuple[Uncertainty, ...]
    evidence_refs: tuple[ArtifactRefV2, ...]
```

模型不得写入 target hash、图片尺寸和确定性 bbox。

```python
class IntentIR:
    schema_version: Literal["intent_v2"]
    intent_id: str
    target_sha256: str
    target_hypothesis_id: str
    target_hypothesis_hash: str
    constraint_set_hash: str
    canvas: CanvasIntent
    objects: tuple[ObjectIntent, ...]
    layers: tuple[VisualLayerIntent, ...]
    relations: tuple[RelationIntent, ...]
    regions: tuple[RegionIntent, ...]
    probes: tuple[PixelProbeIntent, ...]
    hard_constraints: tuple[Constraint, ...]
    soft_preferences: tuple[Preference, ...]
    strategy_hypotheses: tuple[StrategyHypothesis, ...]
    uncertainties: tuple[Uncertainty, ...]
    evidence_refs: tuple[ArtifactRefV2, ...]
```

`build_intent_variants()` 是 Measurements、VisualInterpretation、RequestConstraintSet 和 Context 的唯一合并入口；每个 `TargetHypothesis` 至少产生一个绑定同一 hypothesis id/hash 的 Intent variant。后续 Seed、Candidate 和证据不得丢失该绑定。

视觉层：background、shadow、base_fill、color_lobe、haze、rim、outline、highlight、detail。

## 7. Effect Genome v0

```python
class EffectGenome:
    schema_version: Literal["genome_v0"]
    hash_version: Literal["genome_hash_v1"]
    genome_id: str
    contract_id: str
    strategy: str
    nodes: tuple[EffectNode, ...]
    edges: tuple[EffectEdge, ...]
    parameters: tuple[ParameterSpec, ...]
    output_node_id: str
    provenance: GenomeProvenance
```

节点只引用 parameter id/path，参数值唯一保存在 `parameters`。

首期节点：

- Geometry：CircleSDF、EllipseSDF、RoundedRectSDF；
- Fill：SolidFill、LinearGradient、GaussianColorLobe；
- Light：Shadow、Glow、RimBand、OutlineBand、ArcHighlight；
- Mask algebra：UnionMask、IntersectionMask、DifferenceMask；
- Composition：OverBlend；
- Output：ColorOutput。

节点总数控制在约 15 类。Mask/SDF 端口类型、combine 语义和抗锯齿规则必须显式定义。

## 8. Parameter 与 Hash

```python
class ParameterSpec:
    path: str
    dtype: str
    value: JsonValue
    min_value: JsonValue | None
    max_value: JsonValue | None
    optimizable: bool
    block: str
    affected_regions: tuple[str, ...]
    semantic_role: str
    unit: str
    coordinate_space: str | None
    color_space: str | None
    cyclic: bool
    quantization: float | None
```

四类 Hash 使用 `genome_hash_v1` canonicalization：UTF-8/NFC、对象 key 排序、集合按稳定业务键排序、有限浮点转为小写 IEEE-754 binary64 hex 字符串、`-0` 归一为 `0`，拒绝 NaN/Inf。所有投影先写入 golden fixture 和 property test，再冻结实现。

- `topology_hash`：genome schema、typed node kind/version/ports、由稳定拓扑序、semantic role 和同类 sibling ordinal 派生的 canonical node id、edge、parameter binding 和 output node；不含参数值；
- `parameter_layout_hash`：所有 ParameterSpec 字段但排除 `value`，包括 path/type/range、optimizable、block、affected regions、semantic role、unit/space、cyclic 和 quantization；
- `semantic_genome_hash`：hash version、contract id、topology hash、layout hash 和按 path 排序的 canonical parameter values；
- `record_hash`：完整持久记录，包括 genome id 和 provenance，但排除存储 URI。

任何影响编译或搜索语义的字段变化都必须改变 topology/layout/semantic 三者中至少一个。时间戳和 provenance 不进入 semantic hash。

## 9. SeedPlan 与 Mapper

```python
class SeedPlanV1:
    schema_version: Literal["seed_plan_v1"]
    intent_id: str
    target_hypothesis_id: str
    target_hypothesis_hash: str
    template_id: str
    template_version: str
    layer_bindings: tuple[LayerBinding, ...]
    parameter_overrides: tuple[AllowedOverride, ...]
    source: Literal["rule", "model", "memory"]
    random_seed: int
    evidence_refs: tuple[ArtifactRefV2, ...]
```

流程：

```text
Intent → Template Matcher → Initial Estimates
       → 3 SeedPlans → Deterministic Expander → Genome Validator
```

三个默认 Seed：最低复杂度、语义层增强、备选结构解释。三个展开结果必须具有不同 `semantic_genome_hash`；只改变 seed id、provenance 或随机数不算 distinct。至少两个 Seed 应在 template、topology 或 enabled layer set 上不同，确实无法产生结构差异时必须记录 `diversity_exception` 并使发布 gate 失败，而不是静默复制。

## 10. Compiler

流程：

```text
Validate → Canonicalize → Stable Topological Sort
→ Typed AST → Safe Stdlib → Source Map
→ Shader Validator → WebGL Compile/Link/Draw
```

```python
class CompilationBundle:
    semantic_genome_hash: str
    compiler_version: str
    glsl_ref: ArtifactRefV2
    glsl_sha256: str
    node_line_map_ref: ArtifactRefV2
    compiler_parameter_table_ref: ArtifactRefV2
    estimated_ops: int
    numerical_risks: tuple[str, ...]
    diagnostics: tuple[str, ...]
```

CompilerParameterTable 只描述源码绑定；搜索归一化留给 V3。

Compiler 输出非法 GLSL 是 `compiler_defect`，禁止交给模型改源码。

## 11. Artifact 与 Candidate

```python
class ArtifactRefV2:
    artifact_id: str
    sha256: str
    kind: str
    schema_version: str
    content_type: str
    size_bytes: int

class ArtifactResolver(Protocol):
    def resolve(self, artifact_id: str) -> ArtifactRefV2: ...
    def read_bytes(self, artifact_id: str) -> bytes: ...

class ArtifactCatalog(ArtifactResolver, Protocol):
    def put(self, *, run_id: str, kind: str, schema_version: str,
            content_type: str, data: bytes) -> ArtifactRefV2: ...

class CandidateRecordV2:
    schema_version: Literal["candidate_record_v2"]
    candidate_id: str
    run_id: str
    parent_candidate_id: str | None
    target_hypothesis_id: str
    target_hypothesis_hash: str
    constraint_set_hash: str
    intent_ref: ArtifactRefV2
    genome_ref: ArtifactRefV2
    topology_hash: str
    parameter_layout_hash: str
    semantic_genome_hash: str
    compilation_ref: ArtifactRefV2
    glsl_ref: ArtifactRefV2
    render_refs: tuple[ArtifactRefV2, ...]
    constraint_evaluation_ref: ArtifactRefV2
    evaluation_refs: tuple[ArtifactRefV2, ...]
    provenance_ref: ArtifactRefV2
    record_hash: str

class CandidateAttemptRecord:
    schema_version: Literal["candidate_attempt_v1"]
    attempt_id: str
    run_id: str
    target_hypothesis_hash: str
    semantic_genome_hash: str
    status: Literal["rejected", "compile_failed", "render_failed", "evaluation_failed"]
    error_code: str
    evidence_refs: tuple[ArtifactRefV2, ...]
```

`ArtifactRefV2` 不暴露本地路径或对象 URI。V2 提供 LocalArtifactCatalog，以原子更新的 run 级 manifest 把 opaque id 映射到当前 `RunArtifactStore`，读取时校验 size/SHA；LegacyArtifactRefAdapter 只读适配现有 relative-path Artifact。V5 只替换 Catalog/Blob 后端，不改变领域 Ref。

CandidateRecordV2 是完成 compile/render/evaluate 后一次写入的不可变记录；`record_hash` 计算时排除自身字段。重试或后续 Patch 创建新 candidate id 和 parent lineage。中途失败写不可变 `CandidateAttemptRecord`，不得原地修改 Candidate 或复用其 id。

结构证据：

- IntrinsicGenomeValidationResult：schema/DAG/ports/range/contract；
- IntentConstraintEvaluationV2：topology/instance/hole/required layer；
- RenderedStructureEvidenceV2：beauty render 重测 + required-layer 低分辨率 contribution pass；
- BasicEvaluationRecordV2：V1 Basic Oracle 及环境绑定。

分支内 Selector 字典序：runtime hard → intrinsic → 绑定该 hypothesis 的 rendered structure → Basic Oracle → complexity。跨 hypothesis 只比较相同原始目标和公共 BasicEvaluation Profile 下的可行分支 winner，并按第 4 节的冻结 tie-break 选择。

## 12. Graph 与失败路由

```text
START → initialize_run → prepare_context → ingest_target
→ measure_target_v2 → analyze_visual_layers_v2 → build_intent_variants
→ dequeue_hypothesis → plan_strategy → propose_seed_plans
→ expand_validate_seeds → dequeue_seed → prepare_candidate_attempt
→ compile_genome → render_candidate → evaluate_structure_and_basic_score
→ materialize_immutable_candidate → select_hypothesis_best
→ next_seed/next_hypothesis → select_cross_hypothesis_best
→ promote_or_skip_memory_v2 → finalize
```

| 条件 | 动作 |
|---|---|
| Seed/Genome 非法 | 记录拒绝并处理下一个 Seed |
| 单 Seed compile/render/evaluate 失败 | 保留 best，继续下一个 Seed |
| Renderer transient | 同一 RendererRequest hash 重放一次 |
| Compiler defect | Fail Run |
| Oracle 不可用且已有合法 best | 保存 best 并安全停止 |
| 所有模型 Seed 失败 | deterministic template fallback |
| fallback 失败 | `no_valid_candidate` |
| 预算耗尽 | finalize objective best 或 no-candidate |

Alpha 默认只读 Memory，并显式记录 skip promotion；质量门禁通过后仅晋升验证后的约束、模板策略和失败模式。

## 13. State 与 Budget

```python
PngToShaderV2Phase = Literal[
    "initialized", "measured", "interpreted", "intent_built",
    "seeding", "compiling", "rendering", "evaluating", "selecting", "finalized",
]

class BudgetVectorV2:
    wall_time_ms: int
    model_calls: int
    model_tokens: int
    render_calls: int
    candidate_attempts: int
    artifact_bytes: int
    cost_usd_micros: int

class BudgetStateV2:
    schema_version: Literal["budget_state_v2"]
    policy_hash: str
    revision: int
    limits: BudgetVectorV2
    used: BudgetVectorV2
    reserved: BudgetVectorV2
    exhausted_dimensions: tuple[str, ...]

class HypothesisBranchStateV2:
    target_hypothesis_id: str
    target_hypothesis_hash: str
    intent_ref: ArtifactRefV2
    strategy_ref: ArtifactRefV2 | None
    seed_refs: tuple[ArtifactRefV2, ...]
    seed_cursor: int
    hypothesis_best_id: str | None
    status: Literal["pending", "running", "completed", "failed"]

class PngToShaderV2State:
    state_schema_version: Literal["state_v2"]
    graph_id: Literal["png_to_shader_v2"]
    graph_version: str
    checkpoint_schema_version: str
    checkpoint_namespace: str
    project_id: str
    run_id: str
    run_revision: int
    phase: PngToShaderV2Phase
    evaluation_revision: int
    measurements_ref: ArtifactRefV2
    visual_interpretation_ref: ArtifactRefV2 | None
    request_constraint_set_ref: ArtifactRefV2
    hypothesis_branches: tuple[HypothesisBranchStateV2, ...]
    hypothesis_cursor: int
    objective_best_id: str | None
    candidate_summary_refs: tuple[ArtifactRefV2, ...]
    budget_state: BudgetStateV2
    stop_reason: str | None
```

State 只保存版本、游标、小型分支状态和 ArtifactRef。`checkpoint_namespace` 必须等于总纲规定的 `png-to-shader-v2:{run_id}`；V2 的 `evaluation_revision` 初始化为 0。Run/State mutation 使用 `run_revision` CAS，Budget mutation 使用独立 `budget_state.revision` CAS；每个维度的 `used + reserved` 不得超过 limits，恢复时只读取最后确认的 State。V1 State 不原地升级为 V2，只通过只读 Adapter 回放。

## 14. 实施增量

### V2.0：契约与标注

- 冻结 TargetHypothesis、ConstraintSet、Artifact、Genome Hash、Candidate、State 和 Budget Schema；
- 实现 LocalArtifactCatalog/Resolver 与 legacy read adapter；
- 为 20–50 张起始基准补 topology/instance/hole/required-layer 标签，关键类分母不足时扩充语料，不降低门槛；
- 按 visual family/hash group 拆成 development、validation、release-held-out 三份；development 用于迭代，validation 用于阶段 gate 和阈值校准，release-held-out 在 V2.3 代码、Prompt、模板和阈值全部冻结前保持封存；任何基于 release-held-out 的修订都必须创建新 release split/version；
- 当前 10 例归入 development/regression，不计入 release-held-out；
- validation 与 release-held-out 分别冻结关键标签分母；multi-instance、ring、hollow、required-highlight、required-rim、required-outline 在 release-held-out 中每类至少 10 个正例。样本可多标签重叠，分母不足时对应 gate 不得宣称通过；
- 更新 package discovery 和直接依赖；
- wheel 空环境 import/compiler smoke。

### V2.1：F02 Intent

- MeasurementsV2；
- RequestConstraintSet；
- VisualInterpretation Parser；
- Intent Builder/Validator；
- validation Intent 与结构 gate；release-held-out 此阶段保持封存。

F02 passing 后才能启动 F03。

### V2.2：F03 Genome 与 Compiler

- Genome union、Mask algebra、Parameter/Hash；
- Template/SeedPlan/Expander；
- Compiler AST/stdlib/emitter；
- 每类节点的真实 WebGL 测试。

### V2.3：F03 Graph/Harness

- V2 Graph/State/Routing/Service；
- NodeProvider/Node Lab；
- fixture/mock/real model benchmark；
- V1/V2 并存和切换测试。

## 15. 验收门槛

- 当前 10 例 Intent 10/10 合法；
- validation Intent 合法率 ≥80%；V2.3 release candidate 冻结后仅运行一次 release-held-out，合法率同样 ≥80%；
- 每个 Intent 至少 3 个合法且 `semantic_genome_hash` 不同的 Genome，并满足 Seed diversity 规则；
- 版本化 expected-primitives taxonomy 的模板覆盖 100%；
- Genome Compiler/WebGL 成功率 100%；
- 无贴图静态检查 100%；
- 同 Genome 编译 hash 相同；
- 同一 Renderer 环境、尺寸和 capture profile 下重复 5 次，任意两次 capture 的全图 RGB MAE ≤1/255；
- validation 和 release-held-out 分开报告；instance count 使用 exact match，ring/hollow/required-layer 使用逐类 recall/F1，并报告 macro recall/F1、95% CI 和 numerator/denominator；每个关键类 recall ≥90%，分母不得低于 V2.0 冻结值；
- TargetHypothesis id/hash 在 Intent/Seed/Candidate/State/证据中 100% 一致；
- RequestConstraintSet canonical hash、revision CAS、冲突拒绝/降级测试通过；
- Local Catalog、legacy adapter 和 Artifact 完整性测试通过；
- Hash golden/property tests 证明所有语义字段变化都会命中预期 hash；
- Candidate 不可变，失败重试只新增 Attempt/child Candidate；
- state_v2/BudgetStateV2 序列化、checkpoint 恢复和 V1 隔离测试通过；
- Candidate 全证据 hash 绑定；
- V1/V2 回放隔离；
- `make check`、Integration、Chromium、Node Lab 和 docs-check 通过。
