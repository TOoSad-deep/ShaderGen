# 功能清单

状态值：`not_started`、`active`、`blocked`、`passing`。

- 同一时间最多只有一个功能可以是 `active`。
- 只有验证命令通过后，功能才能变为 `passing`。
- 历史 V1 和 V2-V5 方案只在决策、进度归档与证据 registry 中追溯；当前 Node Lab 仅指 Pipeline 无关的独立开发工具，不恢复旧 V1 产品链路。

| id | 行为 | 验证 | 状态 | 证据 |
|---|---|---|---|---|
| H01 | 新 agent 会话可以通过仓库文件理解当前最小骨架、命令、边界和下一步。 | `make check` | passing | 2026-07-26：单元测试通过，当前只注册 1 个 graph；旧 V1 产品/benchmark 运行面保持删除，通用 Node Lab 作为独立开发工具恢复，Memory/checkpoint 保留但休眠。完整验证基线见 `PROGRESS.md`。 |
| H02 | 开发者可以通过独立 Node Lab 服务和 `/lab` 工作台接入任意显式 Provider，而产品 Backend 不隐式加载 Node Lab。 | `uv run pytest tests/unit_tests/test_node_lab_schemas.py tests/unit_tests/test_node_lab_service.py && npm --prefix frontend run build` | passing | 2026-07-26：通用 `nodelab`、独立 `nodelab_service`、受信任 factory、空安全默认 Application 与工作台构建通过；18 个聚焦测试通过。旧 V1 Adapter、manifest 与 benchmark 脚本未恢复。 |
| F01 | 用户可以为 Shader 任务提交 Idea、需求、参考设计和测试规划输入。 | `uv run pytest tests/unit_tests/test_shader_task_input_contract.py && npm --prefix frontend run build` | not_started | 来自目标架构的用户输入层。 |
| F02 | Routing 和 Agent 分析可以从用户输入产出结构化 Intent IR。 | `uv run pytest tests/unit_tests/test_routing.py tests/unit_tests/test_intent_ir.py` | not_started | 来自目标架构的核心处理层。 |
| F03 | DSL 节点图和 Renderer 可以产出 GLSL 以及可渲染画面。 | `uv run pytest tests/unit_tests/test_dsl_renderer.py && npm --prefix frontend run build` | not_started | 2026-07-24 `shader_graph_v1` 已作为 F09 内部产品表示接入；F03 仍不单独启动，避免与唯一 active 功能并行。 |
| F04 | Oracle 可以用全局评分以及局部颜色、形状、边缘损失评价渲染结果。 | `uv run pytest tests/unit_tests/test_oracle.py` | not_started | 来自目标架构的核心处理层。 |
| F05 | Search Engine 可以调优 Shader 参数，并记录 VLM/HITL 评审结果。 | `uv run pytest tests/integration_tests/test_search_review_store.py` | not_started | 来自目标架构的数据评测层。 |
| F09 | 用户上传 PNG 后，scene_mvp 最小骨架执行感知、ShaderGraph Author、specialized WebGL1 编译/渲染、复合评分、node-id 有界优化和 typed layer Refine，并返回 GLSL、最终 Render、指标与 trace。 | `make check && uv run pytest -q tests/integration_tests && make test-scene-mvp-ui` | active | 2026-07-24：`shader_graph_v1` 已成为默认产品真相源；Prompt v1_2、感知直接产品 fallback、typed layer patch、CandidateSnapshot、多 program cache、manifest/API/Layer inspector 已贯通，满 8 Layer/transform/CSG/层序真实 WebGL 验证通过。合并保留 D072/D073 的 strict total-loss 诊断结论，以及 D074/D075 的 legacy MinScene replay 实现和 12/32 小样本证据；D076 明确这些结果不得外推为当前 ShaderGraph 质量或预算。生产 `dashscope:qwen3.7-plus` 直连合法但 Initial 仍输给 fallback，当前仍缺 ShaderGraph durable benchmark 和独立人工门禁。 |
