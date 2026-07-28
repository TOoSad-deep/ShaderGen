# Direct GLSL Layer 化改造方案

状态：已实现（2026-07-28）
范围：当前 `scene_mvp` / F09 默认 direct engine
目标：把模型生成与 Refine 的最小修改单元从“整份 GLSL”缩小为“单个 Layer”，同时继续复用现有单 Pass WebGL1 Renderer、ProgramSpec 安全校验、真实 Render 和全局 loss 选择。

## 1. 当前问题

当前链路只在视觉分析阶段保留 Layer：

```text
PNG
  -> LayerPlanV1（background / subject / shadow / highlight）
  -> Author 输出一份完整 ShaderProgramSpecV1
  -> 一整段 fragment_source
  -> Refine 重写整份 Spec
```

`LayerPlanV1` 进入 Author 后被压平，`ShaderProgramSpecV1` 只有完整 GLSL 和扁平 uniforms。系统无法知道某段代码或参数属于哪个 Layer；Refine 即使只想修改高光，也可能改动背景、主体和阴影。

本次改造必须让稳定 `layer_id` 从 LayerPlan 延续到模型程序表示和 Refine Patch。

## 2. 目标链路

```text
PNG
  -> VisualAnalysis Author
  -> LayerPlanV1
  -> Initial Author
  -> LayeredShaderSpecV1
  -> 确定性 Layer Compiler
  -> ShaderProgramSpecV1
  -> 静态校验 -> WebGL1 prepare/draw -> metric
  -> 选择 current_best
  -> Refine Author 只输出一个 LayerPatchV1
  -> 应用 Patch 后重新编译、整图渲染和全局验收
```

三个对象的职责固定为：

| 对象 | 职责 |
|---|---|
| `LayerPlanV1` | 视觉规划：有哪些层、位置、顺序、颜色和角色 |
| `LayeredShaderSpecV1` | 模型维护的 Layer 级程序源表示 |
| `ShaderProgramSpecV1` | Compiler 生成、Renderer 实际执行的完整 GLSL 契约 |

最终仍只编译和绘制一份完整 GLSL，不引入多 Pass 或逐层纹理。

## 3. 第一版最小契约

在 `src/shaderforge/layered_spec/` 新增纯确定性契约、哈希、Patch 和 Compiler。该包可以依赖 `shaderforge.program_spec`，`program_spec` 不反向依赖它。

### 3.1 LayeredShaderSpecV1

```json
{
  "schema_version": "layered_shader_spec_v1",
  "plan_sha256": "canonical LayerPlan 哈希",
  "canvas": {"width": 512, "height": 512},
  "layers": [
    {
      "layer_id": "background",
      "role": "background",
      "z_index": 0,
      "glsl_body": "vec4 layer_color = ...; return layer_color;",
      "uniform_schema": {},
      "uniform_values": {},
      "tunable_manifest": []
    }
  ]
}
```

第一版约束：

- `layers` 必须与 `LayerPlanV1.layers` 的 `layer_id`、`role`、`z_index` 一一对应，顺序一致。
- Layer 数量继续使用现有 1..8 上限。
- 每层只提供固定函数体 `glsl_body`；Compiler 负责函数名、签名和最终 `main()`，模型不得输出 `main()`、全局 uniform、precision、varying 或预处理指令。
- 每层函数固定接收 `vec2 uv`，返回 **premultiplied RGBA** `vec4`。
- 每层 uniforms 保存在该 Layer 内；Compiler 汇总时校验全局名称唯一。建议 Prompt 使用 `u_<layer_id>_*`，第一版不实现 GLSL 标识符自动重写。
- 第一版只支持固定的 premultiplied source-over 合成，按 `z_index` 从后到前执行；不开放 blend mode。
- 第一版不增加共享 helper、跨 Layer 依赖、Layer 增删或重排。复杂数学先写在目标 Layer 函数体内。
- 上述 JSON 是模型允许输出的语义字段；canonical `LayeredShaderSpecV1` 的 author identity 仍由可信层在解析后装配。

可信层计算：

- `layer_sha256`：绑定单层 `layer_id/role/z_index/glsl_body/uniform_schema/uniform_values/tunable_manifest`。
- `layered_spec_sha256`：绑定 schema、`plan_sha256`、canvas、全部有序 Layer、author identity。
- 模型不得自报任何哈希或 author identity。

### 3.2 LayerPatchV1

Refine 第一版只支持原子整层替换：

```json
{
  "schema_version": "layer_patch_v1",
  "base_layered_spec_sha256": "当前 best 的 LayeredShaderSpec 哈希",
  "target_layer_id": "highlight",
  "expected_layer_sha256": "当前 highlight Layer 哈希",
  "replacement": {
    "layer_id": "highlight",
    "role": "highlight",
    "z_index": 2,
    "glsl_body": "...",
    "uniform_schema": {},
    "uniform_values": {},
    "tunable_manifest": []
  }
}
```

应用 Patch 时：

1. 校验 base Spec 哈希。
2. 校验目标 Layer 存在及旧 Layer 哈希匹配。
3. replacement 的 `layer_id/role/z_index` 必须与旧 Layer相同。
4. 只替换目标 Layer，其他 Layer 对象和哈希必须保持不变。
5. 完整重建 `LayeredShaderSpecV1`，重新执行全局名称和资源上限校验。

第一版不支持参数 Patch、源码局部文本 Patch、增删层或重排；整层替换已经能够实现所需修改隔离。

## 4. 确定性 Layer Compiler

新增：

```python
compile_layered_shader(
    layered_spec: LayeredShaderSpecV1,
) -> ShaderProgramSpecV1
```

Compiler 负责：

1. 为每层生成稳定函数名，例如 `sg_layer_0_background`。
2. 把 `glsl_body` 包入固定函数：

   ```glsl
   vec4 sg_layer_0_background(vec2 uv) {
       // model-authored glsl_body
   }
   ```

3. 汇总和校验全部 Layer uniforms。
4. 按 Layer 顺序生成固定 source-over compositor：

   ```glsl
   vec4 accum = vec4(0.0);
   vec4 layer = sg_layer_0_background(v_uv);
   accum = layer + accum * (1.0 - layer.a);
   ```

5. 最终与不透明白色背景合成并输出 `alpha=1`，避免未覆盖区域透明。
6. 生成现有 `ShaderProgramSpecV1`，继续走：

   ```text
   validate_program_spec_safety
     -> Renderer prepare/draw
     -> ExecutionReceipt
     -> ValidationAttestation
   ```

Compiler 不做图像评分，也不调用模型。`LayeredShaderSpecV1` 与编译后 `ShaderProgramSpecV1` 的哈希对应关系保存在 candidate 和私有 manifest，不给现有 ProgramSpec 增加新的公开字段。

真实运行发现模型会偶发输出常量 edge 倒置的 `smoothstep`。Compiler 复用既有
确定性修复，将该已知等价形式改写为合法的
`1.0 - smoothstep(smaller, larger, value)`；其他静态或编译失败仍 fail-closed。

## 5. Author 改造

保留现有 VisualAnalysis Author，不改变 `LayerPlanV1`。

新增三个 Prompt：

- `direct_layered_initial_v1.yaml`
- `direct_layered_refine_v1.yaml`
- `direct_layered_repair_v1.yaml`（结构修复共用）

Initial Author 输入：

- 原始 PNG
- 用户 instruction
- canonical LayerPlan
- canvas
- `LayeredShaderSpecV1` JSON Schema

Initial 必须完整实现 LayerPlan 中的每个 Layer。
其本轮 JSON Schema 会把 canvas 和逐层 `layer_id/role/z_index` 编成固定值，
结构修复不再依赖模型重新猜测这些可信字段。

Refine Author 输入：

- 原始 PNG
- 当前完整 Render
- current best 的指标与 residual summary
- current best `LayeredShaderSpecV1`
- canonical LayerPlan
- `LayerPatchV1` JSON Schema

Refine 根据原图、当前 Render、残差和 LayerPlan 在 Patch 中选择一个
`target_layer_id`，不再返回完整 ProgramSpec。可信层只允许它替换该目标
Layer；候选仍只按整张 Render 的 strict total-loss 改善提交。第一版不额外
实现独立的 Layer 路由器，真实使用证明有需要后再增加。

## 6. Direct Runner 改造

`LayerPlanGlslDirectRunner` 的候选需要同时保存：

```text
layered_spec
compiled_program_spec
render bytes
metric / residual
parent layered spec hash
patched layer id
```

Initial：

```text
LayerPlan
  -> Layered Initial
  -> compile_layered_shader
  -> 现有 safety/render/attestation/metric
  -> current_best
```

Refine：

```text
current_best
  -> Refine Author 输出一个 LayerPatch
  -> apply_layer_patch
  -> compile_layered_shader
  -> 现有 safety/render/attestation/metric
  -> loss 严格改善才替换 current_best
```

产品 direct runner 不再把休眠 shadow A/B Harness 当作主执行内核。实现时把当前 direct 所需的 decode、canvas、真实 render/attest、metric 和 ledger 路径保留在 direct service；旧 shadow Harness 保持原协议，不为本次改造同步升级，也不删除历史证据。

现有 attempt 隔离、最多一次 fresh direct 重试、预算和资源清理逻辑保持不变。

## 7. Backend 与 Artifact

公开 API 不增加字段，避免无关前端改造：

- `engine` 继续为 `direct_glsl_layerplan_v1`
- `representation` 继续为 `shader_program_spec_v1`，表示 Renderer 实际执行的表示
- `renderer_path` 继续为 `direct_program_spec_v1`
- `glsl` 继续返回最佳 compiled ProgramSpec 的 `fragment_source`

私有 attempt 增加：

```text
private/layer-plan.json
private/layered-shader-spec.json
private/program-spec.json
private/shader.frag
private/render.png
private/metrics.json
```

`program-spec.json` 明确是编译后的可执行 ProgramSpec；manifest 增加：

```text
authoring_representation=layered_shader_spec_v1
layered_spec_sha256
compiled_spec_sha256
```

普通日志和公开进度仍不得包含 Layer 正文、GLSL、Spec 或 Prompt。
失败 attempt 额外保存脱敏的规则类别、阶段和行号，便于定位真实模型输出问题；
不保存模型原始响应、源码或编译日志正文。

## 8. 实施顺序

### 步骤一：领域契约与 Compiler

新增 `src/shaderforge/layered_spec/`：

- `models.py`
- `parsing.py`
- `hashing.py`
- `patching.py`
- `compiler.py`
- `__init__.py`
- `ARCHITECTURE.md`

完成严格解析、稳定哈希、整层替换和确定性编译到现有 `ShaderProgramSpecV1`。

### 步骤二：Initial/Refine Author

修改或新增：

- `src/agent/app/contracts/` 下 Layered Spec/Patch 的模型输出 Schema adapter
- `src/agent/app/nodes/layered_direct/authors.py` 中默认 direct 使用的 Initial/Refine helper
- `src/agent/app/prompts/direct_layered_initial_v1.yaml`
- `src/agent/app/prompts/direct_layered_refine_v1.yaml`

旧 direct 整份 ProgramSpec Prompt 仅保留给休眠 shadow 兼容，不再进入默认产品 direct runner。

### 步骤三：Direct Runner 与 Artifact

改造：

- `src/agent/app/services/layerplan_glsl_direct.py`
- 必要的 direct candidate/ledger 类型
- `backend/app/services/engine_rollout_runtime.py`

贯通 Initial、单 Layer Patch、Compiler、现有 Renderer/metric/current_best 和私有 Artifact。

### 步骤四：最近文档

实现完成后只更新受影响文档：

- `docs/ARCHITECTURE.md`
- `src/shaderforge/ARCHITECTURE.md`
- `src/shaderforge/program_spec/ARCHITECTURE.md`
- `src/agent/app/services/ARCHITECTURE.md`
- `src/agent/app/nodes/layerplan_glsl_shadow/ARCHITECTURE.md`

本次不修改 LangGraph 节点、边或路由，因此不需要改 Graph 图。

## 9. 最小验证

不运行 benchmark、A/B、shadow、promotion、canary 或全量 `make check`。

只增加和运行：

1. `layered_spec` 单元测试：
   - LayerPlan ID/顺序不一致时拒绝。
   - Compiler 对相同输入生成稳定 GLSL/hash。
   - 编译产物通过现有 ProgramSpec 静态安全校验。
2. Layer Patch 单元测试：
   - 只替换目标 Layer，其他 Layer hash 不变。
   - base hash、target hash 或 Layer identity 不匹配时拒绝。
3. Author 聚焦测试：
   - Initial 输出完整 Layered Spec。
   - Refine 只输出一个合法 Layer Patch。
4. 一条 fake LLM/Renderer direct 集成链：
   - `plan -> layered initial -> compile -> render -> layer patch -> compile -> render -> strict improvement`。
5. 一条真实 WebGL1 happy path：
   - Layered Spec 编译后能够 prepare/draw，并产生有效 PNG/receipt。
6. 受影响 Backend 聚焦测试：
   - 最佳 compiled GLSL 和公开 Artifact 仍正常返回。
   - fresh direct retry 行为不变。

## 10. 完成条件

- 默认 direct Initial 的模型产物是 `LayeredShaderSpecV1`，不再是完整 `ShaderProgramSpecV1`。
- LayerPlan 的全部稳定 Layer ID 在 Layered Spec 中一一保留。
- 每轮 Refine 只允许替换一个 Layer；未目标 Layer 的 hash 保持不变。
- Layer Compiler 生成可通过现有安全校验和真实 WebGL1 draw 的完整 ProgramSpec。
- `current_best` 仍只按整图 strict total-loss 改善更新。
- API 继续返回最佳 GLSL、Render、metrics 和明确失败信息。
- 上述聚焦测试和一条真实 Renderer happy path 通过。

当前实现已满足以上完成条件。后续章节保持为非阻塞的按需扩展，不属于本次
改造验收。

真实 PNG 并行运行补充验证了 bubble 与复杂 ripples 的完整链路；运行中发现
并修复了非零小数除数误报、Layered Initial repair 缺少本轮固定 Layer/Canvas
约束，以及失败 attempt 丢失脱敏规则类别的问题。heart 仍暴露模型 transient
和领域输出无效，后续只按新的稳定 Parser 类别做定向修复。

## 11. 后续接入待办

以下项目不属于第一版完成条件，不阻塞当前 Layer 粒度改造。后续按真实案例
单独选择、实现和验收，不要求按列表顺序全部完成。

### 渲染与合成

- [ ] **增加必要的 blend mode**
  - 触发条件：真实案例证明统一 premultiplied source-over 无法表达辉光、
    屏幕叠加或乘法阴影。
  - 最小接入：只增加案例需要的 mode；冻结合成公式和顺序；Compiler 生成
    对应 GLSL；增加一个 mode 单测和一个真实 Render 用例。
- [ ] **支持多 Pass**
  - 触发条件：出现必须读取上一 Pass 结果的模糊、反馈、折射或后处理效果，
    单段 GLSL 无法合理实现。
  - 最小接入：定义 Pass 顺序、输入输出 texture 和 framebuffer 生命周期；
    保持资源有界清理；用一条两 Pass happy path 贯通。
- [ ] **生成或接入 Layer mask**
  - 触发条件：需要像素级 Layer 边界、外部 mask 输入，或逐层评分无法仅靠
    Layer alpha/region 完成。
  - 最小接入：mask 与 `layer_id`、参考图哈希、尺寸和坐标系绑定；明确是模型
    生成、确定性生成还是用户输入；验证一条 mask 对齐用例。

### Layer 结构与复用

- [ ] **支持 Layer 增删和重排**
  - 触发条件：真实 Refine 案例需要修正 LayerPlan 漏层、多层误合并或错误
    z-order，整层替换无法完成。
  - 最小接入：增加单个原子 add/remove/reorder Patch；绑定 base hash；完整
    重建并校验 LayerPlan/Spec 一致性；每种实际启用的操作各有一个回归测试。
- [ ] **支持一次修改多个 Layer**
  - 触发条件：一个不可分割的视觉修复必须同时改变两个强耦合 Layer，连续
    单 Layer Patch 会产生不可接受的中间状态。
  - 最小接入：Patch 明确列出全部目标 Layer 和旧 hash；原子应用或整体回滚；
    不允许未声明 Layer 被修改。
- [ ] **增加跨 Layer shared helper/依赖图**
  - 触发条件：多个 Layer 重复主体 SDF、噪声或坐标变换，导致明显代码膨胀
    或几何不一致。
  - 最小接入：稳定 definition id、显式依赖列表、循环依赖拒绝、删除引用保护
    和确定性编译顺序；Patch 必须声明是否修改共享定义。
- [ ] **评估 GLSL AST、模块系统或标识符自动重写**
  - 触发条件：Layer helper/uniform 命名冲突频繁，仅靠全局唯一校验已明显影响
    生成成功率。
  - 最小接入：先解决实际冲突类型，不建设通用 GLSL 编译器；保持生成结果可
    静态校验和确定性哈希。

### 评估与优化

- [ ] **增加逐层评分或误差归因**
  - 触发条件：全图 residual 无法稳定判断应修改哪个 Layer，导致连续错误
    Patch 或无效 Refine。
  - 最小接入：明确每层目标来源和重叠像素归因；逐层分数只用于选目标或诊断，
    最终 candidate 仍需通过整图 strict total-loss 验收。
- [ ] **增加 Layer 级 uniform optimizer**
  - 触发条件：Layer 结构和代码已经正确，主要误差可以通过参数微调降低，继续
    调模型成本过高。
  - 最小接入：只沿目标 Layer 的 `tunable_manifest` 搜索；禁止修改源码和其他
    Layer；复用完整 Render 和 strict improvement。

### 架构与兼容

- [ ] **评估 LayeredShaderSpec 与 ShaderGraph 合并**
  - 触发条件：两条 engine 的 Layer、Patch、Compiler 或维护成本出现持续重复，
    且现有 ShaderGraph DSL 能覆盖 direct 的真实表达需求。
  - 最小接入：先形成明确的统一契约和迁移边界；不得为了统一而降低 direct GLSL
    表达能力；保持 engine 结果可判别。
- [ ] **补充旧私有 Artifact 迁移或双写兼容**
  - 触发条件：出现必须继续读取改造前 `program-spec.json` 的真实工具或数据。
  - 最小接入：只覆盖已确认的 reader 和版本；不默认迁移全部历史输出。

### 质量与上线

- [ ] **运行质量实验**
  - 触发条件：需要比较 Layered 与旧整段 GLSL 的质量、成本或稳定性，或者准备
    上线、合并和推广。
  - 可按需要选择 benchmark、A/B、shadow、盲评、promotion、canary 和 evidence；
    普通功能实现不自动启动这些工作。
  - 最小接入：先冻结样本、指标、实现身份和比较问题，只运行回答当前决策所需的
    最小实验。
