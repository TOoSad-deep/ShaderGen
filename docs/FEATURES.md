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
| H01 | 新 agent 会话可以只通过仓库文件理解项目用途、命令、目标架构、决策、当前进度和下一个功能。 | `make check` | passing | 2026-07-22：`make check` 验证 454 个单元测试通过，docs-check、LangGraph validate（2 个 graph）和前端生产构建均成功；架构与进度已同步 `scene_mvp` v2 packed feature、WebGL1 uniform 边界、分档预算、复合 loss 及 CMA/真实模型质量缺口。 |
| H02 | Node Lab Harness 可以通过通用 Provider 隔离诊断生产 Node target、场景流水线、Renderer、HTTP transport、五个模型角色 fixture 和本地工作台，不复制生产节点语义。 | `make benchmark-node-lab-ai-off && make benchmark-node-lab-model && make test-node-lab-ui` | passing | 2026-07-16：三项门禁均已实际通过；模型角色只使用离线 fixture，工作台只连接假 API，未调用真实模型。H02 通过不改变 F09 的质量 gate。 |
| F01 | 用户可以为 Shader 任务提交 Idea、需求、参考设计和测试规划输入。 | `uv run pytest tests/unit_tests/test_shader_task_input_contract.py && npm --prefix frontend run build` | not_started | 来自架构 SVG 的用户输入层。 |
| F02 | Routing 和 Agent 分析可以从用户输入产出结构化 Intent IR。 | `uv run pytest tests/unit_tests/test_routing.py tests/unit_tests/test_intent_ir.py` | not_started | 来自架构 SVG 的核心处理层。 |
| F03 | DSL 节点图和 Renderer 可以产出 GLSL 以及可渲染画面。 | `uv run pytest tests/unit_tests/test_dsl_renderer.py && npm --prefix frontend run build` | not_started | 来自架构 SVG 的核心处理层。 |
| F04 | Oracle 可以用全局评分以及局部颜色、形状、边缘损失评价渲染结果。 | `uv run pytest tests/unit_tests/test_oracle.py` | not_started | 来自架构 SVG 的核心处理层。 |
| F05 | Search Engine 可以调优 Shader 参数，并记录 VLM/HITL 评审结果。 | `uv run pytest tests/integration_tests/test_search_review_store.py` | not_started | 来自架构 SVG 的核心处理层和数据评测层。 |
| F08 | 同一 project_id 的 V1 运行可以复用经过确定性验证的策略记忆，不同项目互不泄漏，并可清除 checkpoint 与长期记忆。 | `make test-memory-postgres && uv run pytest tests/integration_tests/test_png_to_shader_v1_graph.py tests/integration_tests/test_png_to_shader_v1_api.py && npm --prefix frontend run e2e:memory && make check` | passing | 2026-07-15：V1 Graph/Service 覆盖策略晋升、项目隔离与清除；隔离 PostgreSQL 资源重建验收、Memory 浏览器 E2E 和主干验证通过。旧 Review Memory 仅保留只读兼容、不再产生新记录；清除操作兼容删除旧 Graph 遗留的裸 project checkpoint。 |
| F09 | 用户上传 PNG 后，系统可以在 WebGL1 无贴图契约下执行有界的分析、生成、真实渲染、评分和修订闭环，并返回 current_best GLSL 与证据。 | `make check && uv run pytest -q tests/integration_tests && npm --prefix frontend run e2e:procedural-v1 && npm --prefix frontend run e2e:memory && make test-scene-mvp-ui && make benchmark-ai-off && make benchmark-gate BENCHMARK_OUTPUT=<run-dir> HUMAN_REVIEW=<review.json>` | active | 2026-07-23：显式 `scene_mvp` 已升级为 v3 Scene/template/metric，并补齐 run 冻结/独立实验身份、配置指纹、Patch 安全指纹、worst-tile signed residual、最近拒绝历史和 typed Patch 有界成熟；单候选最多 12 次现有 draw，只有 matured loss 严格改善才原子提交，重复/非法/Renderer 失败不能污染 best。Graph 拓扑和单 run 单 prepared program 不变，Refine 后不再重复完整 base/feature sweep，high 合法路径为 65 步、注入 69。真实 Chromium 既有门禁验证三颜色场、六 feature、四槽及 baked/prepared 像素语义；固定 7 例既有 deterministic fallback 对照为 6/7 改善。当前 `0.04/0.02 + 640/9/9` 是独立实验配置，真实模型固定 7 例、匿名人工质量门禁、geometry 语义修正及 CMA-ES/2000 draw 尚未完成；既有正式 run 人工偏好仍为 30%（门槛 50%），因此 F09 继续 active、灰度 no-go。 |
