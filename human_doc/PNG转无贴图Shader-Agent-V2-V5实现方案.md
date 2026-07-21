# PNG 转无贴图 Shader Agent：V2–V5 实现方案

> 状态：第一稿，待架构与工程 Review  
> 编写日期：2026-07-16  
> 前置版本：PNG-to-Shader V1 / F09  
> 权威架构：`shaderforge-technical-architecture-aligned(1).svg`  
> 配套文档：`PNG转无贴图Shader-Agent最终架构.md`、`shaderforge-final-technical-plan.md`

---

## 1. 文档目的

本文把最终架构中的 Phase 2–5 转换为可直接进入开发的 V2–V5 实施方案，明确：

- 每个版本的目标、边界和非目标；
- 需要复用、扩展和退出主路径的 V1 能力；
- ShaderForge、Agent、Backend、Frontend 和 Harness 的模块改造；
- 核心数据契约、Graph 节点和确定性路由；
- 版本内实施增量、依赖顺序和退出门槛；
- V1 历史产物、API、Artifact 和评分版本的兼容策略；
- 质量、性能、成本、恢复和发布门禁。

本文只定义实现方案，不把尚未进入 `docs/FEATURES.md` 的能力描述成已经实现。实际开发仍遵守“一次只有一个 active 功能”的仓库规则。

---

## 2. 当前基线与 V2 启动条件

### 2.1 当前 V1 基线

截至 2026-07-16，V1 已具备：

- WebGL1 静态无贴图 RenderContract；
- PNG 规范化、bbox、调色板、像素探针、边缘和 ROI 测量；
- 静态 Validator；
- 项目自有 Playwright/Chromium WebGL1 Renderer；
- Basic Oracle、CandidateRecord 和单调 `current_best` Selector；
- 1 个确定性主控和 Analyst、Author、Critic 三个模型角色；
- 有界 initial、compile-repair、visual-refine Graph；
- LocalArtifactStore、项目 Memory、过程账本；
- Node Lab 20/20 生产节点、CLI、HTTP、页面和模块 benchmark；
- 固定 10 例 M5 benchmark、质量 gate 和匿名人工盲评。

最新正式 run 的自动检查已经 12/12 通过，但人工盲评 final/initial/tie 为 `3/4/3`，final 偏好率为 30%，低于冻结的 50% 门槛。主要阻塞不是编译或基本像素拟合，而是自动 objective 尚不能充分保护：

- 视觉拓扑；
- 主体实例数量；
- 轮廓与镂空；
- rim、outline、高光和阴影等语义层；
- 低频 affine 近似与人类结构感知之间的冲突。

### 2.2 V2 启动门槛

V2 开始前必须完成 F09 M6.2 的有界诊断，但不要求无限期通过 Prompt 调优强行让 V1 人工门禁转绿。启动条件为：

1. 冻结 M6.2 的逐例结构诊断和人工偏好证据；
2. 为现有基准补充版本化的拓扑、实例数量、轮廓/镂空和语义层标签；
3. 明确哪些字段属于确定性事实、VLM 推断、硬约束和软偏好；
4. 不增加 case id、golden、manifest 或 gate 特判；
5. V1 Graph、Artifact schema、正式失败报告继续只读可回放；
6. 在 `docs/FEATURES.md` 将下一项真实功能设为唯一 active 后再创建新领域包。

M6.2 若能使 V1 人工门禁通过，则将成功证据作为 V2 回归基线；若不能，则将冻结失败结论作为迁移到结构化 Genome 的依据，不继续无界堆叠 Prompt。

---

## 3. 总体演进路线

```text
V1：PNG → 测量 → VLM/LLM → 自由 GLSL → WebGL → Basic Oracle → 有限修订

V2：PNG → MeasurementsV2 → Intent IR → SeedPlan → Effect Genome
        → Deterministic Compiler → WebGL → Seed Selection

V3：Effect Genome → ParameterManifest → Cheap/WebGL Render
        → Oracle V2 → Deterministic Search → AI-off Current Best

V4：Search Stagnation → Archive Summary → GenomePatch
        → Re-optimize → VLM Pairwise / HITL → Final Selection

V5：Async Run → Durable Worker/Checkpoint → SSE/Cancel/Resume
        → Artifact/Cache/Renderer Pool → Replay/Dashboard/Nightly Gate
```

| 版本 | 主要目标 | 产品定位 | 建议周期 |
|---|---|---|---:|
| V2 | Intent IR、Effect Genome、Compiler、多 Seed | 内部 Alpha | 4–5 周 |
| V3 | Oracle V2、确定性参数搜索、完整 AI-off | Beta | 3–4 周 |
| V4 | StructureEvolution、VLM Pairwise、HITL | Release Candidate | 2–3 周 |
| V5 | 异步任务、恢复回放、对象存储和正式 UI | 正式版本 | 3–5 周 |

总关键路径约 12–17 周。V2 与 V3 必须串行；V5 控制面设计可在 V3 后期开始，但前端整体切换应等待 V4 数据契约稳定。

---

## 4. 跨版本不变量

### 4.1 V1 历史不可破坏

- 不原地修改 V1 Artifact schema；
- 不用 V2/V3 Score 重新解释历史 V1 `current_best`；
- V1 正式 benchmark、盲评和失败证据只增不改；
- 历史 V1 Run 使用旧 schema 只读回放；
- V2/V3 使用独立 Graph、State、Candidate 和 Score 版本。

### 4.2 RenderContract 保持稳定

V2–V5 继续使用 `webgl1_static_no_texture_v1`：

- GLSL ES 1.00；
- `precision mediump float`；
- 静态 `u_time = 0`；
- 禁止任何纹理采样；
- 坐标、DPR、Y 翻转、alpha 和颜色空间保持统一；
- 真实 WebGL compile/link/draw/capture 是最终事实。

只有运行时契约真实发生变化时才创建新的 contract id，不能把 Agent 版本号混入 RenderContract。

### 4.3 Agent 与确定性内核边界

Agent 负责视觉分层、Strategy/Template 选择、SeedPlan、搜索停滞后的 GenomePatch，以及晋级候选 Pairwise 评审。

ShaderForge 负责测量、Intent/Genome/Patch 校验、Compiler、Renderer、Oracle、Search、Cache、Archive、Selector、Artifact、预算和停止规则。

参数搜索期间不调用模型；编译是否成功、分数是否改善、预算是否耗尽、候选是否替换 `current_best` 不交给 LLM/VLM。

### 4.4 State 与 Artifact 边界

Checkpoint 只保存 run/project id、阶段、迭代、Artifact 引用、current_best、小型分数摘要、预算使用量、停止原因和版本号。

原图、mask、edge、完整 Intent/Genome/Patch、GLSL、渲染图、残差图、评分详情、搜索轨迹和模型原文进入 Artifact Store。

### 4.5 版本绑定

每个候选和最终 Manifest 至少绑定：

```text
input hash
contract id
intent schema version
genome schema version
compiler version
renderer version
metric version
search policy version
prompt/model version
random seed
budget policy
code/build version
artifact hashes
candidate lineage
```

不同 metric、compiler、renderer、render size 或 Genome schema 的候选禁止直接比较。

### 4.6 Graph、Checkpoint 与注册策略

每个改变 State/Candidate 恢复语义的大版本使用独立版本矩阵：

| graph_id | state schema | checkpoint namespace | candidate schema | 状态 |
|---|---|---|---|---|
| `png_to_shader_v1` | `png_to_shader_state_v1` | `png-to-shader-v1:{project_id}`；兼容清理历史裸 `{project_id}` | CandidateRecord V1 | 产品/历史基线 |
| `png_to_shader_v2` | `png_to_shader_state_v2` | `png-to-shader-v2:{run_id}` | CandidateRecord V2 | V2 Alpha |
| `png_to_shader_v3` | `png_to_shader_state_v3` | `png-to-shader-v3:{run_id}` | CandidateRecord V2 + Search | V3 Beta |
| `png_to_shader_v4` | `png_to_shader_state_v4` | `png-to-shader-v4:{run_id}` | CandidateRecord V2 + Patch/Preference | V4 RC |

Run Manifest 保存 graph id、state schema、supported build range 和 checkpoint revision。V2–V4 使用 run 级 namespace；旧 checkpoint 只由对应版本代码或显式 adapter 读取。V5 Worker 必须调用所选 Graph 的 namespace builder，不能把 `thread_id` 写成无版本前缀的裸 run id。

`langgraph.json` 一次只注册当前 product-active Graph；未发布版本通过 Builder、Node Lab 和 benchmark 运行。切换产品 Graph 前先停止接收旧版本新 Run，并排空、终止或固定迁移已有在途 Run。历史查看默认读取 Ledger/Artifact，不要求旧 Graph 永久公开注册。

---

# 5. V2：Intent IR + Effect Genome + Deterministic Compiler

## 5.1 目标与非目标

V2 的目标是把“模型直接输出完整 GLSL”替换为“模型输出视觉解释和 SeedPlan，确定性系统生成 Genome 与 GLSL”。

V2 必须交付：

- `TargetMeasurementsV2`；
- 正式 `IntentIR`；
- 8–15 类可执行 Genome 节点；
- Intent → SeedPlan → Genome Mapper；
- Genome Validator 与 canonical hash；
- Genome → WebGL1 GLSL Compiler；
- 至少 3 个合法 Seed；
- V2 CandidateRecord、State、Graph、Artifact 和 benchmark；
- 对拓扑、实例数量、轮廓/镂空和语义层的硬保护。

V2 明确不做参数搜索、CMA-ES、MAP-Elites、StructureEvolution、完整 HITL、Raymarch、动画、自由噪声和自由 GLSL 反解析。

## 5.2 复用与退出主路径

| V1 能力 | V2 处理方式 |
|---|---|
| RenderContract、预算、停止原因 | 直接复用，新增 V2 策略类型 |
| PNG normalize | 直接复用 |
| `measure_target()` | 保留 V1，新增 `TargetMeasurementsV2` |
| WebGL1 Renderer | V2 直接复用并增加批量接口；诊断 pass/cheap renderer 归 V3 |
| Shader Validator | 复用并增加 Compiler 数值安全检查 |
| LocalArtifactStore | 增加 Intent/Genome/Compilation Artifact |
| Basic Oracle | V2 Seed 排序暂时复用，保留为 V3 对照 |
| Selector 思想 | 新建版本化 Candidate/Score 接受规则 |
| LLMGateway、bounded node、Parser repair | 用于 VisualInterpretation 和 SeedPlan |
| Node Lab、Benchmark Harness | 通过生产 Provider 接入新节点 |
| measurement affine seed | 迁移为 `AffineEllipseTemplate`，直接创建 Genome |
| `ShaderAuthorResult.glsl` | 退出 V2 主路径，仅留 V1 兼容 |
| compile-repair/visual-refine Author | 退出 V2 主路径 |

## 5.3 推荐目录

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
│   ├── manifest.py
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
└── analysis/
    ├── segmentation.py
    ├── geometry.py
    ├── palette_lab.py
    └── regions_v2.py
```

新公共接口加入 `shaderforge.public`。Agent 不直接 import 内部实现。建议将 Pydantic 作为显式项目依赖，用 frozen model 和 discriminated union 表达 Intent 与 Genome Node。

## 5.4 TargetMeasurementsV2

在 V1 基础上增加：

- 多个 mask hypothesis 及其 confidence；
- bbox、center、area、axes、orientation；
- `fill_topology: solid | hollow | ring | open`；
- `component_count`、`instance_count` 和 `hole_count`；
- `relations: overlap | contains | subtracts | touches | disjoint`；
- 轮廓闭合情况；
- Lab 调色板及区域均值/方差；
- background、subject、shadow、glow、highlight、rim 候选区域；
- 对称性、径向性、梯度方向、边缘软硬；
- 多尺度 edge 和显著区域 Artifact 引用；
- 每项测量的 evidence id 和 confidence。

任何自动 mask 都不能被无条件当成事实。主体与阴影粘连时必须保存多个解释，并把不确定性传入 Intent。

## 5.5 RequestConstraintSet 与 Intent IR

V2 必须显式继承 V1 已有的用户约束和项目连续性。进入 `build_intent()` 前先构造版本化 `RequestConstraintSet`，来源包括：

- 本次请求的自然语言约束；
- RenderContract、输出尺寸和质量/复杂度预算；
- 用户提供或确认的 mask、region lock、color lock；
- `prepare_context` 返回的已确认项目约束和用户偏好；
- 当前运行的部署和安全策略。

约束合并优先级固定为：

```text
RenderContract
  > 用户显式 hard constraint
  > 用户 region/color lock
  > 已确认 Project Memory
  > 高置信确定性测量
  > VLM inference
  > 模板默认值
```

每条 Constraint 保存 `source`、`source_revision`、`confidence`、`verification_status`、`evidence_refs` 和冲突诊断。只有人工确认或达到冻结可靠性阈值的事实才能升级为 hard constraint；中低置信结构应作为 soft preference，或产生多个 Intent/Seed 分支。

```python
class IntentIR:
    schema_version: Literal["intent_v2"]
    target_sha256: str
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
    evidence_refs: tuple[str, ...]
```

`ObjectIntent` 至少表达 object id、geometry family、`fill_topology`、component/instance/hole count、几何范围、confidence、来源和 evidence refs。对象之间的 overlap、contains、subtracts、touches 和 disjoint 只进入 `RelationIntent`，不能与单对象填充拓扑压成一个互斥枚举。

模型节点输出独立 `VisualInterpretationV2`，只包含视觉层推断、primitive candidates、策略假设、不确定项和 evidence refs；它不携带确定性 facts，也不直接成为 Intent。`build_intent()` 是唯一合并入口。

`VisualLayerIntent` 至少支持：

```text
background
shadow
base_fill
color_lobe
haze
rim
outline
highlight
detail
```

每层保存 order、target region、primitive candidates、颜色/opacity 范围、required/optional、confidence 和 evidence refs。

Intent 必须区分 `observed`、`inferred`、`optimizable` 和 `hard_constraint`。VLM 不允许写入 target hash、图片尺寸、测量 bbox 等确定性事实字段；`build_intent()` 负责合并 Measurements、VisualInterpretation、RequestConstraintSet 和 Project Context，并产生完整校验报告。

## 5.6 Effect Genome v0

```python
class EffectGenome:
    schema_version: Literal["genome_v0"]
    genome_id: str
    contract_id: str
    strategy: str
    nodes: tuple[EffectNode, ...]
    edges: tuple[EffectEdge, ...]
    parameters: tuple[ParameterSpec, ...]
    output_node_id: str
    provenance: GenomeProvenance
```

首期约 14 类节点：

- 几何：`CircleSDF`、`EllipseSDF`、`RoundedRectSDF`；
- 填充：`SolidFill`、`LinearGradient`、`GaussianColorLobe`；
- 光效：`Shadow`、`Glow`、`RimBand`、`OutlineBand`、`ArcHighlight`；
- 合成：`Mask`、`OverBlend`；
- 输出：`ColorOutput`。

节点只引用稳定的 parameter path/id，不重复保存参数值；唯一参数值事实源是 `EffectGenome.parameters`。平移、缩放和旋转首期作为几何节点公共参数。重复对象通过多个几何节点表达。

Genome v0 必须定义 typed mask/SDF algebra，至少支持 `UnionMask`、`IntersectionMask` 和 `DifferenceMask`，或者为 `Mask` 明确定义 combine mode、输入输出类型和抗锯齿语义。环形、镂空、多个组件和 overlap 不能只依靠未定义的“Mask”概念表达。节点总量仍控制在约 15 类，按基准证据扩展。

## 5.7 ParameterSpec

```python
class ParameterSpec:
    path: str
    dtype: Literal["float", "vec2", "vec3", "color"]
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

路径 grammar、单位、坐标/颜色空间和浮点序列化必须版本化。路径必须稳定，例如：

```text
nodes.subject_geometry.center.x
nodes.main_rim.width
nodes.highlight_upper_left.angle_center
```

Genome 需要四类不同 hash：

- `topology_hash`：节点类型、端口和边，不含参数值；
- `parameter_layout_hash`：参数 path、dtype、范围、单位和排序；
- `semantic_genome_hash`：topology + 规范化参数值 + contract；
- `record_hash`：包含 genome id、provenance 和版本的持久记录 hash。

canonical JSON 必须定义稳定字段排序、浮点精度、负零处理并拒绝 NaN/Inf；`genome_id`、时间戳和 provenance 不进入可用于 Compiler/Cache 的 semantic hash。

## 5.8 Genome Validator

Genome Validator 只检查 intrinsic validity：schema/contract、id/path 唯一、DAG 无环、端口类型、output 可达、参数范围、affected region 引用、复杂度、无贴图、数值安全和 canonical hash 稳定。

topology、instance、holes 和 required layer 属于 Genome 相对 Intent/Constraint 的符合性，只能由 `IntentConstraintEvaluator` 判断，不能混入 intrinsic Validator。

## 5.9 Intent → Genome Mapper

```text
Intent
  → Template Matcher
  → 参数初值估计
  → 3 个结构化 SeedPlan
  → Deterministic Genome Expander
  → Genome Validator
```

默认 Seed：最低复杂度 2D 分层、高置信语义层增强、备选几何/颜色解释。模型只输出 template id、layer mapping 和有限 parameter override，最终 Genome 由确定性 Expander 创建。

## 5.10 Genome → GLSL Compiler

编译流程：验证/canonicalize → 稳定拓扑排序 → Node AST → 安全 stdlib → source map → `CompilationBundle` → Shader Validator → WebGL compile/link/draw。

`CompilationBundle` 至少包含 semantic Genome hash、compiler version、GLSL/hash、node-line map、`CompilerParameterTable`、estimated ops、numerical risks 和 diagnostics。

V2 的 `CompilerParameterTable` 只描述参数如何绑定到生成源码，不包含搜索归一化、step hint 或 frozen 状态；V3 再从它派生 `SearchParameterManifest`。

相同 Genome、Compiler 和 Contract 必须产生相同源码 hash。Compiler 生成非法 GLSL 属于 `compiler_defect`，不能交给 LLM 修源码。

## 5.11 V2 主干契约

V2.0 必须先冻结以下严格 Schema，不能继续在 State 中使用裸 `str` 和 `dict` 表达长期契约。

```python
class ArtifactRefV2:
    artifact_id: str
    sha256: str
    kind: str
    schema_version: str
    content_type: str
    size_bytes: int

class SeedPlanV1:
    schema_version: Literal["seed_plan_v1"]
    template_id: str
    template_version: str
    layer_bindings: tuple[LayerBinding, ...]
    parameter_overrides: tuple[AllowedOverride, ...]
    source: Literal["rule", "model", "memory"]
    random_seed: int
    evidence_refs: tuple[ArtifactRefV2, ...]

class CandidateRecordV2:
    candidate_id: str
    run_id: str
    parent_candidate_id: str | None
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
    revision: int
    status: str

class BudgetStateV2:
    wall_time_used_s: float
    model_calls_used: int
    renders_used: int
    seeds_processed: int

class CandidateSummaryV2:
    candidate_id: str
    semantic_genome_hash: str
    hard_constraints_passed: bool
    structure_status: str
    basic_loss: float | None
    complexity_score: float
```

ArtifactRef 只能是 opaque id + hash + metadata，不能保存本地绝对路径、任意 URI 或未校验字符串。

结构保护在 V2 就必须形成可执行证据，而不能等 V3：

- `IntrinsicGenomeValidationResult`：Genome 本身是否满足拓扑、实例和 required layer；
- `IntentConstraintEvaluationV2`：候选与 RequestConstraintSet/Intent 的一致性；
- `RenderedStructureEvidenceV2`：V2 使用 beauty render 重测实例、hole 和轮廓，并为 required semantic layer 增加最小低分辨率 contribution diagnostic pass，验证其实际可见，避免“节点存在但 opacity 为 0、在画布外或被遮挡”；V3 再把该能力泛化为完整 object/layer mask 和多保真诊断系统；
- `BasicEvaluationRecordV2`：绑定 V1 Basic Oracle 分数及其完整评估环境。

V2 Selector 使用字典序：

```text
runtime/no-texture hard constraints
  → intrinsic Genome constraints
  → rendered structure constraints
  → Basic Oracle
  → complexity
```

V3 的 `ScoreBreakdownV2` 是对上述最小结构证据的扩展，而不是第一次建立结构判定。

## 5.12 V2 Agent Graph

```text
START
  → initialize_run
  → prepare_context
  → ingest_target
  → measure_target_v2
  → analyze_visual_layers_v2
  → build_intent
  → plan_strategy
  → propose_seed_plans
  → expand_and_validate_seeds
  → dequeue_seed
  → compile_genome
  → materialize_candidate_v2
  → render_candidate
  → score_seed
  → select_current_best_v2
  → more_seeds ? dequeue_seed : finalize_candidate_set
  → promote_or_skip_memory_v2
  → finalize
```

V2 主路径移除 compile-repair、visual-critic/refine 和自由 GLSL 参数修改。`promote_or_skip_memory_v2` 必须显式存在：内部 Alpha 默认只读 Memory，并记录 `skip_memory_promotion(reason=alpha_not_quality_approved)`；质量门禁通过后才允许晋升验证后的约束、模板策略和失败模式，禁止写入完整 Intent、Genome 或 GLSL。

完整条件路由至少包含：

| 条件 | 动作 |
|---|---|
| SeedPlan/Genome 非法 | 写拒绝证据并处理下一个 Seed |
| 单 Seed compile/render/score 失败 | 保留已有 best，处理下一个 Seed |
| Renderer transient | 同候选重放一次，再拒绝或失败 |
| Compiler defect | 失败当前 Run，不交给模型修源码 |
| Oracle 不可用且已有合法 best | 保存 best 并安全停止 |
| 预算耗尽且已有 best | finalize current best |
| 所有模型 Seed 失败 | 尝试 deterministic template fallback |
| deterministic fallback 也失败 | 明确 `no_valid_candidate` |

V2 使用独立 Builder、State 和 checkpoint namespace；开发期通过直接 Builder、Node Lab 和 benchmark 运行，不立即在 `langgraph.json` 同时注册第二个产品 Graph。产品切换时一次只注册一个 active Graph，并在切换前排空或终止旧版本在途 Run。

## 5.13 V2 State

```python
class PngToShaderV2State:
    project_id: str
    run_id: str
    phase: str
    target_measurements_ref: ArtifactRefV2
    visual_interpretation_ref: ArtifactRefV2 | None
    request_constraints_ref: ArtifactRefV2
    intent_ref: ArtifactRefV2
    strategy_ref: ArtifactRefV2
    seed_refs: tuple[ArtifactRefV2, ...]
    seed_cursor: int
    current_candidate_id: str | None
    current_best_id: str | None
    candidate_summary_refs: tuple[ArtifactRefV2, ...]
    budget_state: BudgetStateV2
    stop_reason: str | None
```

## 5.14 V2 实施增量

### V2.0：契约和基准标注

- 冻结版本策略；
- 冻结 ArtifactRef、SeedPlan、CandidateRecord、BudgetState、ConstraintEvaluation 和结构证据 Schema；
- 更新 `pyproject.toml` 的显式 package 列表或受控 package discovery；
- 把 Pydantic 列为直接依赖，并同步 lazy exports、`shaderforge.public` 和 import-boundary tests；
- 增加“build wheel → 空环境安装 → import 新包 → 运行最小 Compiler”门禁；
- 为 20–50 张分层基准图补充期望结构；
- 当前 10 例增加 topology、instance count、holes 和 required layers；
- 建立 schema、canonical hash 和 Artifact manifest 测试。

### V2.1：Intent 内核（F02）

- TargetMeasurementsV2；
- IntentIR Builder/Validator；
- RequestConstraintSet 和 Project Context 合并；
- Intent/结构标签 held-out gate；

F02 通过并标记 passing 后，才切换到 F03。

### V2.2：Genome 与 Compiler Vertical Slice（F03）

- Genome v0 discriminated union；
- ParameterSpec、模板和 Validator；
- measurement affine 迁移为 Genome Template。
- 先打通 Circle/Ellipse + Fill + Output；
- 再增加 gradient、shadow、rim、outline、highlight 和 blend；
- 接入 Validator 和 WebGL Renderer；
- 建立源码稳定性和数值安全测试。

### V2.3：Graph、Node Lab 与 Benchmark（F03）

- V2 State、Node、routing 和 Service；
- 生产 NodeProvider descriptor/binding；
- 离线 Prompt/Parser fixture；
- V1/V2 并存测试；
- V2 Intent/Seed/Compiler benchmark 和 gate。

## 5.15 V2 验收门槛

- 当前 10 例 Intent 合法率 10/10；
- 扩展基准合法 Intent 率 ≥ 80%；
- 每个 Intent 至少 3 个合法 Genome；
- `expected_primitives` 模板覆盖 100%；
- 合法 Genome 的 Compiler/WebGL 成功率 100%；
- 无贴图静态检查 100%；
- 同 Genome 重编译源码 hash 相同；
- 重复渲染像素一致或误差 ≤ 1/255；
- 在 held-out 结构标注集上，instance、ring/hollow、required layers 准确率 ≥ 90%；
- Intent/Genome/GLSL/render/hash/provenance 完整绑定；
- V1/V2 可并存回放；
- `make check`、Integration、真实 Chromium、Node Lab 和 docs-check 全部通过。

---

# 6. V3：Advanced Oracle + Deterministic Parameter Search

## 6.1 目标与非目标

V3 的目标是在固定 Genome 拓扑下稳定优化参数，并建立可交付的 AI-off 闭环：

```text
TargetMeasurementsV2
  → Rule-based Intent
  → Deterministic Template Seeds
  → Compiler
  → Oracle V2
  → Search
  → Final Genome/GLSL
```

V3 必须交付：

- Diagnostic RenderBundle；
- `ScoreBreakdownV2`；
- topology、instance、coverage 和局部语义层评分；
- ParameterManifest 和 `flatten/unflatten`；
- 分块 coordinate descent；
- Content Cache；
- SearchCursor、停滞检测和 ArchiveSummary；
- AI-off benchmark；
- 多保真 cheap/WebGL 渲染闭环。

V3 不做 Agent 结构变异、在线 MAP-Elites、VLM 控制参数搜索，也不为每次参数扰动创建 LangGraph Node。

## 6.2 推荐目录

```text
src/shaderforge/
├── evaluation/
│   ├── models_v2.py
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
│   ├── engine.py
│   ├── coordinate_descent.py
│   ├── cmaes.py
│   ├── archive.py
│   ├── stopping.py
│   └── ARCHITECTURE.md
└── store/
    └── evaluation_cache.py
```

轮廓距离变换若确实需要 SciPy，应作为显式依赖并记录理由；Lab 转换优先使用可测试的 NumPy 实现。

## 6.3 ScoreBreakdownV2

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
```

`ScoreBreakdownV2` 只保存原始/聚合视觉指标，不内嵌随用户反馈变化的 `hard_constraints_passed`。约束结果使用独立记录：

```python
class ConstraintEvaluationRecord:
    constraint_set_hash: str
    evaluation_revision: int
    evaluation_evidence_key: EvaluationEvidenceKey
    hard_constraints_passed: bool
    violations: tuple[ConstraintViolation, ...]
```

Oracle 的可执行配置必须独立版本化：

```python
class EvaluationProfileV2:
    profile_id: str
    profile_version: str
    color_space: str
    normalization_rules: dict[str, str]
    metric_weights: dict[str, float]
    roi_weight_floor: float
    hard_thresholds: dict[str, float]
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
    target_hypothesis_hash: str
```

一个 Candidate 可以拥有多个 `EvaluationRecord`，例如 cheap、WebGL preview、WebGL target-size 和不同诊断 pass。`CandidateRecordV2` 只能保存 evaluation refs，数据库 CandidateIndex 也不得压成单个模糊 score。

指标至少包含：

- 全局 RGB RMSE/MAE 和多尺度差异；
- Lab ΔE；
- target/candidate mask IoU；
- bbox、center、area、axes；
- instance count、holes count、topology match；
- contour distance/Chamfer 或 distance-transform loss；
- edge 位置、强度、宽度和方向；
- background、subject、shadow、highlight、rim、outline 独立 ROI；
- target/candidate mask 并集内颜色损失；
- false-positive/false-negative coverage；
- 节点数、估算 ops 和数值风险；
- Lab、edge 和 mask residual Artifact。

小高光、小对象和薄 rim 必须有最低有效权重，不能被大面积背景稀释。

## 6.4 硬约束与 Selector

Selector 按固定顺序判断：

```text
1. static/WebGL/no-texture hard constraints
2. topology/instance/required-layer hard constraints
3. target problem-domain gain
4. protected-region regression
5. total objective gain
6. unjustified complexity growth
7. optional VLM/HITL tie-break
```

拓扑、实例数量和 required semantic layers 不得仅作为低权重总分分量。候选若把 dual disks 压成单个 ellipse、把 ring 填实、删除关键高光或阴影，即使 RMSE 更低也不得自动晋升。

只有 EvaluationEvidenceKey 完全一致的 Score 才能进入普通 Selector。Challenger 要替换 incumbent 时，必须在同一真实 WebGL `renderer_environment_id` 和目标尺寸下重新渲染、评分两者；cheap score 只用于内层排序。

## 6.5 Diagnostic RenderBundle

```python
class DiagnosticProgramSet:
    beauty_shader: str
    object_mask_shaders: dict[str, str]
    layer_mask_shaders: dict[str, str]
```

WebGL1 没有通用 MRT，因此采用：

- 高频内循环：96/128 px cheap renderer 输出 image、alpha、object masks、layer masks、coverage 和 contribution maps；
- block winner：真实 WebGL 目标尺寸 beauty pass 与必要诊断 pass；
- final/current_best：真实 WebGL 最终尺寸完整评分。

不同 Renderer 或尺寸的分数不得直接比较。Cheap renderer 只用于筛选，最终接受必须绑定真实 WebGL 证据。

## 6.6 ParameterManifest 与向量化

```python
class SearchParameterManifest:
    schema_version: str
    parameter_layout_hash: str
    entries: tuple[ParameterEntry, ...]
    manifest_hash: str
```

每个 entry 保存 stable path、上下界、`[0,1]` 归一化、problem block、affected regions、semantic role、step hint 和 frozen 状态。

必须提供：

```python
flatten(genome, manifest) -> NormalizedVector
unflatten(vector, manifest) -> EffectGenome
```

Search Manifest 绑定不随参数值变化的 `parameter_layout_hash`，不能绑定每次 `unflatten` 都变化的完整 Genome hash。排序固定为“问题域优先级 + parameter path”，不能依赖 JSON 字段插入顺序。

Search Engine 使用独立 `SearchJournal` 保存 Node 内恢复点：journal key 由 run/seed/block/search-policy 派生，每个 accepted step 或 sweep 原子写 Cursor、预算 reservation 和结果 ref。Graph State 只保存最后确认的 journal cursor ref；Node 内崩溃后从 Journal 恢复，不能假设 LangGraph 会在 Node 返回前提交 State。

## 6.7 Search Engine

Search 属于 ShaderForge，不属于 Agent Node：

```python
async def optimize_block(
    request: SearchRequest,
    evaluator: CandidateEvaluator,
) -> SearchBlockResult:
    ...
```

`CandidateEvaluator` 负责：

```text
Genome
  → canonical hash
  → compile/cache lookup
  → render
  → Oracle
  → CandidateRecordV2
```

默认参数块顺序：

1. geometry；
2. background_shadow；
3. base_color_field；
4. rim_edge；
5. highlight；
6. fine_detail；
7. global_balance。

首个优化器使用确定性 coordinate descent：

- 每个维度尝试 `+step/-step`；
- 每轮只接受最佳合法候选；
- 无改善时 step 减半；
- step 小于阈值或连续 sweep 无改善时停止；
- tie-break 使用 canonical Genome hash；
- 所有随机扰动保存固定 seed。

CMA-ES 只有在 coordinate descent、Oracle 性质测试和可复现门禁全部通过后才加入，并复用同一 Manifest、Evaluator、Cache、Archive 和停止策略。

## 6.8 Cache

缓存分层定义：

```text
RenderCacheKey
  = semantic_genome_hash + contract + compiler + renderer_environment + size + pass_set

RawMetricCacheKey
  = render_hash + target/mask/ROI hypothesis + metric implementation + EvaluationProfile

SelectionResult
  = 默认不缓存；若缓存必须加入 ConstraintSet、SelectorPolicy 和 evaluation_revision
```

其中底层 Render Cache 至少包含：

```text
canonical_genome_hash
target_image_hash
contract_id
compiler_version
renderer_version
metric_version
render_size
diagnostic_pass_set
```

RawMetric Cache 还必须包含 ROI/mask hypothesis hash、EvaluationProfile hash 和目标约束相关的评估输入。用户反馈改变 ConstraintSet、保护区或权重后，旧 SelectionResult 必须失效；不能直接复用旧 `hard_constraints_passed`。

## 6.9 V3 Agent Graph

```text
select_seed_best
  → prepare_search
  → optimize_parameter_block
  → materialize_block_winner
  → confirm_with_webgl
  → score_candidate_v2
  → select_current_best_v2
  → decide_after_search
       ├─ 当前 block 继续
       ├─ 下一个 block
       ├─ 下一个 seed
       └─ 达标/预算结束 → finalize
```

不能为每次参数扰动创建 LangGraph Node。`optimize_parameter_block` 内部调用 Search Engine；每个 accepted step 或 sweep 至少持久化一次 `SearchCursor`，block 结束再提交 block summary，避免进程崩溃后整块重复执行。

State 新增：

```text
search_cursor_ref
active_seed_id
active_parameter_block
evaluation_count
cache_hit_count
stagnation_count
archive_summary_ref
```

V3 条件路由至少覆盖：

| 条件 | 动作 |
|---|---|
| 单次 cheap evaluation 失败 | 记录失败分母并继续当前 block |
| block winner WebGL 复核失败 | 拒绝 winner，保留 incumbent |
| Renderer transient/context loss | 同候选重放一次 |
| Oracle/Profile/Cache 版本不一致 | 禁止比较，重新评估或终止当前 block |
| Search 数值异常或越界 | 拒绝候选并记录 parameter path |
| 预算/时间耗尽 | 保存 SearchCursor 和 objective best 后 finalize |
| 取消 token 触发 | 停止新评估并安全 finalize/cancel |
| 模型不可用 | 进入 AI-off Intent/Seed 路径，而不是失败整个 Run |

V3 继续使用 V2 的 `promote_or_skip_memory` 生命周期；只有 Genome 路径通过冻结质量门禁后，才晋升经过验证的模板、参数先验和失败模式。

## 6.10 AI-off 路径

模型不可用时不得直接 finalize。AI-off 使用规则 Intent、确定性模板 Seed、同一 Compiler、Oracle 和 Search，输出完整 Genome/GLSL/PNG/Score/Manifest。

VLM/LLM 只增强视觉层解释、模板选择和结构假设，不是编译、评分或优化的必要依赖。

## 6.11 V3 实施增量

### V3.0：Diagnostic Render 与 Oracle 内核

- 建立 cheap renderer；
- 输出 mask/id/contribution；
- 实现颜色、几何、拓扑、edge、coverage、complexity 和 residual；
- 建立单变量扰动性质测试；
- 建立 cheap/WebGL parity。

### V3.1：ParameterManifest 与 CandidateEvaluator

- 实现 stable path、flatten/unflatten；
- 实现 SearchRequest、SearchCursor、CandidateEvaluator；
- 接入 Compiler、Renderer、Oracle、Artifact 和 Cache；
- 建立 metric/version 不匹配拒绝规则。

### V3.2：Coordinate Descent 与 Graph

- 实现分块 coordinate descent；
- 实现 eval/time/stagnation/cancel 预算；
- 实现 block winner 的真实 WebGL 复核；
- 实现 V3 Graph、routing、NodeProvider 和 Search Artifact。

### V3.3：AI-off 与 Benchmark

- 实现 rule-based Intent 和 deterministic seeds；
- 冻结 AI-off manifest、gate 和报告；
- 与 AI-on 使用相同 Compiler/Renderer/Oracle/Search；
- 对 current_best、保护区和成本进行正式回归。

## 6.12 V3 验收门槛

Oracle：

- 颜色、中心、尺寸和 edge 扰动产生预期方向的 loss；
- 删除实例、hole、主体或小高光时 topology/coverage/ROI 明显失败；
- 只改背景时主体保护区退化 < 2%；
- 只改高光时背景指标变化 < 2%；
- 单变量扰动单调正确率 ≥ 95%。

Search：

- `flatten → unflatten` 全量 round-trip；
- normalized value 全部位于 `[0,1]`；
- 固定 seed、预算和版本时 best Genome hash 相同；
- timeout、最大评估数、停滞和取消正确停止；
- cache hit 与原计算 ScoreBreakdown 一致；
- cheap 和 WebGL EvaluationRecord 不进入同一普通比较；
- metric version 不一致时拒绝比较；
- current_best 非单调运行数为 0。

Benchmark：

- compile/static/traceability 100%；
- AI-off 10/10 输出 Genome、GLSL、PNG、Score 和 Manifest；
- 至少 70% 案例相对各自 best seed 改善 ≥ 0.005；
- 中位相对视觉损失改善建议 ≥ 5%；
- 无关保护区域最大退化 ≤ 2%；
- cheap/WebGL：RGB MAE ≤ 3/255，诊断 mask RGB MAE ≤ 3/255、mask IoU ≥ 0.98；当前 `alpha:false` RenderContract 不使用无意义的 PNG alpha MAE；
- pink-gel 继续沿用已有 bbox、global RMSE 和四个关键 ROI 冻结门禁；
- 新结构门禁和独立人工盲评同时通过后，Genome 路径才可成为产品默认。

---

# 7. V4：StructureEvolution + VLM Pairwise + HITL

## 7.1 目标与非目标

V4 在固定拓扑参数搜索停滞后，让 Agent 提出受限结构修改，并用晋级评审和人工约束补充纯指标难以覆盖的视觉偏好。

V4 必须交付：

- StagnationReport 和 ArchiveSummary；
- `GenomePatch` 及纯函数 Patch Engine；
- `StructureEvolutionAgent`；
- top-k + novelty CandidateArchive；
- VLM Pairwise 协议；
- HITL FeedbackRecord、ConstraintSet 和 PreferenceSet；
- 结构修改后的重新优化和可归因谱系。

V4 不允许 Agent 输出任意 GLSL，不在正常参数搜索每代调用 Agent，不允许 VLM 推翻 hard constraints，也不在未证明需要前直接实现完整 MAP-Elites。

阻塞式 V4 只在 Node Lab 和离线 benchmark 注入反馈；真正的 `awaiting_feedback`、跨进程暂停与恢复在 V5 实现。

## 7.2 GenomePatch

允许操作：

```text
AddNode
RemoveNode
ReplaceNode
Connect
Disconnect
UpdateParameterRange
```

```python
class GenomePatch:
    schema_version: Literal["genome_patch_v1"]
    patch_id: str
    base_candidate_id: str
    base_genome_id: str
    base_genome_hash: str
    base_revision: int
    intent: str
    problem_domain: str
    operations: tuple[PatchOperation, ...]
    expected_regions: tuple[str, ...]
    expected_metric_directions: dict[str, str]
    complexity_delta_limit: int
    prompt_version: str
    model_ref: str
    seed: int
```

Patch 六级门禁：

1. schema；
2. base revision/hash 乐观并发；
3. 节点引用和端口类型；
4. DAG/拓扑；
5. RenderContract、硬约束和复杂度预算；
6. dry compile。

`apply_patch(base, patch)` 必须是纯函数，相同输入产生相同 Genome hash。禁止 Patch 携带任意 GLSL 文本。`UpdateParameterRange` 默认只能收窄范围；放宽范围必须经过 node hard domain 截断，或绑定明确 HITL `approve_complexity/range_expansion` 证据。

## 7.3 StagnationDetector

只有以下条件全部成立，确定性 `decide_next` 才能触发 StructureEvolutionAgent：

- 已完成最小评估次数；
- 连续参数阶段增益低于 epsilon；
- 当前参数块已经耗尽；
- 残差模式持续存在；
- 残差能映射到结构问题域；
- 仍有结构和复杂度预算。

V3 的 SearchStagnation 只描述参数级停滞；V4 的 `StructuralStagnationReport` 才用于触发结构升级。它至少包含评估数量、阶段增益、残差区域、参数敏感度、当前拓扑/语义层、保护区退化、complexity headroom、推荐问题域和证据 refs。

## 7.4 StructureEvolutionAgent

输入：

```text
intent_ref
base_genome_ref
stagnation_report_ref
archive_summary_ref
constraint_set_ref
budget_remaining
```

输出只有类型化 GenomePatch。Agent 不能修改总预算、接受阈值、RenderContract、hard constraints、current_best 或 Search 结果。

默认一轮最多 1 个主 Patch 和 1 次结构化输出修复；一个 Run 最多 3 个 Patch round。

## 7.5 V4 外循环 Graph

```text
optimize_params
  → stagnation_detected
  → summarize_archive
  → propose_patch
  → validate_patch
  → apply_patch
  → validate_candidate
  → compile/render/oracle
  → optimize_params
  → select_best
  → decide_next
       ├─ continue_search / propose_patch
       ├─ promote_candidates
       │    → review_candidates
       │    → deterministic_tradeoff_selector
       ├─ offline_feedback_injection / awaiting_feedback
       │    → apply_feedback
       │    → increment_evaluation_revision
       │    → rescore_archive
       └─ promote_or_skip_memory_v4 → finalize
```

Patch 后候选不能直接晋升，必须重新执行 Compiler、Renderer、Oracle、局部参数搜索和 Selector。

State 只增加：

```text
stagnation_report_ref
archive_summary_ref
current_patch_ref
patch_round
constraint_set_ref
evaluation_revision
objective_best_id
preferred_candidate_id
final_selected_id
preference_set_ref
feedback_cursor_ref
```

每个候选保存 parent、patch、前后 Genome hash、changed nodes、Prompt/model/seed 和实际改善，形成可归因谱系。V4 继续显式保留 Memory 生命周期：只晋升已验证的约束、模板策略、Patch 成败模式和用户偏好，不写完整 Genome、GLSL 或逐像素数据。

## 7.6 CandidateArchive

首期使用 bounded top-k + novelty sampling，保存：

- current_best；
- 高分/Pareto 候选；
- 各 descriptor 代表；
- 高不确定性候选；
- 少量由固定 seed 选择的随机候选。

行为描述符版本化为主色相、edge softness、layer count、symmetry、instance count 和 topology complexity。

只有 benchmark 证明 Archive 坍缩且 top-k + novelty 无法保留有效多样性时，再引入 MAP-Elites。

## 7.7 VLM Pairwise

`PairwiseReviewRequest` 只包含 target/A/B Artifact refs 和固定 rubric。协议要求：

- A/B 左右使用记录的 seed 随机；
- 同时展示参考图、候选 A 和 B；
- rubric 固定覆盖轮廓、实例、颜色、光照、高光、阴影、边缘、材质和背景；
- 输出 `winner: a | b | tie`、confidence、dimension judgments、problem regions 和 reasons；
- 低置信结果可以交换位置复审；
- VLM 不得推翻 hard constraints；
- 只有客观 Score 位于版本化等价交换带内且保护区通过时，VLM 才能作为 tie-break。

Pairwise cache key 至少包含 target hash、A/B render hash、orientation、rubric、Prompt 和 model version。

## 7.8 HITL

结构化反馈类型：

```text
candidate_preference
region_lock
color_lock
issue_label
accept_current
increase_budget
approve_complexity
```

每条反馈带 `expected_run_revision` 和 idempotency key，拒绝过期操作。Constraint、Metric weight 或保护区变化时必须递增 `evaluation_revision`，旧 Score 不得与新 revision 直接比较。`FeedbackCompiler` 将反馈转换为：

- ConstraintSet；
- PreferenceSet；
- Oracle protected regions；
- Search frozen parameters；
- Strategy/complexity approval。

人工反馈必须绑定 run、candidate、Genome hash、metric version 和事件序号，不能只保存在聊天文本中。

V4 必须拆分三个选择指针：

- `objective_best_id`：同一 `evaluation_revision` 和 EvaluationEvidenceKey 内由确定性 Selector 单调更新；
- `preferred_candidate_id`：VLM/HITL 在 hard constraints 通过的 epsilon/Pareto 集中表达偏好；
- `final_selected_id`：最终交付候选，可以等于 objective best，也可以是有完整批准证据的 trade-off。

VLM/HITL 不回写或覆盖 `objective_best_id`。任何目标分数较差的最终选择都必须保存批准者、理由、旧/新指标、evaluation revision 和事件。

## 7.9 V4 实施增量

### V4.0：Patch Core

- Patch discriminated union；
- validate/apply/canonical hash；
- StagnationReport 和 ArchiveSummary；
- Patch property test 和 dry compile。

### V4.1：StructureEvolution 外循环

- StructureEvolution Prompt、Parser、bounded node；
- Graph patch 路由和预算；
- Patch 后重新走确定性闭环；
- 接入 NodeProvider、Node Lab 和模型 fixture benchmark。

### V4.2：Archive、Pairwise 与 HITL

- top-k + novelty Archive；
- Pairwise Request/Review/Cache；
- FeedbackRecord 和 FeedbackCompiler；
- 离线人工标签与 VLM/Oracle 相关性报告。

## 7.10 V4 验收门槛

- Patch schema/ref/DAG/contract/property tests 全部通过；
- 同 Patch 应用结果 hash 100% 可复现；
- StructureEvolutionAgent 不突破预算；
- 固定真实模型集 Patch 合法率 ≥ 70%；
- Patch 失败不损坏或覆盖 current_best；
- 同预算 AI-on 相对 AI-off 中位提升 ≥ 5%；
- 在 held-out 人工标签集上，cheap score 与独立人工/VLM 排序 Spearman ≥ 0.45，并报告样本数与置信区间；
- A/B 交换后按冻结 paired test 和最低样本数验证无显著位置偏差；
- 低置信 Pairwise 不自动晋升；
- region lock 后保护区退化不超过冻结阈值；
- 反馈可追溯到 run/candidate/revision/metric/prompt；
- 新真实模型 M5 和独立人工盲评均通过冻结门槛，才进入 V5 产品化发布路径。

---

# 8. V5：Async Run、Durable Execution 与产品化

## 8.1 目标与关键判断

V5 不能只给当前 `/generate` 包一层后台任务。必须同时解决：

- 恢复关键数据使用 `UntrackedValue`，进程崩溃后无法完整恢复；
- 过程事件主要在 Run 结束时批量落库，不能支撑真实进度流；
- `ProjectLockRegistry` 只在单进程有效；
- LocalArtifactStore 不能跨 Worker；
- 浏览器 AbortController 只停止等待，不取消服务端；
- Renderer 生命周期与 Graph Worker 强绑定；
- 当前终态事务还不是完整任务 outbox/reaper 模型。

V5 必须交付真正的异步任务控制面、持久执行面和产品 UI。

V5.0 必须冻结 DeploymentProfile：

- `local/dev`：允许 in-memory Checkpointer 和 LocalArtifactStore，但不得宣称 Durable DoD；
- `production_single_tenant`：本方案默认正式范围，缺少 PostgreSQL Ledger/Checkpointer、共享 ArtifactStore 或 Worker 时启动失败；
- `production_multi_tenant`：只有另行完成 Principal/AuthN/AuthZ、Project ownership、Worker service identity 和全接口授权后才能启用。

当前仓库没有登录身份体系，因此文档不把仅增加 `tenant_id` 字段描述成已实现租户隔离。`tenant_id` 在默认 single-tenant profile 中为可选保留字段。

## 8.2 Run API

```text
POST   /api/shader/runs
GET    /api/shader/runs/{run_id}
GET    /api/shader/runs/{run_id}/events
GET    /api/shader/runs/{run_id}/events/stream
POST   /api/shader/runs/{run_id}/cancel
POST   /api/shader/runs/{run_id}/feedback
POST   /api/shader/runs/{run_id}/resume
GET    /api/shader/runs/{run_id}/candidates
GET    /api/shader/runs/{run_id}/candidates/{candidate_id}
GET    /api/shader/runs/{run_id}/artifacts/{artifact_id}
DELETE /api/shader/projects/{project_id}/memory
```

`POST /runs` 使用 multipart，返回 `202 Accepted`，支持 `Idempotency-Key`。

Idempotency-Key 必须绑定 deployment principal/project、规范化请求 body hash 和 TTL；同 key 对应不同图片或请求参数返回 409。

生命周期：

```text
queued
  → running
  → awaiting_feedback / paused
  → running
  → succeeded | failed | cancelled
```

`cancel_requested_at` 固定为字段，不再同时设计成状态。终态不可覆盖。`paused/awaiting_feedback` 使用同 Run CAS 恢复；用户取消后若要继续，创建带 `parent_run_id` 和 `resume_from_checkpoint_id` 的新 Run；Worker 崩溃恢复保持原 run_id。

## 8.3 Backend 模块

```text
backend/app/
├── api/routes/shader_runs.py
├── schemas/shader_runs.py
├── services/run_commands.py
├── services/run_queries.py
├── database/run_repository.py
├── database/event_repository.py
├── database/feedback_repository.py
└── workers/run_worker.py
```

Route 只处理 HTTP 校验和 envelope；Run command/query、lease、cancel、resume、feedback 和 Artifact authorization 必须进入 Service/Repository。

## 8.4 数据库与 Ledger

新增 `backend/sql/002_async_runs.sql`，在现有账本上扩展：

```text
RunRecord
  id/project_id/tenant_id/parent_run_id
  status/phase/revision
  contract/preset/budget/usage/versions
  evaluation_revision
  objective_best_id/preferred_candidate_id/final_selected_id
  stop_reason
  committed_checkpoint_id/checkpoint_revision
  cancel_requested_at
  timestamps

RunJob
  job_id/run_id/status/attempt
  lease_owner/lease_epoch/lease_expires_at/heartbeat_at
  available_at/last_error

RunEvent
  run_id/seq/event_id/schema_version
  type/stage/payload/artifact_refs
  causation_id/occurred_at/recorded_at

CandidateIndex
  candidate_id/run_id/parent_id
  genome_hash/descriptor/created_by
  evaluation_refs/objective_summary
  artifact_manifest_ref

FeedbackRecord
  feedback_id/run_id/type/payload
  expected_revision/status/applied_event_seq

ArtifactBlob
  sha256/uri/size/content_type/state

ArtifactBinding
  artifact_id/run_id/candidate_id/kind/visibility/blob_sha

OperationAttempt
  operation_id/run_id/type/lease_epoch/status
  budget_reserved/cost_reserved/request_hash/result_ref

RendererJob
  renderer_job_id/run_id/candidate_id/status
  lease_epoch/environment_id/request_hash/result_ref
  cancel_requested_at/attempt/timestamps
```

建立 `run_jobs`、`operation_attempts`、`renderer_jobs` 和 `run_outbox`，不再同时维护重复的 `run_leases` 表或 RunRecord lease 字段。每次领取 Job 生成递增 `lease_epoch/fencing_token`；event、candidate、Artifact binding、objective/preferred/final 指针、预算和终态写入都必须携带 token，数据库拒绝 stale Worker 的旧 token。

`run_jobs` 建立单 Run 唯一活动 Job 约束；同 project 单活动 Run 使用部分唯一索引或等价数据库约束落实。目标不是承诺外部计算绝不短暂重叠，而是保证只有一个有效 fencing token 可以提交结果。

事件 seq 必须由数据库事务或 CAS 分配，不能由多个 Worker 独立累加。

终态 Run 更新、最终事件、已验证 Artifact binding 和 outbox 必须位于同一事务；冲突终态继续拒绝覆盖。对象存储上传和 LangGraph Checkpoint 不得被误写成可与 PostgreSQL 处于同一原子事务。

### 8.4.1 001 → 002 兼容迁移

V5.0 必须明确：

- 继续复用并扩展现有 `agent_runs/agent_events`，不建立第二套含义重叠的 Run Ledger；
- 先迁移 status constraint，再启用新 Worker 状态；
- 为历史记录回填 schema/version/default revision；
- 迁移脚本幂等，并提供升级前检查和失败回滚方案；
- 新 Artifact API 合并现有三个 legacy alias 与 opaque artifact id，不能注册同形但不可达的重复路由；
- Memory DELETE 复用现有路由，不重复注册；
- 旧 `/api/shader/generate` 在兼容期只继续执行 V1 同步路径，不等待新异步 Genome Run；新 Pipeline 只使用 `/runs`，一个版本后弃用旧入口。

## 8.5 Worker、Lease 与恢复

- API 进程不直接执行 Graph；
- 首版 Worker 使用 PostgreSQL `FOR UPDATE SKIP LOCKED` 领取 Job，并获得 `lease_epoch/fencing_token`；
- Worker 定期 heartbeat 并续租 lease；
- LangGraph 使用持久 Checkpointer，`thread_id = run_id`；
- 恢复必需的图片、GLSL、Candidate、Genome 和搜索状态改成 ArtifactRef；
- 执行语义为 `at-least-once + 幂等副作用`，不宣称 exactly-once；
- Reaper 扫描过期 lease，未取消且 attempt 未耗尽时重新排队；
- 已完成 Artifact 和 Score 通过 idempotency key/hash 复用，不重复污染预算；
- 多 Worker 项目互斥使用上述数据库 Job/Run 约束和 fencing，不保留“advisory lock 或 lease”二选一；
- 同一 project 首期只允许一个活动 Run；
- Memory promotion 与项目锁、Run revision 和 current_best hash 绑定。

LangGraph Checkpointer 与 Run Ledger 使用不同连接和事务，必须采用显式确认协议：

1. 先写入版本化 checkpoint；
2. 使用 run revision + fencing CAS 更新 Ledger 的 `committed_checkpoint_id/checkpoint_revision`；
3. 恢复时只读取 Ledger 已确认的 checkpoint；
4. 未被 Ledger 确认的 checkpoint 视为 orphan，由 Reconciler/GC 处理；
5. 终态事务绑定最后确认的 checkpoint id，但不宣称 checkpoint 与 Ledger 原子提交。

必须增加“checkpoint 成功/Ledger 失败”和“Ledger 前置状态成功/checkpoint 失败”两个方向的 failpoint 测试。

### 8.5.1 OperationAttempt 与保守预算

at-least-once 无法保证模型供应商收费或外部 Renderer 调用 exactly-once。新增 `OperationAttempt` 账本：

```text
operation_id/run_id/type
lease_epoch/status
budget_reserved/cost_reserved
provider_idempotency_key
request_hash/result_ref
started_at/finished_at
```

- 调用前 reserve 预算；
- Provider 支持时传 idempotency key；
- Worker 在外部调用完成但结果未提交时崩溃，该 attempt 标记为 `outcome_unknown`；
- outcome unknown 仍保守计入预算/费用，再由策略决定是否重试；
- Search 每个 accepted step/sweep 保存 cursor，并记录 evaluation reservation/result；
- 验收语义是“不重复晋升、不重复累计已知内部评估；外部未知调用保守计费”，不是成本 exactly-once。

## 8.6 CancellationToken

取消状态由数据库 cancel epoch/revision 驱动。以下位置必须检查：

- Node 开始和结束；
- 模型调用前后；
- 每次参数评估前后；
- Renderer Job 提交前后；
- Artifact 晋升前；
- current_best、Memory 和终态写入前。

无法真正中断的模型调用返回后必须再次检查 token，取消后的迟到结果不能晋升。Renderer Job 支持 kill/recycle。

取消使用 Saga：

1. DB CAS 写 `cancel_requested_at`；
2. Worker 停止产生新候选；
3. 内容寻址 Artifact 幂等写入 staging/verified；
4. 确认 checkpoint 并更新 Ledger 指针；
5. DB 事务写 Artifact binding、final event、terminal status 和 outbox。

Artifact/Checkpoint 固化部分失败时，取消不能无限卡住；Run 可以进入带明确 `durability_status=partial` 的 cancelled 终态，由 Reconciler 后补。若取消发生在首个合法候选前，API 明确返回 `no_candidate_available`，不能承诺 current_best 始终存在。

## 8.7 事件流与 SSE

- 事件在阶段发生时增量写库；
- 数据库 Ledger 是事实源；
- LISTEN/NOTIFY 只用于唤醒；
- SSE 支持 `Last-Event-ID`；
- 断线后从 `after_seq` 回放；
- 事件只带摘要和 ArtifactRef；
- 图片、GLSL、Genome 和 residual 按需下载；
- SSE 定期发送 heartbeat；
- 出现 seq gap 时先拉 Run snapshot；
- 未知事件 schema 由前端安全忽略并记录诊断。

建议事件：

```text
run.queued
run.started
analysis.completed
intent.created
seed.created
candidate.compiled
candidate.rendered
candidate.scored
current_best.updated
search.block_completed
agent.patch_proposed
review.completed
run.awaiting_feedback
run.cancelled
run.completed
run.failed
```

## 8.8 Artifact Store 与 Content Cache

统一接口：

```python
put()
get()
head()
presign()
```

实现 LocalArtifactStore 和 S3/兼容对象存储。数据模型拆分为：

```text
ArtifactBlob
  sha256/uri/size/content_type/state

ArtifactBinding
  artifact_id/run_id/candidate_id/kind/visibility/blob_sha
```

同一个内容 Blob 可以被多个 Run/Cache 引用，权限检查依据 Binding，不依据共享 Blob。上传采用 staging → SHA/HEAD 验证 → DB binding/terminal commit → orphan GC。终态事务只能引用已 verified Blob。

其他要求：

- 内容寻址；
- SHA-256 校验；
- 临时写入后原子提交；
- 数据库保存 Blob metadata 和运行归属 Binding；
- Worker 之间不依赖本地绝对路径；
- Artifact 权限绑定 tenant/project/run；
- 公开下载使用短期签名 URL 或后端鉴权流；
- private compile/model evidence 不进入公开 API。

Render/Score Cache 绑定 Genome、Target、Contract、Compiler、Renderer、Metric、Size 和 diagnostic pass。VLM cache 绑定 target/A/B hash、orientation、rubric、Prompt 和 model version。

## 8.9 Renderer Worker Pool

- V5.2 冻结首个 transport 为 PostgreSQL `renderer_jobs` + 独立 Renderer Worker 进程，不提前拆 HTTP 微服务；
- Chromium Worker 预热；
- 每个 Job 有 wall-time 和资源上限；
- context loss/浏览器崩溃后重建；
- 同一候选最多重放一次；
- Render Request 使用 candidate/contract/version/hash 幂等；
- Graph Worker 不直接管理浏览器进程；
- pool 暴露健康度、队列长度、启动次数、context loss 和 p50/p95 latency；
- 最终 WebGL 环境和 vendor/renderer 信息进入 Manifest。

每个 Run 固定 `renderer_environment_id`。环境发生变化后，challenger 与 incumbent 必须在新环境同时重渲，禁止跨环境直接比较。

## 8.10 前端

新增 `/runs/:runId`：

- 创建后立即导航；
- 浏览器关闭不影响服务端；
- snapshot 作为基线，SSE 事件由 reducer 增量应用；
- 断线自动重连和回放；
- 展示阶段、预算、事件和停止原因；
- 展示 Intent、Genome、Score、Residual 和 Candidate lineage；
- reference/current_best/A/B 对比；
- region/color lock；
- accept、feedback、cancel、resume；
- 下载 GLSL、PNG、Genome、HTML 和 Manifest；
- 客户端 WebGL 继续作为兼容性复核，不参与服务端自动闭环。

图片和残差按需加载，事件和状态接口不得嵌入 base64 大对象。

## 8.11 Nightly 与 Dashboard

nightly 记录：

- 质量：compile/static、结构门禁、objective、人工/VLM 相关性；
- 性能：queue wait、阶段 p50/p95、render 次数、搜索评估数；
- 缓存：render/score/VLM hit rate；
- 模型：调用次数、token、延迟、Parser/repair、费用；
- 运行时：取消、恢复、lease/reaper、Worker crash；
- 版本：Contract、Genome、Compiler、Renderer、Metric、Prompt、Code。

真实模型 nightly 继续要求显式预算和开关；普通 CI 只运行 AI-off、fixture 和假 Worker/Renderer 故障注入。

## 8.12 V5 实施增量

### V5.0：Control Plane 与 Ledger

- Run/Job/Event/Candidate/Artifact/Feedback schema；
- 202 Run API、查询和事件回放；
- idempotency key、revision 和终态 CAS；
- 冻结 DeploymentProfile、身份/授权范围和生产 fail-closed 启动检查；
- 更新 Backend package discovery、对象存储直接依赖和 wheel 安装 smoke；
- 旧 `/generate` 保留一版同步兼容适配器。

### V5.1：Worker、Cancel 与 Recovery

- API 与 Graph 执行解耦；
- lease、heartbeat、reaper；
- 恢复关键 UntrackedValue 改为 ArtifactRef；
- CancellationToken 和幂等副作用；
- crash/cancel/resume failpoint 测试。

### V5.2：SSE、Store 与 Renderer Pool

- 过程事件增量 Ledger；
- SSE Last-Event-ID 和断线回放；
- Local/Object ArtifactStore contract；
- Content Cache；
- Renderer Worker Pool 和健康指标。

### V5.3：UI、HITL 与运营门禁

- 新 Run 页面；
- candidate/residual/lineage/pairwise/feedback；
- cancel/resume/reconnect E2E；
- nightly benchmark 和质量/性能/成本 Dashboard；
- 弃用阻塞式产品路径。

## 8.13 V5 验收门槛

一致性和并发：

- 2 个以上 Worker 不并发执行同一 Run；
- 事件 seq 严格递增且重连无丢失；
- 只允许一个终态成功提交；预期 CAS loser 必须被识别并安全拒绝，不能覆盖 winner；
- 同 project 单活动 Run 约束跨进程有效；
- Artifact、Score 和预算不会因重试重复累计。

恢复：

- Worker 在分析、模型、搜索、Renderer 和终态前崩溃均可恢复；
- 已完成 Artifact 和 Score 可复用；
- lease 过期能由 Reaper 正确回收；
- cancelled Run 不在原 id 上恢复为 running；
- current_best 始终可下载和回放。

取消：

- queued、running、awaiting_feedback 和 paused 均可取消；
- 在冻结硬件、计时起止点和至少 20 个样本下，确定性循环取消延迟 p95 ≤ 2 秒；
- 在同一冻结条件下 Renderer 取消延迟 p95 ≤ 5 秒；
- 模型受供应商能力限制，但必须有硬 timeout，迟到结果不得晋升。

Store 和事件：

- Local/Object Store contract tests 一致；
- 100% 下载执行 SHA 校验；
- 跨 Worker 无本地绝对路径依赖；
- SSE Last-Event-ID、seq gap 和 snapshot 恢复通过；
- selected DeploymentProfile 下的 project/run 隔离和 Artifact 授权通过；multi-tenant profile 只有 AuthN/AuthZ 前置完成后才能进入该门禁。

E2E：

- 创建 → 断线 → 重连 → 完成；
- 创建 → 候选比较 → feedback → 恢复 → 完成；
- 创建 → cancel；
- Worker crash → Reaper → resume；
- Renderer context loss → 重建 → 单次重放；
- 浏览器关闭后 Run 继续；
- 非开发者可以完成上传、运行、反馈和下载。

发布：

- 自动质量门禁和独立人工门禁通过；
- nightly 能发现冻结的质量、性能和成本回退；
- 完整 Manifest 可复现最终 GLSL；
- 所有文档、API、Graph 可视化和功能状态同步。

---

## 9. 推荐功能状态拆分

遵守一次只处理一个 active 功能，建议按以下顺序登记：

| 顺序 | 对应现有/新增功能 | 版本 |
|---:|---|---|
| 1 | F09 M6.2 人类偏好对齐 | V1 收口 |
| 2 | F02 Intent IR | V2.0–V2.1 |
| 3 | F03 Genome/DSL/Compiler/Renderer | V2.1–V2.3 |
| 4 | F04 Oracle V2 | V3.0 |
| 5 | 将现有 F05 拆分后的 Deterministic Search 功能 | V3.1–V3.3 |
| 6 | 将现有 F05 的 VLM/HITL 部分迁出的新功能：StructureEvolution/Review/HITL | V4 |
| 7 | 新功能：Async Run/Productization | V5 |

V3/V4 实现前先修改 `docs/FEATURES.md`，把当前同时描述 Search 与 VLM/HITL 的 F05 拆成两个互不重叠的功能。每个功能进入 active 前完成规格、验证命令和目录边界；通过后再切换下一功能。不得为了“大版本”同时激活两个功能。

---

## 10. 测试矩阵

| 层级 | V2 | V3 | V4 | V5 |
|---|---|---|---|---|
| Schema/Unit | Intent、Genome、Compiler | Oracle、Manifest、Search | Patch、Archive、Feedback | Run、Event、Lease、Cancel |
| Property | DAG/hash/参数范围 | 扰动单调、round-trip | Patch apply 可复现 | 幂等、CAS、事件序列 |
| Integration | Genome→WebGL→Artifact | Search→WebGL→Selector | Patch→Search→Pairwise | API→Worker→Store→SSE |
| Browser | Compiler/Renderer parity | final/current_best 复核 | Pairwise/HITL 页面 | 断线/重连/取消/恢复 |
| AI-off | 模板 Seed | 完整优化闭环 | 规则升级/安全停止 | Nightly/故障恢复 |
| AI-on | Intent/SeedPlan fixture+real | 与 AI-off 同预算对照 | Patch/Pairwise real | 预算化端到端 |
| Human | 结构标签复核 | Top-k 质量复核 | 独立盲评 | 产品可用性验收 |

所有真实模型测试继续使用固定 manifest、显式开关、独立预算和只增不改证据。普通单元/Integration 不持有真实密钥。

### 10.1 量化门禁协议

- 20–50 张扩展数据必须拆分为模板/开发集与 held-out 发布集；不得用同一批结构标签既调模板又报告最终准确率；
- “保护区退化 ≤ 2%”默认定义为 `max(0, candidate_loss - incumbent_loss) / max(incumbent_loss, 0.01) ≤ 0.02`，同时报告绝对 delta；如某 Profile 使用不同定义，必须冻结在 EvaluationProfile；
- Spearman 门禁至少使用 30 个独立 pairwise 判断，报告 bootstrap 95% 置信区间；
- A/B 位置偏差至少使用 30 个镜像复审样本和冻结 paired test；
- AI-on 相对 AI-off 的 ≥ 5% 提升使用固定模型快照、相同总预算、配对样本和预先冻结的重复次数，并报告置信区间；
- p95 延迟必须记录硬件、浏览器/Renderer 环境、样本数和计时起止点；样本少于 20 时不报告 p95；
- 所有阈值、样本选择和统计方法在真实付费 Run 前冻结，运行后不得移动。

---

## 11. 发布与兼容策略

1. V2/V3 首先只在 Node Lab、CLI 和 benchmark 中启用；
2. V1 产品路径保持当前发布/no-go 语义，不与 V2 分数混合；
3. V2 不以画质超过 V1 为唯一门槛，而以结构合法、可编译和可追溯为门槛；
4. V3 自动和人工门禁通过后，Genome 路径才成为产品默认；
5. V4 Pairwise/HITL 先离线和 Node Lab，再进入产品；
6. V5 上线时，旧 `/api/shader/generate` 保留一个版本作为同步兼容适配器；
7. 新 UI 稳定后弃用阻塞式产品路径；
8. 历史 V1 Run 永久使用旧 schema 只读回放；
9. 不执行破坏性 Artifact 或数据库批量迁移，必要时使用 adapter 读取旧格式。

---

## 12. 主要风险与控制

| 风险 | 控制方案 |
|---|---|
| V2 直接修改 V1 schema，历史无法回放 | 新建版本化契约和独立 Graph |
| Genome 节点过多、无法定位错误 | v0 限制约 14 类，按基准证据扩展 |
| LLM 输出完整 Genome 经常 Parser 失败 | 输出 SeedPlan，确定性 Mapper 展开 |
| Compiler 与 cheap renderer 语义漂移 | 坐标、alpha、AA、blend parity gate |
| 自动指标再次奖励模糊/缩小主体 | topology、coverage、mask union、小层独立评分 |
| Search 维度过高 | stable manifest、分块、冻结低置信参数 |
| Cheap 与 WebGL 候选错排 | block winner 和 final 必须真实 WebGL 复核 |
| 每次评估进入 Graph 导致 checkpoint 爆炸 | Search Engine 内循环，Graph 只保存 cursor |
| Compiler 失败后重新让 LLM 修 GLSL | 只允许 Genome repair，Compiler defect fail-fast |
| VLM 位置偏差或噪声 | 随机 A/B、交换复审、低置信不晋升 |
| HITL 反馈应用到旧候选 | expected revision + idempotency key |
| 异步 API 只是表面任务化 | 先 ref 化恢复数据、增量事件、lease、Reaper |
| 多 Worker 重复执行 | DB lease/advisory lock + CAS + 幂等副作用 |
| 假取消 | 节点、搜索、模型、Renderer 全链检查 token |
| LocalArtifactStore 跨机失效 | ArtifactStore 抽象 + 对象存储 + 内容 hash |
| 声称 exactly-once 不符合真实语义 | 明确 at-least-once + idempotence |

---

## 13. 最终 Definition of Done

V2–V5 全部完成后，系统必须满足：

- PNG 可转换为区分事实、推断、不确定和约束的 Intent IR；
- Intent 可生成多个可解释、可校验的 Effect Genome；
- Genome 可被确定性编译为 WebGL1 无贴图 GLSL；
- 每个参数拥有稳定 path、范围、问题域和 affected regions；
- Oracle 分离全局、拓扑、实例、形状、颜色、边缘、coverage 和局部语义层；
- 固定拓扑参数搜索可复现并稳定改善 Seed；
- AI-off 能独立输出 Genome、GLSL、PNG、Score 和 Manifest；
- Agent 只在阶段边界提出受限 GenomePatch；
- VLM/HITL 只能在硬约束之后参与晋级和偏好选择；
- `objective_best_id` 在同一 evaluation revision 内不发生单调退化；
- `final_selected_id` 若不是 objective best，必须属于 hard constraints 通过的 epsilon/Pareto 集，并绑定 VLM/HITL 明确批准证据；
- 每个候选和最终结果都具有完整版本、hash 和谱系；
- Run 可异步执行、取消、暂停、恢复和回放；已有合法候选时 objective best 始终可下载，首个候选前取消则返回明确 no-candidate 状态；
- Worker/Renderer 崩溃不会丢失已完成证据；
- 浏览器关闭不影响服务端任务；
- 自动、人工、性能、成本和恢复门禁全部通过；
- 非开发者可完成上传、观察、反馈、接受和下载；
- 历史 V1 产物保持可读且不被新版本错误重解释。

最终系统定义为：

> 一个以 Intent IR 和 Effect Genome 为可解释、可搜索中间表示，以真实 WebGL Renderer 和结构感知局部 Oracle 为事实反馈，以确定性参数搜索为内循环，以 Agent GenomePatch 和 VLM/HITL 为外循环，并通过 current_best、硬预算、持久任务和完整证据链保证质量不退化、结果可复现和运行可恢复的程序化视觉拟合系统。
