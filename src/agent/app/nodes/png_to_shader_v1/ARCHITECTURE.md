# PNG-to-Shader V1 Node 架构

本目录是 PNG-to-Shader V1 全部 Node 工厂及其支持实现的功能命名空间。模型角色、确定性事实层和 Node Lab Provider 都归属这里；Graph、Service 和脚本不再从 `nodes/` 根目录拼装零散 V1 模块。`decide_after_render`、`decide_after_selection` 同时承载纯路由规则，继续与条件边函数共同位于 `graphs/png_to_shader_v1_routing.py`，不伪装成可复用公共 Node。

## 目录职责

- `__init__.py`：V1 Node 的稳定公开入口。Graph 只从这里导入生产工厂、模型配置和运行时协议；它不反向导入 `integrations/`。
- `model/`：VisualAnalysis、三模式 ShaderAuthor、VisualCritic、结构化输出修复和模型预算包装器。角色 Node 只依赖 `LLMGateway`、V1 契约、Prompt、Parser 和消息 helper，不依赖 Renderer、Evaluator、Selector 或 Artifact Store。
- `deterministic/`：Context、运行生命周期、候选物化、Validator、真实 WebGL1 Renderer、Evaluator、Selector、current_best 回载、finalize 和验证后策略晋升。
- `integrations/node_lab/`：V1 生产 Node 对通用 Node Lab 的 Provider、descriptor 和受控 Adapter。它可以依赖 `model/`、`deterministic/` 与生产 routing；Harness 内核不得反向依赖本目录。

## 确定性模块

- `deterministic/runtime.py`：共享预算读取、Artifact 访问、候选记录转换、证据摘要、Renderer registry 和时间边界。
- `deterministic/context.py`：读取候选 Memory 并调用纯 GSSC Context Builder。
- `deterministic/preparation.py`：初始化 run、规范化输入、测量目标和保存 VisualAnalysis。
- `deterministic/candidates.py`：物化模型/确定性候选、准备 measurement seed 和 compile repair。
- `deterministic/render_evaluate.py`：`render_and_evaluate` Node 的薄编排入口，按顺序调用校验、渲染和评分阶段。
- `deterministic/render_evaluate_validation.py`：验证 Candidate/GLSL 证据绑定，执行静态 Validator 和白名单确定性修复。
- `deterministic/render_evaluate_rendering.py`：执行真实 WebGL1 渲染，持久化 compile/render 证据并归类 Renderer 或编译失败。
- `deterministic/render_evaluate_scoring.py`：在 finalize 预留时间内调用确定性 Evaluator，持久化 metrics 或形成明确的未评分降级结果。
- `deterministic/selection.py`：调用纯 Selector、重载 current_best Artifact 和按轮次保存 VisualReview。
- `deterministic/finalization.py`：选择已评分 best 或合法的已验证 fallback，复制 final Artifact 并关闭 run 级 Renderer。
- `deterministic/promotion.py`：构造无副作用预览，并只把确定性验证过的 current_best 策略晋升 Memory。

`render_evaluate_validation.py`、`render_evaluate_rendering.py` 和 `render_evaluate_scoring.py` 是一个 Graph Node 内部阶段，不是新的 Graph Node，也不出现在路由表或 Node Lab descriptor 中。

## 边界规则

- Graph Node 名称、直接边、条件边、路由结果和 `current_best` 安全语义由 `app/graphs/png_to_shader_v1_graph.py` 及其架构文档定义；本目录重组不得静默改变这些契约。
- 外部组合根使用顶层 `__init__.py`；包内 Adapter 可以从 `model/`、`deterministic/` 导入受控接口，但不得复制 Node 语义。
- `model/` 不依赖 `deterministic/`、`integrations/` 或 Graph Builder；`deterministic/` 不依赖 `model/`、`integrations/` 或 Graph Builder；`integrations/` 不得被前两者或顶层入口反向导入。
- Artifact 写入、hash 绑定、预算保留和失败降级属于生产 Node 语义；不得移入 Graph 路由、Backend 或 Node Lab Adapter。
- Node Lab benchmark 递归冻结本目录全部 Python 源码；descriptor 的 `source_ref` 必须指向真实职责模块，不能继续引用已删除的根级兼容路径。
- 新增、删除或重命名真正的 Graph Node 时，仍必须同步 Graph Builder 上方 ASCII 图、`app/graphs/ARCHITECTURE.md` Mermaid、路由表和验证证据。
