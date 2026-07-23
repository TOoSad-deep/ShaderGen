# Nodes 架构

当前只有 `png_to_shader_min/` 产品 Node 命名空间，包含 `scene_mvp` 的 12 节点运行时与 Model Author helper。

- Node 通过构造参数接收 `agent.app.contracts.llm.LLMGateway`，不得直接依赖 provider 实现。
- Initial 生成完整 Scene；Refine 只从 `current_best` 派生一个 typed patch。
- 模型调用、结构修复或解析失败安全回退，不保存原始响应，也不覆盖 best。
- Patch trace 只允许 operation、feature id/type、SHA-256、metric delta、拒绝原因和耗时。
- Node 不决定全局流程，不持有数据库连接，不原地修改 State。
- Prompt 从 `app/prompts/` 加载，结构化输出由 `app/parsers/png_to_shader_min.py` 解析。
- Renderer、Evaluator、Optimizer 和 Store 只通过 ShaderForge typed 子包公共入口使用。
