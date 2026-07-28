# Nodes 架构

当前只有 `png_to_shader_min/` 产品 Node 命名空间，包含 `scene_mvp` 的 12 节点运行时、ShaderGraph 产品运行时与 Model Author helper。

- Node 通过构造参数接收 `agent.app.contracts.llm.LLMGateway`，不得直接依赖 provider 实现。
- 产品 Initial 生成完整 `ShaderDocument`；Refine 只从 `current_best.document` 派生一个绑定 base hash 的 typed layer patch。
- 模型调用、结构修复或解析失败安全回退，不保存原始响应，也不覆盖 best。
- Patch trace 只允许 operation、layer id、节点类型集合、SHA-256、metric delta、拒绝原因和耗时。
- Node 不决定全局流程，不持有数据库连接，不原地修改 State。
- Prompt 从 `app/prompts/` 加载，结构化输出由 `app/parsers/png_to_shader_min.py` 解析。
- Renderer、Evaluator、Optimizer 和 Store 只通过 ShaderForge typed 子包公共入口使用。
- `shader_graph_runtime.py` 沿用现有 12 节点拓扑：感知阶段直接产出 ShaderDocument fallback（`fallback_shader_graph`），产品热路径不再经过 MinScene 中间转换；模型文档与 fallback 都经真实 Compiler/Renderer 仲裁。参数优化按稳定 node/layer block 工作，结构 Patch 每轮只分配一个 raw draw 并严格回滚。
- `current_best` 使用冻结 `ShaderGraphCandidateSnapshot`，Prepared handle 只保存在 `MinRendererRegistry` 的 run-scoped program cache。`finalize` 输出权威 `shader-graph.json`、specialized WebGL1 GLSL、Render、metrics 和 manifest。
- `shader_graph_shadow.py` 仍保留给显式 legacy Builder 测试与兼容审计；ShaderGraph engine 组合根不运行二次 shadow，也不把 MinScene 固定模板作为真相源。
- `shader_graph_author.py` 加载 Initial/Refine Prompt 与严格 Schema；契约与解析由 `app/contracts/shader_graph_author.py` 和 `app/parsers/shader_graph_author.py` 承担，调用继续复用 `invoke_min_author` 的有界调用与结构修复。
- 结构修复消息除稳定错误码外，只接收 Parser 提取的脱敏校验位置、类型和安全消息；模型原始输出仍作为不可信修复输入，不写入 State、日志或 Artifact。
