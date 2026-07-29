# F09 Refine 闭环与 Uniform 参数优化方案

状态：`implemented`

适用范围：当前唯一 active 功能 F09 — Layered Direct GLSL。

本文是在现有链路审查基础上修订后的实施方案，分两阶段完成：

1. 修复 Refine 最小闭环；
2. 接入基于 `tunable_manifest` 的 uniform-only 参数搜索。

本文只定义实现方案，不启动 benchmark、A/B、shadow、promotion 或上线治理。

实施结果（2026-07-29）：

- Refine 已接入双 target 早停、重复 Patch 检测、失败反馈、
  `min_delta_mae` / `min_delta_loss` / `patience` 收敛与 WebGL 左下原点
  residual；
- 已接入基于 `tunable_manifest` 的单层、单轮、uniform-only 确定性搜索；
- 每个候选仍经过 compile / validate / prepare / draw / receipt /
  attestation / evaluate / select 完整边界；
- uniform 搜索 provenance 与私有 trace 已落盘，公开结果仅暴露安全汇总；
- 聚焦测试、前端测试与构建、`docs-check`、`langgraph validate`、
  `check-wheel` 均已通过。

后续 selection、失败恢复和真实 WebGL 多参数链路的加固语义，以
`docs/REFINE_SELECTION_WEBGL_HARDENING_PLAN.md` 为准；其中 target-relative
MAE/loss excess dominance 已取代本文早期的 strict lower-loss selection。

## 1. 当前问题

当前 Attempt 主链路为：

```text
LayerPlan
→ Layered Initial
→ compile / validate / prepare / draw
→ receipt / attestation
→ evaluate / select
→ bounded single-layer Refine
→ finalize
```

现有安全、哈希、真实 WebGL1 执行和严格 lower-loss incumbent 边界可以保留，
但优化闭环存在以下缺口：

- `quality_preset` 的 target 只在 Backend 组装响应时计算，Graph 不知道 target，
  Initial 已达标仍会 Refine；
- Refine 失败或变差后，下一轮仍收到同一 incumbent、render、metrics 和
  residual，默认 `temperature=0` 时容易重复同一 Patch；
- `min_delta` 和 patience 不存在，微小改善或连续无改善都只能靠固定
  `refine_budget` 收敛；
- residual 的 `row=0` 来自图片顶部，而 LayerPlan/GLSL 使用 WebGL 左下原点；
- `tunable_manifest` 当前只被解析、校验和编译透传，没有真正驱动数值优化；
- 现有 Refine 会重写整层 GLSL、schema、values 和 manifest，成本高于
  uniform-only draw。

## 2. Review 后冻结的关键决策

### 2.1 先做数值搜索，再做结构重写

Uniform optimizer 不放在全部 LLM Refine 结束之后。最终顺序应是：

```text
Initial / accepted structural Refine
→ target check
→ uniform-only search for this source
→ local optimum / budget exhausted
→ structural Refine
→ new source accepted
→ uniform-only search for the new source
```

这样符合“便宜参数搜索优先，结构重写兜底”的成本顺序。

### 2.2 `patience` 使用“恢复尝试次数”语义

默认：

```text
min_delta_loss = 0.001
patience = 1
```

`patience=1` 的确切含义是：

```text
第一次无有效改善：允许 1 次携带反馈的恢复尝试
第二次连续无有效改善：停止
```

实现条件为：

```python
consecutive_non_improving > patience
```

不得把它实现成第一次无改善立即停止。

### 2.3 target 必须同时满足 MAE 与 objective loss

当前 API 同时公开 `target_mae` 和 `target_loss`。修订后统一定义：

```python
target_reached = (
    current_best.mae <= policy.target_mae
    and current_best.loss <= policy.target_loss
)
```

`target_reached` 优先级高于 `min_delta`、patience 和剩余 Refine 次数。即使本轮
改善小于 `min_delta_loss`，只要达到两个 target，也立即停止。

### 2.4 incumbent 与“有效进展”分离

继续保留严格 lower-loss incumbent 边界：

```python
candidate.loss < current_best.loss
```

同时单独计算：

```python
material_improvement = loss_delta >= min_delta_loss
```

因此：

- 任意严格 lower-loss candidate 都可成为新 incumbent；
- 只有 material improvement 才重置 patience；
- 微小改善不会丢失，但也不会无限延长优化。

### 2.5 确定性优化不得伪装成模型 Refine

不得构造假的：

```text
AuthorIdentity(role="refine", model_ref="optimizer")
```

模型 `AuthorIdentity` 继续表示结构和源码的模型来源；uniform-only 派生使用
独立、受信的 `UniformOptimizationProvenanceV1`，并进入 Layered/Program
canonical hash。

### 2.6 搜索仍走完整可信执行链

Prepared WebGL program 可以复用，但每个不同 uniform binding 都必须：

```text
derive new Spec
→ validate
→ render_uniforms
→ new ExecutionReceipt
→ new attestation
→ evaluate
→ select
```

不得复用旧像素、receipt 或 attestation。

## 3. 全局不变量

两阶段实现都必须保持：

1. `current_best` 只能由 `select_candidate` 更新；
2. `current_best.loss` 单调不增；
3. 失败的 Refine/optimizer candidate 不得使一个已有成功 incumbent 的
   Attempt 失败；
4. 全部模型文本、GLSL、uniform values、图片和 private diagnostics 不进入
   public progress；
5. 每个预算都有 attempt scope，局部预算不得绕过全局 LLM/compile/draw 上限；
6. 模型不能提供 hash、attestation、provenance 或 optimizer 控制字段；
7. Graph state 不保存 browser/page/program 等进程对象；
8. 所有浮点指标、delta、target 和参数值必须 finite；
9. 新增节点、边、路由、`current_best` 边界或终止路径时，同步 Graph ASCII
   文档并运行：

```bash
make docs-check
uv run langgraph validate
```

## 4. 阶段一：Refine 最小闭环

### 4.1 范围

阶段一实现：

- target 早停；
- Refine Patch 重复检测；
- 上一轮失败/退化反馈；
- `min_delta_loss`；
- patience；
- residual WebGL UV 坐标；
- 稳定 stop reason 和 policy fingerprint。

阶段一不实现：

- uniform-only optimizer；
- 新 Graph 节点；
- objective 权重调整；
- 多成功 parent attempt 的质量选择；
- 前端断线结果恢复。

### 4.2 逐运行策略契约

在 `agent.app.contracts.layerplan_glsl_direct` 新增：

```python
@dataclass(frozen=True)
class DirectOptimizationPolicy:
    schema_version: str = "direct_optimization_policy_v1"
    quality_preset: Literal["fast", "balanced", "high", "manual"] = "balanced"
    target_mae: float = 0.06
    target_loss: float = 0.08
    min_delta_loss: float = 0.001
    refinement_patience: int = 1
    detect_duplicate_patch: bool = True
```

质量目标沿用当前 Backend 数值：

| preset | target_mae | target_loss |
|---|---:|---:|
| fast | 0.08 | 0.10 |
| balanced | 0.06 | 0.08 |
| high | 0.04 | 0.06 |
| manual | 0.03 | 0.05 |

约束：

- target、`min_delta_loss` 必须 finite 且位于 `[0, 1]`；
- `refinement_patience` 必须是非负整数；
- policy 提供 canonical `to_dict()` 和稳定 fingerprint。

Policy 是逐请求数据，不进入当前进程级静态
`LayerPlanGlslDirectConfig`。产品入口只接收 `quality_preset`，由 Agent
contract 的单一映射函数生成 policy，Backend 不再维护第二份 target 表。

从阶段一开始使用 `DirectOptimizationPolicy` 而不是
`DirectRefinementPolicy`，避免阶段二接入 uniform optimizer 时再次迁移
逐运行 target/min-delta 契约。阶段一只消费其中的 Refine 字段，阶段二复用
同一个 target 和 material-improvement 语义。

调用链调整为：

```text
ParentRunRequest.quality_preset
→ DirectEngineAttemptExecutor
→ OwnedLayerPlanGlslDirectRunner.run(..., quality_preset=...)
→ DirectOptimizationPolicy
→ DirectGraphContext.optimization_policy
```

Studio adapter 接收可选 `quality_preset`，默认 `balanced`，不接收任意 raw
target。

### 4.3 Refine 反馈契约

新增 graph-independent、JSON-safe 的：

```python
@dataclass(frozen=True)
class RefineFeedback:
    schema_version: str
    outcome: Literal[
        "minor_improvement",
        "not_improved",
        "author_failed",
        "patch_invalid",
        "static_failed",
        "compile_failed",
        "draw_failed",
        "receipt_failed",
        "attestation_failed",
    ]
    target_layer_id: str | None
    candidate_loss: float | None
    candidate_mae: float | None
    loss_delta: float | None
    mae_delta: float | None
    metric_deltas: Mapping[str, float]
    failure_codes: tuple[str, ...]
```

允许进入下一轮 Prompt 的字段只有：

- stable failure/violation code；
- target layer ID；
- loss/MAE delta；
- 固定白名单 metric delta；
- 本轮 outcome。

禁止进入反馈：

- provider exception 文本；
- compile/link log 原文；
- GLSL；
- reference/current render bytes；
- prompt 或模型原始输出；
- private path；
- receipt/attestation 内容。

Metric delta 白名单：

- `global_mae`
- `foreground_mae`
- `background_mae`
- `geometry_mask_loss`
- `edge_loss`
- `worst_tile_mae`

### 4.4 State 变更

`LayerPlanGlslDirectState` 新增：

```python
optimization_policy: DirectOptimizationPolicy
consecutive_non_improving: int
previous_refine_feedback: RefineFeedback | None
attempted_patch_fingerprints: tuple[str, ...]
duplicate_patch_detected: bool
refinement_stop_reason: str | None
candidate_selected: bool
candidate_loss_delta: float | None
candidate_mae_delta: float | None
candidate_material_improvement: bool
```

`prepare_reference` 初始化：

```python
consecutive_non_improving = 0
previous_refine_feedback = None
attempted_patch_fingerprints = ()
duplicate_patch_detected = False
refinement_stop_reason = None
```

### 4.5 target 与停止优先级

`decide_refinement` 按以下固定优先级决策：

```text
1. no current_best                    → no_valid_candidate
2. MAE 与 loss 同时达标              → target_reached
3. terminal resource/runtime block    → hard_resource_block
4. duplicate patch                    → duplicate_patch
5. refinement_count 达上限            → refine_budget_exhausted
6. non-improving streak > patience    → patience_exhausted
7. LLM/draw 剩余预算不足              → hard_resource_block
8. 其他                               → author_refinement
```

伪代码：

```python
if current_best is None:
    stop("no_valid_candidate")
elif target_reached(current_best, policy):
    stop("target_reached")
elif refinement_blocked:
    stop("hard_resource_block")
elif duplicate_patch_detected:
    stop("duplicate_patch")
elif refinement_count >= config.refine_budget:
    stop("refine_budget_exhausted")
elif consecutive_non_improving > policy.refinement_patience:
    stop("patience_exhausted")
elif llm_remaining <= 0 or draw_remaining <= 0:
    stop("hard_resource_block")
else:
    continue_to("author_refinement")
```

Draw budget 在调用 Refine LLM 前预检，因为一个可执行 Refine 至少需要一次
新 draw。Compile budget 不提前硬拦：相同 source/schema 仍可能命中 prepared
cache。

### 4.6 重复检测

在 `apply_refinement` 真正 apply/compile 前计算 Patch 语义 fingerprint：

```python
sha256(canonical_json({
    "base_layered_spec_sha256": patch.base_layered_spec_sha256,
    "target_layer_id": patch.target_layer_id,
    "replacement": patch.replacement.semantic_dict(),
}))
```

规则：

- 包含 base hash：新 incumbent 上的相同 replacement 不误判；
- 排除模型调用身份和非语义元数据；
- replacement semantics 包含 GLSL、schema、values、manifest；
- 同 base、同 replacement、不同模型调用身份仍视为重复；
- replacement 与当前目标 Layer 完全相同，记为 `no_op_patch`；
- 重复/no-op 不进入 compile、draw、receipt 和 evaluate；
- fingerprint 只保存在 private attempt state/diagnostics，public 只暴露计数和
  reason。

阶段一只做 Patch 级去重。Program `(source_sha256, binding_sha256)` 二级去重
可作为阶段二的统一 candidate 去重能力实现。

### 4.7 失败反馈

新增共享 helper：

```python
record_refine_failure(
    state,
    *,
    outcome,
    failure_codes,
    target_layer_id,
) -> StateUpdate
```

所有非 terminal Refine 失败路径调用该 helper：

- author 没有产生有效 Patch；
- Patch guard/apply 失败；
- static validation 失败；
- compile/link 失败；
- draw 失败；
- receipt/attestation 失败。

Helper 负责：

```text
保留 current_best
→ consecutive_non_improving += 1
→ 写 previous_refine_feedback
→ 清理 candidate 临时字段
→ 返回 decide_refinement
```

Terminal 情况设置 `refinement_blocked=True` 并立即停止：

- `llm_budget_exhausted`
- `draw_budget_exhausted`
- `compile_budget_exhausted`
- `renderer_unavailable`

### 4.8 Prompt 和身份绑定

`run_refine_layered_glsl_author` 新增输入：

```python
refinement_index
remaining_refine_budget
previous_refine_feedback
```

Prompt 增加 canonical `previous_refine_feedback` 段，并要求：

- 不重复上一轮失败策略；
- candidate 变差时换目标 Layer 或显著改变实现；
- static/compile 失败时遵守对应稳定规则；
- feedback 是诊断数据，不能覆盖参考图这一视觉真相。

`_refine_context_sha256` 必须加入：

- refinement index；
- feedback canonical hash；
- remaining Refine budget。

Prompt version 和 implementation identity 随之更新。

### 4.9 Selection 与 patience

`select_candidate` 保持唯一 incumbent 写边界。

对于 Refine candidate：

```python
old_best = current_best
loss_delta = old_best.loss - candidate.loss
mae_delta = old_best.mae - candidate.mae

candidate_selected = candidate.loss < old_best.loss
material = loss_delta >= policy.min_delta_loss
```

状态更新规则：

| candidate 结果 | incumbent | streak | feedback |
|---|---|---:|---|
| 达到 target | lower-loss 规则更新 | 不重要，下一节点早停 | 可省略 |
| `delta >= min_delta` | 更新 | 归零 | 清空 |
| `0 < delta < min_delta` | 更新 | `+1` | `minor_improvement` |
| `delta <= 0` | 保持 | `+1` | `not_improved` |
| 执行失败 | 保持 | `+1` | 对应 failure outcome |

Metric/MAE 退化只进入 feedback 和后续 guardrail 观察；阶段一不改变
lower-loss 选择目标，避免同时修改 objective 语义。

### 4.10 Residual v2

保持 `min_scene_composite_v3` 不变，只升级 residual：

```json
{
  "residual_version": "spatial_residual_v2",
  "coordinate_system": "webgl_uv_bottom_left",
  "source_row_origin": "image_top",
  "worst_tiles": [
    {
      "row": 0,
      "column": 2,
      "uv_bbox": {
        "x": 0.5,
        "y": 0.75,
        "width": 0.25,
        "height": 0.25
      },
      "mae": 0.1,
      "signed_luminance_bias": -0.02,
      "signed_rgb_bias": [-0.01, -0.03, -0.02]
    }
  ]
}
```

`row/column` 暂时保留作为兼容诊断字段，但 Refine 和后续 optimizer 只消费
`uv_bbox`。

非整除尺寸按真实 pixel edge 计算：

```python
x0 = column_indices[0] / width
x1 = (column_indices[-1] + 1) / width
y0 = 1.0 - (row_indices[-1] + 1) / height
y1 = 1.0 - row_indices[0] / height
```

不得假设每个 tile 永远正好是 `0.25 × 0.25`。

### 4.11 Result 和 progress

`DirectAttemptResult` 与 safe summary 新增：

```text
optimization_policy_fingerprint
refinement_stop_reason
non_improving_count
duplicate_patch_count
```

稳定 stop reason：

- `target_reached`
- `refine_budget_exhausted`
- `patience_exhausted`
- `duplicate_patch`
- `hard_resource_block`
- `no_valid_candidate`

Phase 1 不改变 Graph 节点目录。`decide_refinement` 的 completed progress 可选
增加安全 decision：

```json
{
  "next_action": "author_refinement | release_resources",
  "reason_code": "target_reached | patience_exhausted | ..."
}
```

不得把整个 Graph state/events 直接投影到 public progress。

### 4.12 阶段一影响文件

核心：

- `src/agent/app/contracts/layerplan_glsl_direct.py`
- `src/agent/app/states/layerplan_glsl_direct.py`
- `src/agent/app/nodes/layered_direct/lifecycle_nodes.py`
- `src/agent/app/nodes/layered_direct/workflow_author_nodes.py`
- `src/agent/app/nodes/layered_direct/candidate_nodes.py`
- `src/agent/app/nodes/layered_direct/workflow_support.py`
- `src/agent/app/nodes/layered_direct/authors.py`
- `src/agent/app/prompts/direct_layered_refine_v1.yaml`
- `src/shaderforge/evaluation/mae.py`
- `src/agent/app/services/layerplan_glsl_direct.py`
- `backend/app/services/engine_rollout_runtime.py`

文档：

- `src/agent/app/graphs/ARCHITECTURE.md`
- `src/agent/app/nodes/layered_direct/ARCHITECTURE.md`
- `src/shaderforge/evaluation/ARCHITECTURE.md`
- 受影响的最近 Backend/Frontend README（仅在公开契约变化时）

### 4.13 阶段一验收测试

Policy：

- 四个 preset 映射正确；
- 非 finite/负 target、负 `min_delta`、非法 patience fail closed；
- policy fingerprint 稳定；
- Backend 与 Studio 使用同一映射。

Routing：

- Initial 同时达到两个 target：0 次 Refine；
- 只满足一个 target：继续 Refine；
- Refine 达标：立即停止；
- `refine_budget=0`：Initial-only；
- LLM/draw hard budget 不足：author 前停止；
- renderer unavailable：不再调用 LLM。

Duplicate：

- 同 base + 同 replacement 第二次不 compile/draw；
- 同 base、只改 uniform value不误判；
- 新 base 上相同 replacement 不误判；
- no-op replacement 不 draw；
- duplicate count 和 stop reason 正确。

Feedback/patience：

- worse candidate 的 layer、loss delta、metric delta进入下一轮模型消息；
- static violation 只传稳定 code/line；
- compile log/provider 文本不进入 Prompt；
- material improvement 清零 streak；
- minor improvement 更新 incumbent但增加 streak；
- 第一次无有效改善后允许一次恢复；
- 第二次连续无有效改善触发 `patience_exhausted`。

Residual：

- 图片顶行映射到高 `uv.y`；
- 图片底行映射到低 `uv.y`；
- 非 4 整除宽高得到精确 bbox；
- row/column 兼容字段不变；
- Prompt 消费 `coordinate_system` 和 `uv_bbox`。

代表性集成路径：

```text
Initial not target
→ Refine worse
→ feedback-aware Refine improves and reaches target
→ release/finalize
```

断言 current_best 单调、调用/预算正确、public summary 无私密内容。

## 5. 阶段二：`tunable_manifest` Uniform-only 参数搜索

### 5.1 范围

阶段二新增一个确定性、有界、attempt-local 的参数优化器。

MVP 支持：

- 一个候选只修改一个 tunable component；
- bounded coordinate pattern search；
- prepared program 复用；
- 全部候选真实 draw/evaluate；
- strict lower-loss incumbent；
- 每个 source 一次参数优化 session；
- optimizer provenance、ledger、stop reason；
- optimizer 完成后进入结构 Refine。

MVP 不支持：

- CMA-ES、Bayesian optimization、遗传算法；
- 并行 browser evaluation；
- 跨 run warm start；
- 基于历史 benchmark 的参数先验；
- 模型自报 priority/group；
- 多参数联合 move；
- objective 权重自动学习。

### 5.2 Graph 位置和顺序

阶段二完成后的主循环：

```text
select initial/refine candidate
  → decide_uniform_optimization
      ├─ target reached / hard stop → release_resources
      ├─ source eligible
      │    → propose_uniform_candidate
      │    → apply_uniform_candidate
      │    → compile_candidate
      │    → validate_candidate
      │    → prepare_program
      │    → render_program
      │    → verify_receipt
      │    → attest_candidate
      │    → evaluate_candidate
      │    → select_candidate
      │    → record_uniform_outcome
      │    → decide_uniform_optimization
      └─ local optimum / source already searched / tuning budget exhausted
           → decide_refinement
                ├─ structural Refine accepted
                │    → decide_uniform_optimization for the new source
                └─ stop → release_resources
```

规则：

- target check 永远在 optimizer/LLM 调用前；
- 同一 `source_sha256` 默认只开启一次 optimizer session；
- structural Refine 被接受、产生新 source 后，可开启新 session；
- global `uniform_tuning_draw_budget` 仍是整个 Attempt 的硬上限；
- structural Refine 被拒绝时，不能对同一 source 重跑已结束的 optimizer。

新增显式节点：

- `decide_uniform_optimization`
- `propose_uniform_candidate`
- `apply_uniform_candidate`
- `record_uniform_outcome`

新增节点必须加入 `DIRECT_GRAPH_NODE_NAMES`、状态 route literal、Backend
progress 白名单、Frontend timeline 和 Graph 文档。

### 5.3 ShaderForge 领域边界

新增：

```text
src/shaderforge/uniform_optimization/
  models.py
  flattening.py
  search.py
  patching.py
  hashing.py
  ARCHITECTURE.md
```

该包只做确定性能力：

- 从 Layered/Program Spec 构建 tunable 维度；
- 生成下一次 move；
- 应用 trusted uniform patch；
- 校验范围和不变量；
- 重算 Layer/Layered/Program hashes；
- 记录确定性 provenance/search state。

该包不得：

- 调用模型；
- 调用 Renderer；
- 计算图像 objective；
- 选择 Graph 路由；
- 写 Artifact。

### 5.4 参数扁平化

新增：

```python
@dataclass(frozen=True)
class FlatTunableComponent:
    layer_id: str
    path: str
    component_index: int
    minimum: Decimal
    maximum: Decimal
    step: Decimal
    base_value: Decimal
```

稳定顺序：

```text
Layered Spec canonical layer order
→ tunable path 字典序
→ component index
```

类型维度：

- `float` → 1
- `vec2` → 2
- `vec3` → 3
- `vec4` → 4

剔除：

- `minimum == maximum`；
- `+step` 与 `-step` clamp 后都等于当前值；
- 不在目标 Layer；
- 不在 `tunable_manifest`；
- manifest/schema/type 不一致。

任何非-manifest path、越界值、修改 schema/body/manifest 的 patch 必须
fail closed。

### 5.5 目标 Layer 和 active components

MVP 先选择一个 Layer：

1. 使用 residual v2 worst `uv_bbox`；
2. 与 LayerPlan `region` 计算交集，并按 tile coverage、Layer confidence 和
   region area 形成稳定 advisory score；
3. `background` 只在 dominant metric 为 `background_mae`，或没有可行的
   非 background Layer 时参与；
4. `geometry_mask_loss`/`edge_loss` 优先考虑 `subject`、`detail`、
   `highlight`、`shadow`；
5. 其他 metric 先按 advisory score，再按 canonical LayerPlan 顺序消歧；
6. 跳过没有可行 tunable component 的 Layer；
7. 全部无 tunable 时 `no_tunables`。

不能只按 bbox overlap 排序：background region 通常覆盖全画布，会在每个
worst tile 上得到最大 overlap，从而吞掉所有局部优化机会。上述 role 规则只
用于分配搜索预算，不影响真实 objective acceptance。

LayerPlan 只作为搜索预算分配 advisory，不参与 acceptance 和 objective。

配置：

```text
uniform_tuning_active_component_cap = 8
```

若目标 Layer 的组件数超过 cap：

- 不静默跳过整个 optimizer；
- 使用 `sha256(base_spec_sha256 + optimizer_version)` 生成稳定 permutation；
- 取前 `cap` 个作为本 source session 的 active set；
- provenance 记录 active set hash 和 `dimension_cap_reached=true`；
- 不公开 raw path/value。

这只是 MVP 的确定性预算策略，不宣称代表参数重要性。未来若给 manifest 增加
priority/group，必须另行更新严格 schema 和 implementation identity。

### 5.6 数值量化

禁止累计二进制：

```python
value = value + float_step
```

使用从 canonical number string 构造的 `Decimal`，以 session base value 为
锚点生成 integer ticks：

```text
candidate = clamp(base_value + tick * step, minimum, maximum)
```

每个 candidate：

- 只替换一个 component；
- 保持 scalar/tuple 表达；
- 其他 uniform values 字节语义不变；
- 输出 canonical finite float；
- provenance 记录 tick、direction 和 component identity hash。

### 5.7 搜索算法

MVP：deterministic bounded coordinate pattern search。

对 active component 按稳定 permutation：

```text
probe +step
  ├─ material improvement → accept，进入下一个 component
  └─ no material improvement → probe -step
        ├─ material improvement → accept，进入下一个 component
        └─ no improvement → 进入下一个 component
```

Candidate selection 仍由 `select_candidate` 的严格 lower-loss 规则决定。

Optimizer session stop reason：

- `target_reached`
- `no_tunables`
- `no_feasible_components`
- `local_optimum`
- `uniform_tuning_budget_exhausted`
- `global_compile_budget_exhausted`
- `global_draw_budget_exhausted`
- `dimension_cap_reached_local_optimum`
- `renderer_unavailable`

全 pass 没有 material improvement 即 local optimum。微小 lower-loss candidate
仍可成为 incumbent，但不算 material improvement，不会无限重启 pass。

### 5.8 预算

`LayerPlanGlslDirectConfig` 新增：

```python
uniform_tuning_draw_budget: int = 4
uniform_tuning_active_component_cap: int = 8
uniform_tuning_max_passes: int = 1
```

预算关系：

```text
uniform_tuning_draw_count <= uniform_tuning_draw_budget
direct_ledger.draw_count <= draw_budget
```

默认仍由总 `draw_budget=8` 封顶。第一版不自动提高总 draw budget，避免接入
optimizer 后运行成本隐式增长。

预算优先级：

```text
target reached
→ global hard budget
→ uniform local budget
→ local optimum
→ structural Refine eligibility
```

若产品后续希望 High/Manual 有更多 draw，应单独修改 preset policy，不在 MVP
中暗改。

### 5.9 Trusted Uniform Patch

新增 trusted-only：

```python
@dataclass(frozen=True)
class UniformPatchV1:
    base_layered_spec_sha256: str
    base_program_spec_sha256: str
    target_layer_id: str
    path: str
    component_index: int
    expected_value: Decimal
    replacement_value: Decimal
    derivation: UniformOptimizationProvenanceV1
```

`apply_uniform_patch` 必须验证：

- base Layer/Layered/Program hash 完整；
- path 属于 target Layer 的 tunable manifest；
- type/component index 正确；
- expected value 与当前 binding 相同；
- replacement 落在 tunable 子域；
- 只改变一个 component；
- Layer identity、GLSL、schema、manifest、canvas、z-index、role、顺序不变；
- 全局 uniform 仍唯一；
- 受影响 layer hash、Layered hash、binding hash、Program spec hash全部重算；
- source hash 保持不变。

该 Patch 不进入任何模型 JSON schema。

### 5.10 Provenance 与 hash

新增 trusted-only：

```python
@dataclass(frozen=True)
class UniformOptimizationProvenanceV1:
    schema_version: str
    parent_layered_spec_sha256: str
    parent_program_spec_sha256: str
    algorithm_id: str
    algorithm_version: str
    optimizer_config_fingerprint: str
    active_components_sha256: str
    component_identity_sha256: str
    move_ordinal: int
    tick: int
    direction: Literal[-1, 1]
```

实现方式：

- `LayeredShaderSpecV1` 和 `ShaderProgramSpecV1` 增加可选、trusted-only
  `derivation_provenance`；
- 模型 parser 继续拒绝该字段；
- Initial/LLM Refine candidate 为 `None`；
- optimized candidate 必须非空；
- provenance 进入 Layered/Program canonical hash；
- 为保持未优化旧 Spec hash，canonical hash 只在 provenance 非空时加入该
  字段；
- compiler 确定性地把 Layered provenance 传入 Program Spec；
- 原模型 `AuthorIdentity` 原样保留，不伪装 optimizer 身份；
- implementation identity、result/manifest schema version随之升级。

完整 search summary 不进入每个 candidate Spec，避免最终 trace 尚未完成造成
循环依赖。Attempt 结束后另行生成：

```python
UniformOptimizationSummaryV1(
    base_spec_sha256,
    selected_spec_sha256,
    algorithm_version,
    config_fingerprint,
    active_component_count,
    evaluated_count,
    accepted_count,
    loss_delta,
    stop_reason,
    private_trace_sha256,
)
```

Parent/private manifest 用该 summary 绑定最终 Spec 与完整 private trace。

### 5.11 Candidate、State 和 Ledger

扩展：

```python
DirectCandidate.role:
    "initial" | "refine" | "uniform_optimize"
```

State 新增：

```text
uniform_search_plan
uniform_search_cursor
uniform_search_base_source_sha256
uniform_active_components
uniform_candidate_patch
uniform_optimized_source_sha256s
uniform_tuning_stop_reason
```

以上均为 JSON-safe value object，不包含 Renderer/program handle。

Ledger 新增：

```text
uniform_tuning_draw_count
uniform_tuning_evaluated_count
uniform_tuning_accepted_count
uniform_tuning_duplicate_count
uniform_tuning_session_count
uniform_tuning_active_component_count
uniform_tuning_stop_reason
```

候选失败路由必须按 role 区分：

- `initial` 失败：Attempt 无 incumbent 时终止；
- `refine` 失败：保留 incumbent，进入 Refine feedback/patience；
- `uniform_optimize` 失败：保留 incumbent，结束当前 optimizer session，
  再由策略决定结构 Refine 或 finalize；
- hard renderer/global budget failure：立即停止后续优化，但保留已有 incumbent。

### 5.12 Prepared program、receipt 与 attestation

现有 cache key：

```text
source_sha256
+ uniform schema signature
+ canvas
+ renderer contract
```

不含 values，适合 uniform-only candidate。

验收要求：

- base source 首次 prepare；
- uniform candidate 命中 prepared cache；
- `compile_count` 不因纯 values 变化增长；
- `draw_count` 和 `uniform_tuning_draw_count` 每次真实 draw 增长；
- 每个 candidate 使用新 `spec_sha256` 调用 `render_uniforms`；
- receipt 绑定新 binding/spec/pixel hash；
- 旧 receipt/attestation 对新 Spec 验证失败；
- final selected Spec 拥有匹配的新 attestation。

不得为性能跳过 validate、receipt、attestation 或 evaluate。

### 5.13 Refine 与 optimizer 的反馈衔接

当 optimizer 对某 source 到达 local optimum，下一次 structural Refine Prompt
增加安全摘要：

```json
{
  "uniform_optimization": {
    "stop_reason": "local_optimum",
    "evaluated_count": 4,
    "accepted_count": 1,
    "loss_delta": 0.003
  }
}
```

不传 raw path、旧值、新值或完整 search trace。该摘要进入
`_refine_context_sha256`。

Structural Refine 一旦产生 accepted new source：

- 清除该 source 的 duplicate candidate 临时状态；
- 保留全局 tuning draw count；
- 若全局 tuning budget仍有剩余，为新 source建立一次 session；
- target check 仍优先于新 session。

### 5.14 Artifact 和 public progress

公开 Artifact 仍只有：

- final render；
- metrics；
- manifest。

Private child 增加：

```text
private/uniform-optimization-trace.json
```

Private trace 可记录：

- candidate/parent hashes；
- component identity hash；
- tick/direction；
- loss/MAE/metric delta；
- accepted；
- stop reason。

Public metrics/manifest 只增加安全 summary：

- algorithm/version；
- config fingerprint；
- active/evaluated/accepted count；
- draw budget/used；
- initial/final loss；
- stop reason；
- base/selected spec hash；
- private trace hash。

不得公开 raw parameter path/value 或 rejected render。

Progress v2 最小扩展：

```json
{
  "candidate": {
    "role": "uniform_optimize",
    "outcome": "accepted | not_improved | duplicate"
  },
  "decision": {
    "next_action": "uniform_optimize | author_refinement | release_resources",
    "reason_code": "..."
  },
  "budgets": {
    "uniform_tuning": {"used": 2, "limit": 4, "scope": "attempt"}
  }
}
```

生命周期 wrapper 继续只负责 node/status/duration；业务 node 通过严格
projection 生成安全 decision/snapshot，不序列化完整 state/events。

### 5.15 阶段二影响文件

新增：

- `src/shaderforge/uniform_optimization/`
- `src/agent/app/nodes/layered_direct/uniform_optimization_nodes.py`

修改：

- `src/shaderforge/layered_spec/models.py`
- `src/shaderforge/layered_spec/hashing.py`
- `src/shaderforge/layered_spec/compiler.py`
- `src/shaderforge/program_spec/models.py`
- `src/shaderforge/program_spec/hashing.py`
- `src/shaderforge/program_spec/parsing.py`
- `src/agent/app/contracts/layerplan_glsl_direct.py`
- `src/agent/app/states/layerplan_glsl_direct.py`
- `src/agent/app/graphs/layerplan_glsl_direct.py`
- `src/agent/app/nodes/layered_direct/candidate_nodes.py`
- `src/agent/app/nodes/layered_direct/lifecycle_nodes.py`
- `backend/app/services/engine_rollout_runtime.py`
- `frontend/src/runStages.ts`
- `frontend/src/components/MinRunLivePanel.tsx`

同步最近架构文档和对应聚焦测试。

### 5.16 阶段二验收测试

ShaderForge：

- float/vec2/vec3/vec4 扁平化和稳定排序；
- 非整步初值、Decimal tick、clamp、边界；
- 不可行 component剔除；
- 非-manifest path、越界、schema/body/manifest 修改 fail closed；
- trusted patch 只改变一个 component；
- layer/Layered/binding/spec hash变化，source hash不变；
- AuthorIdentity不变，provenance绑定 parent/config/move；
- provenance/parser 篡改 fail closed。

Search：

- `+step` 改善则不浪费 `-step`；
- `+step` 不改善再测试 `-step`；
- strict lower-loss selection；
- minor improvement可保留但不算 material；
- full pass 无 material improvement得到 local optimum；
- local/global预算都不超限；
- dimension cap 使用稳定 permutation；
- 同 source不重复开启 session；
- accepted structural new source可开启新 session。

Renderer/attestation：

- 纯 uniform candidate prepared cache hit；
- source prepare只发生一次；
- 每个 binding产生新 receipt/attestation；
- 旧 receipt/attestation不能验证新 Spec；
- renderer failure保留 incumbent。

Graph：

- Initial 达 target，optimizer/Refine 都不运行；
- Initial 未达标且有 tunable，先 optimizer；
- optimizer 达 target，不调用 LLM Refine；
- optimizer local optimum 后进入 Refine；
- Refine accepted 后对新 source再做 optimizer；
- Refine rejected 后不重跑旧 source optimizer；
- no tunables直接进入 Refine；
- tuning hard failure不丢已有成功 Attempt；
- final candidate role可以是 `uniform_optimize`。

Artifacts/progress：

- public 只有安全 summary；
- private trace和manifest hash一致；
- raw path/value不进入 public API/progress；
- Backend/Frontend认识新增节点、role、budget和stop reason。

代表性跨组件 happy path：

```text
Initial not target
→ uniform +step rejected
→ uniform -step accepted
→ optimizer local optimum
→ structural Refine accepted
→ new-source uniform candidate reaches target
→ atomic parent Artifact publication
```

## 6. 实施切分

### PR 1：Refine policy 与 residual v2

- `DirectOptimizationPolicy`
- preset 单一映射
- target 早停
- residual `uv_bbox`
- policy/result fingerprint
- policy/residual/routing tests

### PR 2：Refine feedback、duplicate、min_delta/patience

- `RefineFeedback`
- Prompt/context hash
- Patch fingerprint/no-op
- failure helper
- selection delta/streak
- safe stop reason/progress
- Graph/full-chain regression tests

阶段一在 PR 2 完成后验收。

### PR 3：Uniform optimization 纯领域能力

- flattener/Decimal ticks
- search state machine
- `UniformPatchV1`
- provenance/hash/compiler
- ShaderForge unit/property tests

### PR 4：Uniform optimizer Graph 集成

- 新节点/路由/state/ledger
- prepared cache 和 candidate pipeline 复用
- target/Refine 衔接
- Agent/Renderer integration tests
- Graph 文档、`docs-check`、LangGraph validate

### PR 5：Artifact、Backend、Frontend 契约

- private trace/public summary
- progress v2 projection
- Backend schemas
- Frontend node catalog/timeline/budgets
- 代表性跨组件 happy path

阶段二在 PR 5 完成后验收。

## 7. 风险与控制

### 7.1 参数维度过多

风险：最多 16 个 tunable，每个可有 4 个 component。

控制：

- 单 Layer；
- active component cap；
- 全局/局部 draw 双预算；
- 一次只动一个 component；
- 稳定 permutation；
- provenance明确记录覆盖范围，不宣称全局最优。

### 7.2 LayerPlan region 选错目标 Layer

风险：LayerPlan 是 advisory，region 可能不精确。

控制：

- acceptance只看真实 objective；
- rejected candidate不更新 incumbent；
- 无 overlap时稳定 fallback；
- 后续可用有限差分 sensitivity取代 advisory ranking，但不进入 MVP。

### 7.3 provenance 扩展破坏旧 hash

控制：

- 只在 provenance 非空时加入 canonical 字段；
- 非优化旧 Spec hash保持不变；
- strict parser禁止模型注入；
- implementation/result/manifest版本显式升级；
- 加旧 Spec hash和反序列化回归测试。

### 7.4 optimizer 挤占结构 Refine 预算

控制：

- 不自动提高总 draw budget；
- optimizer有独立局部预算；
- target优先；
- local optimum后保留结构 Refine资格；
- public summary分开显示各预算。

### 7.5 进度泄露 private state

控制：

- 业务 node只输出严格 DTO/projection；
- stable enum，不透传任意 dict；
- public不包含 path/value/GLSL/diagnostics；
- 增加 DTO key-set与敏感字段回归测试。

## 8. 完成定义

阶段一完成必须同时满足：

- Initial/Refine target早停；
- deterministic duplicate不再进入draw；
- 首次失败反馈可驱动一次恢复尝试；
- `min_delta/patience`语义有测试锁定；
- residual top/bottom与WebGL UV一致；
- current_best严格单调；
- focused unit/integration tests通过；
- Graph文档、docs-check和LangGraph validate通过。

阶段二完成必须同时满足：

- 只有manifest参数能被修改；
- uniform candidate复用program但重新draw/receipt/attest；
- source hash不变，binding/spec hash变化；
- trusted provenance完整绑定；
- optimizer优先于结构Refine；
- local/global预算不超限；
- optimizer失败保留incumbent；
- public只发布安全summary；
- Backend/Frontend识别新增节点和reason；
- 至少一条代表性跨组件happy path通过；
- Graph文档、docs-check和LangGraph validate通过。
