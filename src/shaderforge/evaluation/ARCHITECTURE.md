# Evaluation 架构

当前只保留 `scene_mvp` 复合评分与空间残差：

- global、foreground、background、geometry、edge 和 worst-tile 指标；
- `min_scene_composite_v3` 固定权重；
- `spatial_residual_v2` 4×4 worst-tile signed RGB/亮度 residual，保留
  image-top row/column 兼容字段，并提供精确的 WebGL 左下原点 `uv_bbox`；
- 稳定的 dominant metric 决策。

评分函数只处理参考/候选 RGB 与背景信息，不调用模型、不选择 Graph 路由，也不持久化 Artifact。
