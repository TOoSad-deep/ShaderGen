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
| F09 | 用户上传 PNG 后，系统可以在 WebGL1 无贴图契约下执行有界的分析、生成、真实渲染、评分和修订闭环，并返回 current_best GLSL 与证据。 | `make check && uv run pytest -q tests/integration_tests && npm --prefix frontend run e2e:procedural-v1 && npm --prefix frontend run e2e:memory && make benchmark-ai-off && make benchmark-gate BENCHMARK_OUTPUT=<run-dir> HUMAN_REVIEW=<review.json>` | active | 2026-07-14：M0–M5 工程实现和正式人工盲评均已完成；M6.0 进一步修复阶段预算、确定性恢复、未评分 WebGL fallback、类型化错误、原子终态账本、安全日志、Legacy 默认/服务端 timeout，并以真实 Chromium 验证正常评分和 fallback UI。质量结论未改变：`m5-20260713-balanced-v3` 的 compile/static/traceability/final=current_best/单调性均为 10/10，但 initial-final 改善仅 1/10，低于 70%；pink-gel bbox/global/四个 ROI 失败。人工评审 10/10 完整，但 final 偏好只有 1/10，低于 50%。自动与人工门禁均失败，最终 gate 为 `failed`、灰度 no-go，F09 不得标记 passing。 |
