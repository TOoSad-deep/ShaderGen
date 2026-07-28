# 当前产品功能

本表不是路线图或 backlog；只有用户明确启动的产品功能才进入。状态为 `active`、`blocked` 或 `passing`，同一时间最多一个 `active`。

| id | 用户行为 | 阶段验收 | 状态 |
|---|---|---|---|
| F09 | 上传 PNG 后默认执行 direct GLSL + LayerPlan；单次 attempt 修复失败后隔离创建一个 fresh direct attempt 重试，不自动降级到 ShaderGraph，并返回可渲染 GLSL、最终 Render、指标或明确失败信息。 | 相关聚焦测试通过，一条覆盖本次范围的 scene_mvp happy path 贯通，且用户确认达到当前阶段目标。 | active |
