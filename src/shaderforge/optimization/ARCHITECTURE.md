# ShaderForge Optimization 架构

`optimization/` 为 `scene_mvp` 提供小预算、无随机、无模型依赖的数值候选提议，不持有 Renderer、Graph State 或 Artifact。

## 当前能力

- `propose_min_scene_candidates()` 按固定参数顺序和“减小、增大”顺序生成单参数邻域候选；每个候选都重新通过完整 `MinScene` schema。
- `base` 阶段白名单覆盖主体 `center`、`axes`，背景色，径向渐变的 `inner`、`outer`、`origin`、`scale`。
- `feature` 阶段使用稳定 `feature_id` 定位 `rim`、`shadow`、`polar_arc`、`edge_line` 等现有特征，白名单覆盖其 `center`、`axes`、`color`、`intensity`；当前 schema 没有独立 `highlight` 类型，高光由 `polar_arc`/`edge_line` 等现有 typed feature 表达。
- 颜色裁剪到 `[0, 1]`，强度裁剪到 `[0, 2]`，渐变 scale 裁剪到 schema 合法范围，位置与轴长按画布归一化范围裁剪；边界产生的空操作会被跳过。
- 每次调用同时受调用方 `remaining_draw_budget`、请求批量和模块硬上限 `32` 截断。模块只提议候选，不创建 draw，也不会把一次调用扩大成 2000 draw 搜索。
- `accept_strict_mae_improvement()` 保留为全局 MAE 工具；当前最小 Graph 由调用方按 `min_scene_composite_v2` 复合 loss 严格下降串行维护单调 `current_best`。
- `rebase_candidate_proposal()` 把固定顺序的候选计划逐项重放到最新 best，避免同批候选都从旧 baseline 出发而丢失已经接受的其他参数变化。
- 当前 `png_to_shader_min` 为 base 请求最多 32 个候选，并为每个真实 feature 请求最多 16 个候选；每次真实 draw、接受参数和候选数量都进入阶段 trace。

## 边界

- 优化器只能改白名单中的数值叶子；不修改画布尺寸、图元/颜色模型、feature id/type、schema version 或 feature 结构。
- 输入 `MinScene` 保持不变。候选是新的冻结实例，失败候选不会覆盖调用方 best。
- 本模块不实现 CMA-ES、随机采样、并行验收、Renderer 调用、预算记账或 Graph 路由；较大的 run 预算必须由调用方显式分批调度和逐 draw 记账。
- 结构变化仍应通过 `shaderforge.scene` 的 typed patch 完成，不能向本模块传入任意字段路径绕过白名单。
