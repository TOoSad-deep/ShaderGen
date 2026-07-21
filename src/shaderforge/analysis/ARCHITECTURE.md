# ShaderForge Analysis 架构

`analysis/` 把参考图片转换成确定性、可序列化的测量结果，不调用 VLM，不推断物体语义。

## 当前能力

- 解码 Pillow 当前可安全识别的图片格式，并按白色底合成 alpha；产品 F09 的 HTTP 契约仍只接受 PNG，Analysis 本身不把 PNG/JPEG/WebP 维护成独立 allowlist；
- 超过运行契约长边时仅对分析副本降采样，保留原始尺寸和 hash；
- `normalize_target_png()` 为 M3 把模型输入与 Renderer/Oracle 参考图统一成白底 RGB PNG，并在超过契约长边时等比缩小，避免评分尺寸错配；
- 用边框颜色中位数估计背景；
- 用背景色差和最大连通区域估计主体 mask、bbox 与置信度；
- 提取量化主色、代表像素、边缘摘要和基础 ROI。

V2.0 另在 `models_v2.py` 冻结 Target/Measurements 基础模型；分段环 raw-instance evidence 增量将其 breaking 升级为 `TargetHypothesis.target_hypothesis_v3`、`TargetMeasurementsV2.target_measurements_v2_2` 和 `target_hypothesis_hash_v3`。每个 `InstanceGeometryV2` 仍由对应 ownership instance mask 像素独立重测 bbox、center、axes、PCA orientation、area、component、hole 与 `fill_topology`，并精确绑定 mask ref；禁止从主体 aggregate bbox、topology 或 instance count 猜布局/实例拓扑。假设 hash 同时绑定这些逐实例几何/拓扑、原目标、按 index 排列的 mask 内容、量化 confidence、规范 relation/结构语义，以及可选 `radial_segment_structure_evidence_v1` 的内容身份，不绑定 hypothesis id、Artifact id 或存储位置。

这是 breaking contract：旧 `target_hypothesis_v2` / `target_measurements_v2_1` payload 或旧 hash version 都 fail closed；ArtifactRef schema 同步迁移为 `target_measurements_v2_2`，payload/ref 任一侧仍为旧版不得加载。既有 development/validation Intent、Genome、Candidate 与 gate golden 必须基于新 hash 全量重放，不能原地改状态字符串升级。

透明 segmented-ring 会同时保留 source alpha 的 literal segment masks 与闭合 semantic ring 的互斥 ownership partition。`radial_segment_structure_evidence_v1` 逐段冻结 raw/ownership refs、共同 radial frame、内外径、跨 `2π` 的角中心/跨度、raw component/hole/topology，以及 raw pair relation 完整闭包；公开 verifier 会重新读取 source 与全部 mask bytes，重放 alpha segmentation、raw union、semantic subset、ownership union/exclusivity 和每段几何。该 evidence 是 Expander/Compiler 后续实现 segment primitive 的 typed 接入点，不把 bbox 椭圆近似冒充 segment 真值。

## V2.1 Measurements producer

- `measure_target_v2()` 复用白底 `normalize_target_png()` 作为模型/渲染参考，同时从原 source 按同一缩放规则单独重放 alpha；透明素材不得从白底 PNG 反推 mask。
- 有效 alpha 先经过冻结阈值、微小 component/孔洞去噪；无有效 alpha 时继续使用 normalized RGB border distance。所有 source、normalized reference、subject/instance/edge masks、evidence index 和 Measurements JSON 都写入 run 级内容寻址 Catalog。
- 每张 instance mask 在物化后立即形成 `InstanceGeometryV2`；runtime verifier 会重读 mask bytes 并以同一算法逐字段复算，手工复制 aggregate 几何/拓扑、错 mask ref 或浮点投影漂移均拒绝。instance pair relation 也从两张 mask 的交集与 4-neighbour 边界接触重测为 `overlap | touches | disjoint`，不用单一 draft label 涂抹全部 pair。
- 连续环/异形 hollow 使用归一化径向 profile 形成确定性分类；分段环同时保留 literal open 多实例与低置信 radial-closure ring 假设，不把语义闭合作为无不确定性的硬事实。`component_count`、`instance_count`、`hole_count` 与 topology 独立记录。
- 低置信 segmentation 只能保留替代 hypothesis 或进入 `soft_only_manual_review`，不能凭 confidence 晋升 hard constraint。evidence index 冻结 alpha/颜色 derivation、清理阈值、topology hint 和全部 Artifact 内容身份。
- `region_statistics` 只保存 `source_visible_alpha` 或 `full_normalized_image` 的 hypothesis-neutral 统计，不用 primary subject/instance id 污染 alternate hypothesis。hypothesis-bound bbox/center/area/axes/orientation 只保存在各自 `TargetHypothesis`。
- visible validation 的 producer、instance exact、ring/hollow、hole 和多实例指标由 stage-scoped 测试报告；2026-07-20 修正两张客观 18 段素材的错误 12 段标签后，41 例 structure exact 为 41/41。该可见结果不读取或替代 release-held-out。

## 边界

- 坐标统一使用 Shader UV：左下 `(0, 0)`，右上 `(1, 1)`；
- 自动前景 mask 只适合背景相对稳定的 V1 样例，低置信度时调用方必须降低 geometry loss 权重；
- 不在这里做 VLM 视觉分层、GLSL 生成、浏览器渲染或候选接受；
- 当前 producer 只从已物化 instance masks 确定性生成 `overlap | touches | disjoint`；contains/subtracts 仍需要更强的独立 instance evidence，不能从连通块或颜色标签猜测；
- 模型均为不可变 dataclass；聚合根 `TargetMeasurements.to_dict()` 使用 dataclass 递归序列化嵌套测量，叶子模型不承诺各自提供 `to_dict()`。
