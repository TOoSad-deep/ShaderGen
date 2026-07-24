# Perception 边界

`shaderforge.perception` 保存最小 scene 流水线的确定性感知。当前只面向单主体、纯色或近纯色背景图片，负责缩放工作图、背景估计、前景 bbox、中心/轴长和代表色；它不选择模型、不调用 LLM，也不声称支持通用图片拓扑。

输出同时包含可持久化测量摘要、只在 run 内存活的 RGB 数组、严格 `MinScene` fallback（legacy Builder 使用）和由同一感知计算经 `shaderforge.dsl` 迁移映射直接产出的 `ShaderDocument` fallback（默认 ShaderGraph 产品热路径使用，不再经过 MinScene 中间转换）。上层只能通过 `shaderforge.public` 或 typed 子包公共根使用该能力。
