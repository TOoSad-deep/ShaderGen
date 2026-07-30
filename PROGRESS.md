# Progress

当前 active 功能为 F09：Layered Direct GLSL。

已完成：

- 产品链路固定为 LayerPlan → LayeredShaderSpec → ShaderProgramSpec → WebGL1。
- 父运行使用最多 3 个 fresh Direct attempt。
- Layered 输入不再设置静态 uniform 总数/分量上限，最终容量由本机 WebGL1
  compile/link/draw 决定。
- 已移除旧模型直出 ProgramSpec、ShaderGraph、Graph runtime、policy、
  fallback、promotion、shadow、Memory 和 Node Lab 运行代码。
- 当前 Layered Direct attempt 已使用新的 LangGraph 编排；删除的是旧
  `png_to_shader_min` graph，不是当前 LayerPlan 流程。
- reference、LayerPlan、Initial/Refine、compile、validate、prepare、draw、
  receipt/attestation、evaluate、incumbent selection 和 finalize 均为显式 node。
- Refine 已具备 MAE/loss 双 target 早停、target-relative excess 双目标
  selection、重复 Patch 检测、失败反馈、`min_delta_mae` /
  `min_delta_loss` / `patience` 收敛，以及统一为 WebGL 左下原点的 residual。
- `tunable_manifest` 已驱动受预算约束的确定性 uniform-only 参数搜索；
  搜索候选沿用完整的真实 WebGL1 验证、证明和评估闭环，候选级非硬失败
  只淘汰当前 probe 并继续有界搜索。
- Initial/Refine 可附带不进入 Shader/Patch hash 的 `optimization_focus`；
  可信层按真实 Layered/Program pair 校验目标层与参数分量白名单，无效建议
  安全回退到既有自动选层。局部 ROI 指标复用同一次 beauty render，不增加
  draw，且全局 MAE/loss 仍是唯一 `current_best` 接受边界。
- Layered compiler 已为 Beauty program 注入可信、默认关闭的 role alpha
  诊断模式；Refine 前可复用 incumbent 的 prepared program，以最多两次
  有界 draw 输出 subject/highlight/detail 与 shadow/glow/background 的独立
  alpha mask。Mask 只作为私有多模态 shape/coverage 反馈并绑定输入身份，
  不进入模型 Shader/Patch 语义、公开 metrics 或 `current_best` 接受边界。
- 当前公开 Artifact 只有 final-render、metrics、manifest。
- 成功的私有 Direct attempt 按顺序保留高层 Initial/Refine 渲染图；参数
  搜索试参图不进入该历史，未来只允许保留调优前与最终最优边界快照。

历史材料保留在 `docs/archive/` 和 `docs/evidence/`，不属于当前运行闭包。
