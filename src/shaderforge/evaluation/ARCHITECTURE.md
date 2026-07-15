# ShaderForge Evaluation 架构

`evaluation/` 对参考图与候选渲染图执行确定性、可解释的基础评分。

## 当前能力

- sRGB `[0, 1]` 全局 RMSE / MAE；
- 灰度 Sobel 边缘损失；
- 基于 TargetMeasurements 的 bbox、中心和面积几何损失；
- 代表像素颜色损失；
- ROI RMSE 与 protection region loss；
- 按前景 mask 置信度衰减 geometry 权重并重新归一化总分；
- 比较两轮 protection loss 的最大退化。
- `CandidateRecord` 强绑定 GLSL、Author/provenance、compile、render、metrics、review 与父候选引用，并把来源收紧为 `model | deterministic`；确定性来源必须由调用方同时保存 generator version；
- `select_current_best()` 只接受硬约束通过、总损失达到最小改善且保护区最大退化不超阈值的候选；缺失既有保护证据直接拒绝。

## 边界

- 参考图与候选图尺寸必须一致，不在评分时静默 resize；
- V1 在 sRGB 空间评分，Lab、CIEDE2000、SSIM 和多尺度指标属于后续增强；
- 总分只是优化输入，调用方仍需保留完整评分向量；
- `ScoreBreakdownV1` 内部用不可变 pair tuple 保存有序映射，但写入 metrics Artifact 或 API 前必须调用 `to_dict()`；不得直接依赖 dataclass `asdict()`，否则 JSON 会把映射编码为 pair-list；
- Oracle 不调用 VLM、不选择问题域；`current_best` 更新由同包独立的纯 Selector 完成，Graph 只消费其决定；
- Oracle 只评分调用方显式提供的 ROI；生产 Graph 可以把严格 `VisualAnalysis` 的语义 ROI 追加到确定性测量 ROI，但不得用同名语义区域覆盖测量事实；
- 指标公式变化必须升级 `metric_version` 并重新校准 benchmark。
