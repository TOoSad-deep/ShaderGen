# PNG-to-Shader V1 确定性 Node 架构

本目录保存 PNG-to-Shader V1 的确定性 Graph Node 工厂及其内部阶段实现。Graph 和 Node Lab 都只从 `__init__.py` 导入公开工厂，不能各自维护一套业务语义。

## 模块职责

- `__init__.py`：稳定公开入口，只导出 Node 工厂、Renderer 协议和运行时类型。
- `runtime.py`：跨确定性 Node 共享的预算读取、Artifact 访问、候选记录转换、证据摘要、Renderer registry 和时间边界。
- `preparation.py`：初始化 run、规范化输入、测量目标和保存 VisualAnalysis。
- `candidates.py`：物化模型/确定性候选、准备 measurement seed 和 compile repair。
- `render_evaluate.py`：`render_and_evaluate` Node 的薄编排入口，按顺序调用校验、渲染和评分阶段。
- `render_evaluate_validation.py`：验证 Candidate/GLSL 证据绑定，执行静态 Validator 和白名单确定性修复。
- `render_evaluate_rendering.py`：执行真实 WebGL1 渲染，持久化 compile/render 证据并归类 Renderer 或编译失败。
- `render_evaluate_scoring.py`：在 finalize 预留时间内调用确定性 Evaluator，持久化 metrics 或形成明确的未评分降级结果。
- `selection.py`：调用纯 Selector、重载 current_best Artifact 和按轮次保存 VisualReview。
- `finalization.py`：选择已评分 best 或合法的已验证 fallback，复制 final Artifact 并关闭 run 级 Renderer。

`render_evaluate_validation.py`、`render_evaluate_rendering.py` 和 `render_evaluate_scoring.py` 是一个 Graph Node 内部的确定性阶段，不是新的 Graph Node，也不出现在路由表或 Node Lab descriptor 中。

## 边界规则

- Graph Node 名称、直接边、条件边、路由结果和 `current_best` 安全语义由 `app/graphs/png_to_shader_v1_graph.py` 及其架构文档定义；本目录内部拆分不得静默改变这些契约。
- Graph 与 Node Lab 必须调用 `__init__.py` 暴露的同一工厂。Node Lab 只负责 JSON/Artifact 形状适配，并递归冻结本目录全部 Python 源码的 benchmark 指纹。
- Artifact 写入、hash 绑定、预算保留和失败降级属于生产 Node 语义；不得移入 Graph 路由、Backend 或 Node Lab Adapter。
- 只有两个以上模块共同使用的运行时协议和无业务分支 helper 才进入 `runtime.py`；单阶段逻辑保留在对应职责模块。
- 新增、删除或重命名真正的 Graph Node 时，仍必须同步 Graph Builder 上方 ASCII 图、`app/graphs/ARCHITECTURE.md` Mermaid、路由表和验证证据。
