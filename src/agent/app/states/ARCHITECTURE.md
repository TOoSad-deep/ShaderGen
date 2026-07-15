# States 架构

`src/agent/app/states/` 保存 PNG-to-Shader V1 的 LangGraph State，区分任务内 checkpoint 字段和当前调用的 `UntrackedValue` 字段。

## 当前状态

- `PngToShaderV1State`：M3 自动闭环状态，区分小型路由摘要和当前调用的大对象/证据。

## State 规则

- checkpoint 字段只保存 `project_id`、phase、iteration、current candidate/best 摘要、预算计数和停止原因。
- 图片、渲染图、完整 GLSL、ContextPack、选中 Memory ID、memory status、run ID、模型调用、事件、日志和 reasoning 使用 `UntrackedValue`。
- 不把数据库连接、Store 实例或对象存储客户端放进 State。
- 新增跨节点字段时，先更新 `agent_state.py`，再更新相关节点、service 映射和测试。
- 后端需要持久化的数据，由 Agent service 返回结构化摘要，再由后端统一写库。
- 依赖模型的测试必须使用模拟对象，不通过 State 触发真实模型调用。
- 模型运行配置由 Node 配置和 Graph 依赖注入提供，不放进 State；State 只保存当前图计算出的业务中间结果和摘要。

## `PngToShaderV1State` 边界

- checkpoint 摘要：phase、iteration、current candidate/best id 与 hash、best total loss/score summary、compile/visual/no-improvement/model 计数器、candidate sequence、stop reason。
- `UntrackedValue`：参考图、GLSL、渲染 PNG、TargetMeasurements、完整分析/Review/Score、CandidateRecord、ContextPack、预算快照、run id、model calls/events/logs 和 final result。
- Candidate 的可恢复真相源是 `LocalArtifactStore`，不是 checkpoint 中的大对象。M3 是同步闭环；跨进程中断恢复和异步 Run API 属于 V1.1，不能假定 `UntrackedValue` 可恢复。
- Graph State 不保存 Renderer、Artifact Store 或 Gateway 实例；这些依赖由 Builder/运行时 registry 持有。
