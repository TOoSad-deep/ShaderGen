# Layered Direct Author

本目录只负责默认 direct engine 的两个有界模型调用：

- Initial 接收参考图、canonical LayerPlan 和 canvas，输出完整
  `LayeredShaderSpecV1` 语义；可信层注入 AuthorIdentity、Plan hash 和内容
  hash。
- Refine 接收参考图、current render、整图指标和 current best，只输出一个
  `LayerPatchV1`；Patch 绑定父 Layered Spec、目标 Layer 和旧 Layer hash。

严格 JSON adapter 位于 `agent.app.contracts.layered_direct_glsl`，确定性
Patch/Compiler 位于 `shaderforge.layered_spec`。本目录不渲染、不评分、不
决定 `current_best`，也不调用历史整份 ProgramSpec Initial/Refine Author。
