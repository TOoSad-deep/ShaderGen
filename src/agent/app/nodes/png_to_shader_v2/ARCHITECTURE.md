# PNG-to-Shader V2 Nodes 架构

`png_to_shader_v2/runtime.py` 是 V2.4 Graph 的 production node 真相源。`PNG_TO_SHADER_V2_NODE_IDS`、`build_png_to_shader_v2_node_callables()` 和 `PngToShaderV2NodeRuntime` 同时供 Graph Builder 与显式诊断 Harness 使用；Node Lab 不编译 Graph，也不复制 routing、Intent、Seed、Compiler、Renderer、Evaluator 或 Selector 语义。

## 依赖与副作用

- `PngToShaderV2NodeRuntime` 只通过 `ArtifactCatalog`、Interpretation provider、Context provider、Renderer factory、Metric evaluator、State Store/committer、structure envelope provider 和 promotion sink 注入副作用。
- 默认 runtime 不隐式调用模型、浏览器、State Store 或 Memory。正式 Graph run 必须注入 State Store；其 metered Catalog 对所有 JSON/GLSL/PNG put 按真实 bytes 在写入前 reserve、写入后 commit，不只统计 render PNG。V2.4 AI-off fixture 只闭合 candidate-attempt、逐 logical request 最多两次的 render-call、Artifact bytes 及 fixture/mock model-call；wall-time、model-tokens、cost 仍待 Service 的 monotonic deadline/typed receipt，production 因此 blocked。
- State 只传递 `PngToShaderV2State` 的版本、游标、小型 branch 状态与完整 `ArtifactRefV2`；production node 每次恢复时复验 ref identity、size/SHA 与 typed Schema。
- 22 个 node id 对应正式 §12 流程；Graph 与 Provider 一致性测试必须精确比较同一 tuple，禁止 Harness 自行维护另一份节点集合。
- Interpretation provider 当前只返回已审计 Artifact ref，不能回传 tokens/cost/elapsed receipt；因此仅允许 fixture/mock 或预物化 ref，真实模型与 product enable 保持 blocked，直至 typed call receipt 能闭合 `model_tokens`、`cost_usd_micros` 和 `wall_time_ms`。
- START 恢复由 phase/ref-aware routing 驱动：fresh 才进入 prepare；compile/render/evaluation/Candidate checkpoint 从最后确认边界继续，`finalized` 幂等结束。崩溃遗留 reservation 保守计为 used，禁止无账重复副作用。
- `render_candidate_v2` 先物化 immutable plan：固定五次独立 beauty capture，再按 pass id 执行 subject、每实例和全部启用 layer diagnostic。每次调用只执行一个 physical render 并经 Graph self-loop 继续；相同 PNG bytes 可内容寻址复用，但五个 logical request/ref 与预算调用不可去重。Renderer environment v3 从真实 WebGL1 context attributes 和 clear state 派生，不接受 fixture 自报为 actual。

## Node Lab Provider

`integrations/node_lab/registry.py` 从 production tuple 构造 descriptor。除 `analyze_visual_layers_v2` 的 fixture/mock/real 三种模式外，其余节点只声明 deterministic；`render_candidate_v2` 单独标记 browser 副作用。

`V2ProductionNodeExecutor` 每步创建隔离 runtime：

- `NodeLabArtifactCatalogV2` 把当前 LabRun 的不透明 id 映射为完整 `ArtifactRefV2`；既有 Artifact 必须由 State 携带完整元数据，新 Artifact 通过 Lab host 写入并返回 descriptor。跨 LabRun、缺失 schema、size/SHA 漂移全部 fail closed。
- fixture 生成版本化的最小严格 VisualInterpretation 响应；mock 只接受同一 LabRun 的 strict JSON Artifact；real 只接收由组合根注入、负责自身调用审计的 Interpretation provider。三者统一进入 production analyze callable 做恢复和预算状态转换。
- Node Lab 的 real execute 固定在步骤 id 分配和 Artifact 写入前 fail closed：Lab 的同步 provider
  边界没有 durable invocation、token/cost receipt 与 operation journal，不得成为第二套真实模型
  side-effect 协议。真实 Interpretation 只能由 V2 Service 预物化并绑定 audit/receipt；fixture/mock
  不变。preview 不调用 production/model，只返回未变 State 与安全边界摘要。
- Harness 固定 `production_admission_enabled=false`、`state_committer=None`、`promotion_sink=None`；`project_commit` 在任何副作用前拒绝。Renderer 只由 `render_candidate_v2` 创建，并由 production callable 的 `finally` 关闭。
- Production callable 的 Renderer 调用在 budget reservation 前持久化 call ordinal intent，并在 budget commit 前持久化 success/transient/failure evidence；未知崩溃恢复为占用该 ordinal 的 `unknown` evidence。Promotion 只接受实现稳定 operation id `execute/recover` 的 sink，并以 operation/receipt 两阶段 Artifact outbox 收口；未知 sink 结果不重放。

Provider 是可显式注入 `create_node_lab_application(node_provider=...)` 的公共入口；V1 仍是默认 Provider，两条 pipeline 的 LabRun 不可混用。
