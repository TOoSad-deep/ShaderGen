# 当前产品功能

本表不是路线图或 backlog；只有用户明确启动的产品功能才进入。状态为 `active`、`blocked` 或 `passing`，同一时间最多一个 `active`。

| id | 用户行为 | 阶段验收 | 状态 |
|---|---|---|---|
| F09 | 上传 PNG 后默认执行 direct GLSL + LayerPlan；当前按 [Layer 化改造方案](LAYERED_DIRECT_GLSL_REFACTOR.md) 将生成与 Refine 的最小单元收敛为单个 Layer；单次 attempt 修复失败后隔离创建一个 fresh direct attempt 重试，不自动降级到 ShaderGraph，并返回可渲染 GLSL、最终 Render、指标或明确失败信息。 | Layer ID 从 Plan 贯通到 Author/Patch，未目标 Layer 不被改写；编译后完整 ProgramSpec 通过真实 Render，并保持现有 API、strict total-loss 选择和 fresh direct retry。 | active |
