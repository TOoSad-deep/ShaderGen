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

历史材料保留在 `docs/archive/` 和 `docs/evidence/`，不属于当前运行闭包。
