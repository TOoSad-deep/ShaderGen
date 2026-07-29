# Progress

当前 active 功能为 F09：Layered Direct GLSL。

已完成：

- 产品链路固定为 LayerPlan → LayeredShaderSpec → ShaderProgramSpec → WebGL1。
- 父运行使用最多 3 个 fresh Direct attempt。
- Layered 输入不再设置静态 uniform 总数/分量上限，最终容量由本机 WebGL1
  compile/link/draw 决定。
- 已移除旧模型直出 ProgramSpec、ShaderGraph、Graph runtime、policy、
  fallback、promotion、shadow、Memory 和 Node Lab 运行代码。
- 当前公开 Artifact 只有 final-render、metrics、manifest。
- 成功的私有 Direct attempt 按顺序保留高层 Initial/Refine 渲染图；参数
  搜索试参图不进入该历史，未来只允许保留调优前与最终最优边界快照。

历史材料保留在 `docs/archive/` 和 `docs/evidence/`，不属于当前运行闭包。
