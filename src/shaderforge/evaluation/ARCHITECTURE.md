# Evaluation 架构

当前只保留 `scene_mvp` 复合评分与空间残差：

- global、foreground、background、geometry、edge 和 worst-tile 指标；
- `min_scene_composite_v3` 固定权重；
- `spatial_residual_v2` 4×4 worst-tile signed RGB/亮度 residual，保留
  image-top row/column 兼容字段，并提供精确的 WebGL 左下原点 `uv_bbox`；
- 稳定的 dominant metric 决策。

`region.py` 还提供纯确定性的 `focused_region_metrics_v1`：输入同尺寸 RGB
beauty 图、显式 background RGB 与 WebGL 左下原点 `uv_bbox`，规范化并裁剪 ROI
后计算局部 MAE、几何 mask loss、edge loss 和 ROI 外 MAE。它沿用 `spatial_residual_v2` 的
坐标映射（图像数组保持 image-top 行序，不翻转图像），可用固定像素半径扩张
ROI，且不参与候选选择或 renderer/draw。

`role_alpha.py` 固定 diagnostic render 的 RGB channel 契约：pass 1 的 RGB
依次为 subject/highlight/detail，pass 2 的 RGB 依次为 shadow/glow/background。
它只从一次严格校验尺寸的 RGB uint8 帧拆出对应的不可变 alpha 平面和确定性
灰度 PNG；安全 `to_dict()` 仅暴露 role、哈希、非零像素率及尺寸，绝不内嵌大
字节字段。

评分函数只处理参考/候选 RGB 与背景信息，不调用模型、不选择 Graph 路由，也不持久化 Artifact。
