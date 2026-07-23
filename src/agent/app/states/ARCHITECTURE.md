# States 架构

当前只保留 `PngToShaderMinState`，用于 `scene_mvp` 最小 Graph。

- checkpoint 轻量字段只包含阶段、状态、停止原因、运行身份、预算/计数、质量目标、best 摘要、feature queue 和 Refine 路由标志。
- `run_id`、图片、目标 RGB、Scene、GLSL、Render、`current_best`、Patch 证据、trace 和 final result 使用 `UntrackedValue`。
- 模型原始响应、reasoning、完整 Patch value、图片和 GLSL 不进入轻量状态或日志摘要。
- 数据库连接、Store、Renderer 和 Gateway 由 Builder/Service 持有，不进入 State。
- Memory/checkpoint 基础设施暂时保留，但当前产品 Graph 不读取或写入旧 V1 Memory。
