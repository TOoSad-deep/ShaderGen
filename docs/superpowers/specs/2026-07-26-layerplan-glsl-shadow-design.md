# LayerPlan + 直接 GLSL Author shadow 设计（修订稿）

## 1. 状态与结论

- 日期：2026-07-26
- 状态：修订稿（2026-07-26 契约收尾：冻结 attestation 防伪/A 与 B 臂定义），取代被 Codex 审阅否决的首稿（首稿已随修订移除，不作为任何基线）
- 授权范围：D087 只授权 shadow 实验与本文档级设计基线；不修改生产 Graph、代码、API、FEATURE 状态、scorer、预算或 `current_best` 安全边界
- 当前功能：`F09` 继续是唯一 `active` 功能，发布缺口不变
- 与 D070 的关系：shadow 期间 D070 生产路径（ShaderDocument + specialized Compiler + 12 节点闭环）完全不变；只有第 10 节门禁全部通过后的新 ADR 才可以取代 D070 的执行表示部分

修订要点（相对被否决的首稿）：

1. LayerPlanV1 由独立受约束的视觉分析 Author 直接读取参考图生成严格结构化 JSON；确定性感知只是可选观测输入，LayerPlan 永久 advisory，不参与 scorer/acceptance。
2. InitialGLSLAuthor 与 RefineGLSLAuthor 都直接读取参考图（多模态），不是只给旧 ShaderDocument Prompt 注入摘要。
3. ShaderProgramSpecV1 是模型生成并经安全校验的执行真相，绝不能由 `CompiledDslShader`/`GraphProgramKey` 派生。
4. CandidateSnapshotV2 以 ProgramSpec/GLSL/Render/metric 为唯一权威，不保留 document/compiled 双真相。
5. 补齐静态校验、WebGL1 compile/link/draw、资源上限与 fail-closed 语义。
6. 定义 typed uniform 优化入口；源码/拓扑变化只能由 Author 新提案并重新全量校验。
7. legacy ShaderDocument 路径只作为带显式 provenance 的 control/fallback。
8. shadow A/B 两臂预算与状态完全隔离，失败/inconclusive 规则预先冻结。
9. 晋升证据必须 durable、内容寻址、可跨环境复验；`local_ignored`/`partial` 只能 no-go。

## 2. 真相层级

| 层 | 对象 | 角色 | 约束 |
|---|---|---|---|
| 视觉真相 | 用户上传的参考图 | 一切质量判断的最终锚点 | 所有 Author 直接读取参考图；候选 Render 只与参考图比较 |
| 非权威视觉分层参考 | LayerPlanV1 | 视觉分析 Author 对参考图的结构化分层解读 | 永久 advisory；不参与 scorer、acceptance、`current_best` 提交；缺失或错误不得使产品路径失败 |
| 执行真相 | ShaderProgramSpecV1 | 模型生成、经安全校验、实际编译渲染的 GLSL 程序 | 只有它能产生 Render；任何源码/拓扑变化都是新 Spec |
| 选择真相 | 真实 Render + metric | 候选接受的唯一依据 | 真实 WebGL1 渲染后按 `min_scene_composite_v3` strict total-loss 严格改善 |

核心规则不变：LayerPlan 可以影响“提议什么候选”，永不能影响“接受哪个候选”。接受谓词在任何阶段都不读取 LayerPlan 字段。

## 3. 契约

### 3.1 LayerPlanV1

- `schema_version`: 字面量 `layer_plan_v1`。
- 生成方式：由独立的视觉分析 Author（专用 constrained 角色，独立 Prompt 与输出 schema）**直接读取参考图**生成严格结构化 JSON。`shaderforge.perception` 的确定性感知结果只是可选观测输入（可作为 Prompt 上下文一并提供），不是 LayerPlan 的来源或真值。
- 输出契约：严格 JSON schema，未知字段拒绝、非法值 fail-closed、修复回路有界；模型自由文本不得绕过 schema 成为隐式分层通道。
- 字段：
  - `layers`: 有序数组，最多 8 项，每项含 `layer_id`（同 `ID_PATTERN`）、`role`（受限枚举：`background|subject|highlight|shadow|glow|detail`）、`z_index`、`region`（归一化 bbox）、`dominant_colors`（最多 4 个 RGBA）、`confidence`（`[0,1]`）、`notes`（可选短字符串）。
  - `reference_sha256`: 参考图内容哈希，绑定视觉真相。
  - `author_identity`: 生成该 plan 的 model ref、Prompt 版本与 schema 版本。
  - `observations_ref`: 可选，确定性感观测量的哈希引用（缺失不影响 plan 合法性）。
  - `plan_sha256`: 规范化 JSON 内容哈希，作为一切引用的唯一身份。
- 语义边界：LayerPlan 是“对参考图的一种分层解读”，不承诺与任何 ShaderProgramSpec 一一对应。
- 隐私边界：可进入 run 私有区；公开面只出现 `plan_sha256`、尺寸与状态摘要。

### 3.2 ShaderProgramSpecV1

ShaderProgramSpecV1 是**模型生成并经安全校验**的执行真相。它是一个独立的契约对象，绝不能由 `CompiledDslShader`/`GraphProgramKey` 派生或反向构造；legacy 路径的身份字段只能经显式 adapter 映射进兼容视图（见第 7 节），不构成同一对象。

- `schema_version`: 字面量 `shader_program_spec_v1`。
- 模型只输出语义字段：`fragment_source`（完整 WebGL1 fragment shader GLSL 源码）、`uniform_schema`（严格 uniform 声明表：名称、类型、取值域、默认值，未知字段拒绝）、`uniform_values`（与 schema 一一对应的初值，必须在声明域内且全部有限）、`tunable_manifest`（typed 可调参数清单：参数路径、类型、范围、步长，是数值优化的唯一合法地址空间）、`canvas`（宽/高）、`renderer_contract_id`（当前 `webgl1_static_no_texture_v1`）。
- 防伪边界：模型输出中**不得**包含 `validation_attestation` 或任何哈希字段；出现即拒绝（fail-closed），防止模型自我签发校验结论或自指哈希。
- 可信层字段（由可信解析/规范化层在模型输出之外组装或重算，不信任模型自报值）：
  - `source_sha256` / `binding_sha256` / `spec_sha256`：由可信层对规范化后的源码、uniform 绑定与整体语义字段重算；`spec_sha256` 明确**排除** `validation_attestation`，避免“哈希包含 attestation、attestation 又绑定哈希”的自哈希循环。
  - `author_identity`：由可信层按实际调用元数据绑定 `reference_sha256`（必填）、可选 `plan_sha256`、instruction hash、reference content type、角色输入上下文 hash、model ref、Prompt 版本、实际生效采样参数、角色（initial/refine/repair）与父 `spec_sha256`（Refine/repair 必填）；若发生结构修复，还必须绑定 repair Prompt、首轮输出/错误、Schema 以及首轮与第二次实际调用身份的 `repair_context_sha256`。
  - `validation_attestation`：只能由可信 Validator 在全量校验（含真实 compile/link/draw）通过后签发，绑定 `spec_sha256`、validator version、通过的检查项清单与 compile/link/draw 结果；模型或任何非 Validator 组件不得生成。
- 可执行真相是“Spec + 匹配 attestation”的组合：无 attestation、attestation 的 `spec_sha256` 与内容重算不匹配、validator version 不受信任或检查项/执行结果缺失的组合，不得渲染为候选、不得进入快照。
- 约束：不同 topology/源码产生新 `spec_sha256`；run 内 program 复用与 compile 上限沿用 run-scoped registry 语义，但以 `spec_sha256` 为 key。

### 3.3 ShaderCandidateSnapshotV2

- 权威字段：`program_spec`（ShaderProgramSpecV1）、`render`（真实 WebGL1 像素）、`mae`、`loss`、`metrics`、`residual_summary`、`parent_spec_sha256`、`provenance`。
- 可选字段：`layer_plan_ref={plan_sha256, plan_size_bytes, role=advisory}`，只记录候选提议时参考了哪份 LayerPlan；选择谓词不得读取。
- 不保留 `document`/`compiled` 字段：V2 不存在 DSL 文档与编译产物的双真相，执行真相只有 `program_spec`。
- 不变量：不可变；GPU prepared handle 仍只存在于 run-scoped registry，不进入 State/Artifact；提交条件仍是 strict total-loss 严格改善。
- legacy 兼容：D070 的 `ShaderGraphCandidateSnapshot` 只能经显式 legacy adapter 包装为 control candidate（`provenance=legacy_shader_graph_control`）进入对比，不得与 V2 字段混写。

## 4. Author 角色与时序

shadow 期间生产 Graph 不变；以下为 shadow 臂内部时序，实现前需另立 ADR。

### 4.1 角色

- **VisualAnalysisAuthor**：直接读取参考图（可选叠加确定性感知观测与用户意图），输出 LayerPlanV1。独立 Prompt、独立预算、独立失败域。
- **InitialGLSLAuthor**：直接读取参考图 + 用户意图 + 可选 LayerPlanV1，输出完整 ShaderProgramSpecV1（initial 角色）。
- **RefineGLSLAuthor**：直接读取参考图 + 当前 Render + metric/residual 摘要 + validated incumbent（当前 `current_best` 的 Spec 与指标）+ 可选 LayerPlanV1，输出一个新 ShaderProgramSpecV1（refine 角色，父 hash 绑定 incumbent）。
- 三者都直接读取参考图（多模态输入），不是把摘要注入旧 ShaderDocument Prompt；旧 Prompt 契约不复用。

### 4.2 时序

```text
参考图 ──> VisualAnalysisAuthor ──> LayerPlanV1（仅 shadow 私有区/State 引用）
参考图 + 用户意图 (+ LayerPlan) ──> InitialGLSLAuthor ──> Spec ──> 全量校验 ──> 真实 Render ──> strict 选择
参考图 + current Render + metric/residual + incumbent (+ LayerPlan)
        ──> RefineGLSLAuthor ──> 新 Spec ──> 全量校验 ──> 真实 Render ──> strict 选择
uniform 数值优化：只沿 tunable_manifest 调 uniform_values ──> 重渲染 ──> strict 选择
```

规则：

- LayerPlan 派生失败是 shadow 失败，不是产品失败；产品路径继续，shadow 臂按预声明规则降级或标记 `inconclusive`。
- Refine 只能基于 validated incumbent 提案；非法、校验失败、渲染失败或未严格改善的候选整体丢弃。
- 无效 Refine 不得重建优化队列或触发额外渲染（沿用 D078 收敛语义）。

## 5. 校验与安全边界

每个 ShaderProgramSpecV1 在进入渲染前必须通过全量校验，任何一项失败即 fail-closed（候选拒绝，不冒泡为未分类异常）：

1. **静态校验**：schema 严格性、uniform 声明与值一一对应、所有数值有限（拒绝 NaN/Inf）、canvas 为正且在上限内。
2. **GLSL 结构限制**：禁止 texture sampler 与任何扩展（`#extension`），符合 `webgl1_static_no_texture_v1`；循环必须有编译期常量上界（有界循环）；禁止动态索引越界模式。
3. **资源上限**：源码字节数上限、uniform 数量与总分量上限（不超过 WebGL1 最低保证）、tunable 参数数量上限、单 run compile 次数上限（按 run 预算推导，同 D077 原则）。
4. **真实执行校验**：WebGL1 compile/link 必须成功，并执行至少一次真实 draw 读回像素；compile/link/draw 任一失败即候选失败。
5. **attestation 签发**：以上全部通过后由可信 Validator 签发 `validation_attestation`（绑定重算的 `spec_sha256`、validator version、检查项清单与 compile/link/draw 结果）；模型输出自带 attestation/哈希字段、attestation 与内容重算不匹配或缺失的组合一律 fail-closed，不得渲染、不得成为候选。

数值优化边界：优化器只能沿 `tunable_manifest` 修改 `uniform_values`（typed uniform 优化入口），不得触碰 `fragment_source`、uniform schema 或拓扑；源码/拓扑变化只能由 Author 提出新 Spec 并重新走全量校验。`current_best` 提交谓词、scorer、recursion 推导与预算计数均不得读取 LayerPlan。

## 6. Shadow A/B 预算与状态隔离

- 冻结实验臂定义：Arm A 与 Arm B 尽量使用**同一模型、同一 Prompt 主体、同一请求采样参数、同一预算和同一组 direct GLSL Author**（Initial/Refine/repair 角色相同）；预期控制差异只有 LayerPlan——Arm A 不提供，Arm B 增加同一份 LayerPlanV1。无 seed 的模型采样、执行顺序和服务端漂移仍是混杂因素，单次运行只作探索；必须多轮重复并做 AB/BA 交叉平衡后才能评价关联，不能声称唯一因果变量。
- legacy ShaderDocument 路径**不属于**这两个实验臂：它只是产品安全 fallback 与额外 control reference（见第 7 节），不得与任一臂共享 `current_best`、program cache 或预算；其运行与结果单独记账、单独 provenance。
- 两臂各自独立记账与隔离状态：LayerPlan 生成、Initial、Refine、repair 的模型调用与 token、compile 次数、draw 次数、wall-clock、program cache 与 `current_best` 演进完全分离，互不消耗、互不豁免对方预算，也不占用产品 run 的既有硬预算。
- 执行顺序与臂身份入证据：每条候选/指标记录携带 `arm_id`（`A|B`）、配置指纹与执行序号；两臂执行顺序（固定或交错）在查看结果前冻结并写入报告，不得事后调整。
- 实验 run 按 D061/D062 惯例在 YAML 显式声明 `independent_experiment`、实验 ID 与报告版本；冻结 benchmark 身份携带 shadow 配置必须 fail closed。
- 失败/inconclusive 规则在查看结果前冻结：任一臂的 LayerPlan 生成失败、Author 输出非法（含自带 attestation/哈希字段）、校验失败、渲染失败、预算耗尽或报告缺字段的归类（降级/排除/`inconclusive`）必须预声明，不得事后归类。

## 7. legacy ShaderDocument 路径的定位

- D070 的 ShaderDocument/specialized Compiler 路径在 shadow 期间是生产真相源，在 shadow 实验中只能作为 **control/fallback** 参与：经显式 legacy adapter 包装为 control candidate，`provenance=legacy_shader_graph_control`，身份由 adapter 映射进兼容视图，不冒充 ShaderProgramSpecV1。
- 任何 legacy 路径失败（fallback 触发、编译失败、渲染失败）必须带显式 provenance 记录，不得冒充 model-generated 候选；scorer 选择结果与 provenance 一并入账。

## 8. Artifact 与 API 兼容

- 公开 Artifact 白名单继续只有 `final-render`、`metrics`、`manifest`；进度事件不新增 LayerPlan/GLSL 内容字段。
- manifest 可选追加 `shadow` 摘要块（schema 版本、各对象 hash、尺寸、durability）；缺省表示无 shadow 实验，旧消费者行为不变。
- shadow 详细证据（LayerPlan 全文、Spec 源码、两臂候选对比）只写 run 私有区；HTTP API 不新增端点、不改既有字段语义；前端第一阶段不展示 LayerPlan。

## 9. 晋升证据的耐久性要求

晋升评审只接受 **durable、内容寻址、可跨环境复验** 的证据：报告与输入产物内容寻址（SHA-256）、身份与版本完整、在非本机环境可独立复算。`local_ignored` 或 registry `partial` 的证据只能支撑 no-go 结论，不得作为晋升依据（对齐 D074/D075 与 evidence registry 规则）。

## 10. 晋升门禁与决策关系

LayerPlan/直接 GLSL Author 从 shadow 变为任何生产用途之前必须全部满足：

1. 在新候选空间上重新冻结的版本中立 benchmark manifest（按 D076，不得复用旧 Feature/旧 DSL 空间结论）。
2. 固定样例真实模型 A/B：实验臂在预声明 gate 下严格优于控制臂且无实质回退；gate 在看结果前冻结。
3. 独立人工偏好门禁（自动代理看片不替代人工盲评）。
4. 晋升证据满足第 9 节耐久性要求；`local_ignored`/`partial` 一律 no-go。
5. 差异审计与版本冻结后另立 ADR。该 ADR 可以取代 D070 的执行表示部分（ShaderDocument/specialized Compiler 作为默认执行真相）；门禁通过前 D070 生产路径完全不变，本文档不授权任何晋升。

## 11. 阶段划分

| 阶段 | 内容 | 完成判据 |
|---|---|---|
| 1（本次） | 修订版设计基线：D087 修订 + 本文档 + PROGRESS 刷新 | `make docs-check` 与 `git diff --check` 通过；无生产改动 |
| 2 | shadow 实现：三类 Author、Spec 校验器、私有证据、A/B 隔离记账 | 另立 ADR；单元/集成测试；Graph 文档同步（如触碰） |
| 3 | shadow A/B 实验：两臂真实运行与冻结报告 | 预声明 gate；durable 内容寻址报告入 evidence registry |
| 4 | 晋升评审：按第 10 节门禁决定生产角色 | 另立 ADR；未过门禁则保持 shadow 或退役 |
