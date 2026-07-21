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
| H01 | 新 agent 会话可以只通过仓库文件理解项目用途、命令、目标架构、决策、当前进度和下一个功能。 | `make check` | passing | 2026-07-21：`make check` 验证 421 个单元测试通过，docs-check、LangGraph validate（2 个 graph）和前端生产构建均成功；架构与进度已同步 `scene_mvp` 当前能力和 prepared/CMA/模型缺口。 |
| H02 | Node Lab Harness 可以通过通用 Provider 隔离诊断生产 Node target、场景流水线、Renderer、HTTP transport、五个模型角色 fixture 和本地工作台，不复制生产节点语义。 | `make benchmark-node-lab-ai-off && make benchmark-node-lab-model && make test-node-lab-ui` | passing | 2026-07-16：三项门禁均已实际通过；模型角色只使用离线 fixture，工作台只连接假 API，未调用真实模型。H02 通过不改变 F09 的质量 gate。 |
| F01 | 用户可以为 Shader 任务提交 Idea、需求、参考设计和测试规划输入。 | `uv run pytest tests/unit_tests/test_shader_task_input_contract.py && npm --prefix frontend run build` | not_started | 来自架构 SVG 的用户输入层。 |
| F02 | Routing 和 Agent 分析可以从用户输入产出结构化 Intent IR。 | `uv run pytest tests/unit_tests/test_routing.py tests/unit_tests/test_intent_ir.py` | not_started | 来自架构 SVG 的核心处理层。 |
| F03 | DSL 节点图和 Renderer 可以产出 GLSL 以及可渲染画面。 | `uv run pytest tests/unit_tests/test_dsl_renderer.py && npm --prefix frontend run build` | not_started | 来自架构 SVG 的核心处理层。 |
| F04 | Oracle 可以用全局评分以及局部颜色、形状、边缘损失评价渲染结果。 | `uv run pytest tests/unit_tests/test_oracle.py` | not_started | 来自架构 SVG 的核心处理层。 |
| F05 | Search Engine 可以调优 Shader 参数，并记录 VLM/HITL 评审结果。 | `uv run pytest tests/integration_tests/test_search_review_store.py` | not_started | 来自架构 SVG 的核心处理层和数据评测层。 |
| F08 | 同一 project_id 的 V1 运行可以复用经过确定性验证的策略记忆，不同项目互不泄漏，并可清除 checkpoint 与长期记忆。 | `make test-memory-postgres && uv run pytest tests/integration_tests/test_png_to_shader_v1_graph.py tests/integration_tests/test_png_to_shader_v1_api.py && npm --prefix frontend run e2e:memory && make check` | passing | 2026-07-15：V1 Graph/Service 覆盖策略晋升、项目隔离与清除；隔离 PostgreSQL 资源重建验收、Memory 浏览器 E2E 和主干验证通过。旧 Review Memory 仅保留只读兼容、不再产生新记录；清除操作兼容删除旧 Graph 遗留的裸 project checkpoint。 |
| F09 | 用户上传 PNG 后，系统可以在 WebGL1 无贴图契约下执行有界的分析、生成、真实渲染、评分和修订闭环，并返回 current_best GLSL 与证据。 | `make check && uv run pytest -q tests/integration_tests && npm --prefix frontend run e2e:procedural-v1 && npm --prefix frontend run e2e:memory && make benchmark-ai-off && make benchmark-gate BENCHMARK_OUTPUT=<run-dir> HUMAN_REVIEW=<review.json>` | active | 2026-07-21：并行 `scene_mvp` 已贯通 Graph、真实 Chromium、Backend、Artifact 和 Kimi 前端；粉球 HTTP smoke 为 MAE `0.057279`、1 draw、0 模型调用，manifest 可读取，`make check` 通过。该结果只证明最小垂直链路，prepared/CMA/模型仍未实现；既有正式 run 的人工偏好仍为 30%（门槛 50%），因此 F09 继续 active、灰度 no-go。 |
