# States 架构

`src/agent/app/states/` 保存 LangGraph State 和 runtime Context 类型。Shader State 区分任务内 checkpoint 字段和当前调用 `UntrackedValue` 字段。

## 当前状态

- `State`：基础对话图状态，继承 `MessagesState`。
- `ShaderPipelineState`：图片到 GLSL 及渲染评审状态。
- `PngToShaderV1State`：M3 自动闭环状态，区分小型路由摘要和当前调用的大对象/证据。
- `Context`：LangGraph runtime context，用于传入运行时配置，例如 `model_thinking` 和 `capture_reasoning`。

## State 规则

- checkpoint 字段只保存 `project_id`、phase、iteration、GLSL hash、模型/时间和最近 Review fallback。
- 图片、渲染图、完整 GLSL、ContextPack、选中 Memory ID、memory status、run ID、模型调用、事件、日志和 reasoning 使用 `UntrackedValue`。
- 不把数据库连接、Store 实例或对象存储客户端放进 State。
- 新增跨节点字段时，先更新 `agent_state.py`，再更新相关节点、service 映射和测试。
- 后端需要持久化的数据，由 Agent service 返回结构化摘要，再由后端统一写库。
- 依赖模型的测试必须使用模拟对象，不通过 State 触发真实模型调用。
- 模型运行配置放在 `Context`，不要放进 State；State 只保存当前图计算出的业务中间结果和摘要。
- State 共享的 thinking 类型来自 `app/contracts/`，不得反向依赖 `app/llms/` 或 Node 实现。

## `ShaderPipelineState` 当前字段

- 输入：`image`、`content_type`、`rendered_image`、`rendered_content_type`、`glsl`。
- 输出：`evaluation`、`suggestions`。
- 模型元数据：`glsl_model_name`、`vision_model_name`、`review_model_name`。
- 当前调用过程摘要：`model_calls`、`events`、`logs`，均不进入 checkpoint。

## `PngToShaderV1State` 边界

- checkpoint 摘要：phase、iteration、current candidate/best id 与 hash、best total loss/score summary、compile/visual/no-improvement/model 计数器、candidate sequence、stop reason。
- `UntrackedValue`：参考图、GLSL、渲染 PNG、TargetMeasurements、完整分析/Review/Score、CandidateRecord、ContextPack、预算快照、run id、model calls/events/logs 和 final result。
- Candidate 的可恢复真相源是 `LocalArtifactStore`，不是 checkpoint 中的大对象。M3 是同步闭环；跨进程中断恢复和异步 Run API 属于 V1.1，不能假定 `UntrackedValue` 可恢复。
- Graph State 不保存 Renderer、Artifact Store 或 Gateway 实例；这些依赖由 Builder/运行时 registry 持有。
