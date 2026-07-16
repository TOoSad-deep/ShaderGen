# ShaderForge Analysis 架构

`analysis/` 把参考图片转换成确定性、可序列化的测量结果，不调用 VLM，不推断物体语义。

## 当前能力

- 解码 Pillow 当前可安全识别的图片格式，并按白色底合成 alpha；产品 F09 的 HTTP 契约仍只接受 PNG，Analysis 本身不把 PNG/JPEG/WebP 维护成独立 allowlist；
- 超过运行契约长边时仅对分析副本降采样，保留原始尺寸和 hash；
- `normalize_target_png()` 为 M3 把模型输入与 Renderer/Oracle 参考图统一成白底 RGB PNG，并在超过契约长边时等比缩小，避免评分尺寸错配；
- 用边框颜色中位数估计背景；
- 用背景色差和最大连通区域估计主体 mask、bbox 与置信度；
- 提取量化主色、代表像素、边缘摘要和基础 ROI。

## 边界

- 坐标统一使用 Shader UV：左下 `(0, 0)`，右上 `(1, 1)`；
- 自动前景 mask 只适合背景相对稳定的 V1 样例，低置信度时调用方必须降低 geometry loss 权重；
- 不在这里做 VLM 视觉分层、GLSL 生成、浏览器渲染或候选接受；
- 模型均为不可变 dataclass；聚合根 `TargetMeasurements.to_dict()` 使用 dataclass 递归序列化嵌套测量，叶子模型不承诺各自提供 `to_dict()`。
