# 功能清单

状态值：`not_started`、`active`、`blocked`、`passing`。

- 同一时间最多只有一个功能可以是 `active`。
- 只有验证命令通过后，功能才能变为 `passing`。
- 历史 V1、Node Lab 和 V2-V5 方案只在决策、进度归档与证据 registry 中追溯，不作为当前功能。

| id | 行为 | 验证 | 状态 | 证据 |
|---|---|---|---|---|
| H01 | 新 agent 会话可以通过仓库文件理解当前最小骨架、命令、边界和下一步。 | `make check` | passing | 2026-07-23：单元测试通过，当前只注册 1 个 graph；旧 V1/Node Lab 运行面已删除，Memory/checkpoint 保留但休眠，历史本地产物已清理。完整验证基线见 `PROGRESS.md`。 |
| F01 | 用户可以为 Shader 任务提交 Idea、需求、参考设计和测试规划输入。 | `uv run pytest tests/unit_tests/test_shader_task_input_contract.py && npm --prefix frontend run build` | not_started | 来自目标架构的用户输入层。 |
| F02 | Routing 和 Agent 分析可以从用户输入产出结构化 Intent IR。 | `uv run pytest tests/unit_tests/test_routing.py tests/unit_tests/test_intent_ir.py` | not_started | 来自目标架构的核心处理层。 |
| F03 | DSL 节点图和 Renderer 可以产出 GLSL 以及可渲染画面。 | `uv run pytest tests/unit_tests/test_dsl_renderer.py && npm --prefix frontend run build` | not_started | 2026-07-24 `shader_graph_v1` 已作为 F09 内部产品表示接入；F03 仍不单独启动，避免与唯一 active 功能并行。 |
| F04 | Oracle 可以用全局评分以及局部颜色、形状、边缘损失评价渲染结果。 | `uv run pytest tests/unit_tests/test_oracle.py` | not_started | 来自目标架构的核心处理层。 |
| F05 | Search Engine 可以调优 Shader 参数，并记录 VLM/HITL 评审结果。 | `uv run pytest tests/integration_tests/test_search_review_store.py` | not_started | 来自目标架构的数据评测层。 |
| F09 | 用户上传 PNG 后，scene_mvp 最小骨架执行感知、ShaderGraph Author、specialized WebGL1 编译/渲染、复合评分、node-id 有界优化和 typed layer Refine，并返回 GLSL、最终 Render、指标与 trace。 | `make check && uv run pytest -q tests/integration_tests && make test-scene-mvp-ui` | active | 2026-07-24：`shader_graph_v1` 已成为默认产品真相源；Prompt v1_2、感知直接产品 fallback、typed layer patch、CandidateSnapshot、多 program cache、manifest/API/Layer inspector 已贯通，满 8 Layer/transform/CSG/层序真实 WebGL 验证通过。生产 `dashscope:qwen3.7-plus` 直连合法但 Initial 仍输给 fallback；参数优化转为跨分支 TODO，当前仍缺 durable benchmark 和独立人工门禁。 |
