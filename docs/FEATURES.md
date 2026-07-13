# 功能清单

状态值：`not_started`、`active`、`blocked`、`passing`。

规则：

- 同一时间最多只有一个功能可以是 `active`。
- 只有验证命令通过后，功能才能变为 `passing`。
- `passing` 行必须包含证据。
- `blocked` 行必须在 `evidence` 中写明阻塞原因和解除条件。
- `not_started` 行的验证列写实现该功能时必须创建并通过的目标命令。

| id | 行为 | 验证 | 状态 | 证据 |
|---|---|---|---|---|
| H01 | 新 agent 会话可以只通过仓库文件理解项目用途、命令、目标架构、决策、当前进度和下一个功能。 | `make check` | passing | 2026-07-08：`make check` 已通过：单元测试通过、docs-check 通过、LangGraph validate 发现 2 个 graph、前端构建通过。 |
| F01 | 用户可以为 Shader 任务提交 Idea、需求、参考设计和测试规划输入。 | `uv run pytest tests/unit_tests/test_shader_task_input_contract.py && npm --prefix frontend run build` | not_started | 来自架构 SVG 的用户输入层。 |
| F02 | Routing 和 Agent 分析可以从用户输入产出结构化 Intent IR。 | `uv run pytest tests/unit_tests/test_routing.py tests/unit_tests/test_intent_ir.py` | not_started | 来自架构 SVG 的核心处理层。 |
| F03 | DSL 节点图和 Renderer 可以产出 GLSL 以及可渲染画面。 | `uv run pytest tests/unit_tests/test_dsl_renderer.py && npm --prefix frontend run build` | not_started | 来自架构 SVG 的核心处理层。 |
| F04 | Oracle 可以用全局评分以及局部颜色、形状、边缘损失评价渲染结果。 | `uv run pytest tests/unit_tests/test_oracle.py` | not_started | 来自架构 SVG 的核心处理层。 |
| F05 | Search Engine 可以调优 Shader 参数，并记录 VLM/HITL 评审结果。 | `uv run pytest tests/integration_tests/test_search_review_store.py` | not_started | 来自架构 SVG 的核心处理层和数据评测层。 |
| F06 | Agent/后端在线 Review 节点可以基于原图、当前渲染图和 GLSL 输出渲染评估与代码修改建议。 | `make check && uv run pytest tests/integration_tests && uv run ruff check src/agent backend tests scripts` | passing | 2026-07-08：验证通过。单元测试通过；docs-check 通过；集成测试通过；LangGraph validate 通过并发现 2 个 graph；前端构建通过；ruff 通过。 |
| F07 | 浏览器端 Review 闭环可以完成 canvas 截图 -> review API -> UI 展示，并在失败时展示可理解错误。 | `npm --prefix frontend run build && npm --prefix frontend run e2e:review` | not_started | 2026-07-08：从 F06 拆出的浏览器自动化缺口；实现时需要补 Playwright 或等价浏览器集成检查。 |
| F08 | 同一 project_id 的 Shader 生成与 Review 可以复用经过筛选的任务记忆和项目记忆，不同项目互不泄漏，并可清除记忆。 | `make test-memory-postgres && uv run pytest tests/integration_tests/test_shader_memory_flow.py && npm --prefix frontend run e2e:memory && make check` | passing | 2026-07-13：隔离临时 PostgreSQL 资源重建测试通过且测试库已删除；Memory Flow 集成测试通过；Playwright CLI 浏览器 E2E 通过；`make check`、ruff 和 docs-check 通过。 |
| F09 | 用户上传 PNG 后，系统可以在 WebGL1 无贴图契约下执行有界的分析、生成、真实渲染、评分和修订闭环，并返回 current_best GLSL 与证据。 | `uv run pytest tests/unit_tests/test_png_to_shader_v1_*.py tests/unit_tests/test_current_best_selector.py && uv run pytest tests/integration_tests/test_webgl1_renderer.py tests/integration_tests/test_png_to_shader_v1_graph.py && npm --prefix frontend run e2e:png-to-shader-v1 && make check` | active | 2026-07-13：M0 已完成并通过定向测试、全量单元测试、普通集成测试、ruff、docs-check、LangGraph validate 和前端构建；M1–M5 尚未实现，不得标记 passing。 |
