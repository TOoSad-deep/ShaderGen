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
| H01 | 新 agent 会话可以只通过仓库文件理解项目用途、命令、目标架构、决策、当前进度和下一个功能。 | `make check` | passing | 2026-07-15：`make check` 已通过：单元测试通过、docs-check 通过、LangGraph validate 发现 1 个 graph、前端构建通过。 |
| H02 | Node Lab Harness 可以通过通用 Provider 隔离诊断生产 Node target、场景流水线、Renderer、HTTP transport、五个模型角色 fixture 和本地工作台，不复制生产节点语义。 | `make benchmark-node-lab-ai-off && make benchmark-node-lab-model && make test-node-lab-ui` | passing | 2026-07-16：三项门禁均已实际通过；模型角色只使用离线 fixture，工作台只连接假 API，未调用真实模型。H02 通过不改变 F09 的质量 gate。 |
| F01 | 用户可以为 Shader 任务提交 Idea、需求、参考设计和测试规划输入。 | `uv run pytest tests/unit_tests/test_shader_task_input_contract.py && npm --prefix frontend run build` | not_started | 来自架构 SVG 的用户输入层。 |
| F02 | Routing 和 Agent 分析可以从用户输入产出结构化 Intent IR。 | `uv run pytest -q tests/unit_tests/test_target_measurements_v2_pipeline.py tests/unit_tests/test_v2_1_measurement_validation_gate.py tests/unit_tests/test_request_constraint_builder.py tests/unit_tests/test_visual_interpretation_v2_prompt.py tests/unit_tests/test_visual_interpretation_v2_artifacts.py tests/unit_tests/test_intent_ir.py tests/unit_tests/test_v2_1_intent_gate.py tests/unit_tests/test_run_v2_1_intent_benchmark.py tests/unit_tests/test_runtime_target_structure_verifier.py tests/unit_tests/test_runtime_structure_artifact_recovery.py tests/unit_tests/test_candidate_artifact_recovery.py tests/unit_tests/test_runtime_admission_adapter.py` | passing | 2026-07-20：目标命令与 `make check`（668 tests）通过；51/51 Intent conformance、required-layer 十项闭集、runtime structure 重放、Candidate/provenance 全闭包恢复及 sealed Selector adapter 已落地。V2.2 typed compilation/evaluation 尚未存在时 adapter 明确 fail closed，production admission 未启用；fixture/no-model 结果不代表真实 VLM 视觉真值。 |
| F03 | DSL 节点图和 Renderer 可以产出 GLSL 以及可渲染画面。 | `uv run pytest -q tests/unit_tests/test_effect_genome_v2.py tests/unit_tests/test_seed_plan_expander_v2.py tests/unit_tests/test_deterministic_compiler_v2.py tests/unit_tests/test_v2_typed_candidate_semantics.py tests/unit_tests/test_png_to_shader_v2_graph_runtime.py tests/unit_tests/test_candidate_artifact_recovery.py tests/unit_tests/test_rendered_structure_evidence_v2.py tests/unit_tests/test_v2_3_actual_chromium_replay.py tests/unit_tests/test_v2_3_rendered_structure_gate.py tests/unit_tests/test_run_v2_3_rendered_structure_benchmark.py tests/integration_tests/test_png_to_shader_v2_chromium_graph.py tests/integration_tests/test_v2_webgl_rendering.py && npm --prefix frontend run build` | active | 2026-07-21：State v4/Graph 2.4 保持稳定；已补齐 `stable_instance_ordinal_first_match_v1`、Diagnostic/RenderPlan V3、Rendered Evidence/Verification V4、metric v3.2，以及可从 source alpha 重放的 12/18 段 radial-segment evidence。Measurements/Hypothesis/Intent、Candidate loader、Service resume 和 formal gate 已同步升版并可运行；单例真实 Chromium smoke 通过。旧 strict-v3 结果因 breaking schema 只保留历史诊断，尚未运行新 51 例效果门禁；F03 继续 active，下一步是 segment primitive 与质量优化。production admission/release access 仍关闭。 |
| F04 | Oracle 可以用全局评分以及局部颜色、形状、边缘损失评价渲染结果。 | `uv run pytest tests/unit_tests/test_oracle.py` | not_started | 来自架构 SVG 的核心处理层。 |
| F05 | Search Engine 可以调优 Shader 参数，并记录 VLM/HITL 评审结果。 | `uv run pytest tests/integration_tests/test_search_review_store.py` | not_started | 来自架构 SVG 的核心处理层和数据评测层。 |
| F08 | 同一 project_id 的 V1 运行可以复用经过确定性验证的策略记忆，不同项目互不泄漏，并可清除 checkpoint 与长期记忆。 | `make test-memory-postgres && uv run pytest tests/integration_tests/test_png_to_shader_v1_graph.py tests/integration_tests/test_png_to_shader_v1_api.py && npm --prefix frontend run e2e:memory && make check` | passing | 2026-07-15：V1 Graph/Service 覆盖策略晋升、项目隔离与清除；隔离 PostgreSQL 资源重建验收、Memory 浏览器 E2E 和主干验证通过。旧 Review Memory 仅保留只读兼容、不再产生新记录；清除操作兼容删除旧 Graph 遗留的裸 project checkpoint。 |
| F09 | 用户上传 PNG 后，系统可以在 WebGL1 无贴图契约下执行有界的分析、生成、真实渲染、评分和修订闭环，并返回 current_best GLSL 与证据。 | `make check && uv run pytest -q tests/integration_tests && npm --prefix frontend run e2e:procedural-v1 && npm --prefix frontend run e2e:memory && make benchmark-ai-off && make benchmark-gate BENCHMARK_OUTPUT=<run-dir> HUMAN_REVIEW=<review.json>` | blocked | 2026-07-21：旧正式 run 自动门禁通过，但独立盲评 final/initial/tie 为 3/4/3，final 偏好率 30% 低于 50%；production admission 仍默认关闭。解除条件依次为：先完成 F03 的 actual-visible 严格门禁并冻结 RC；再由独立保管人封存 release-held-out；随后在用户显式授权完整模型预算后启用 admission、以新 suite-run-id 运行真实 M5；最后由独立人员完成新一轮 10 例匿名 A/B 独立盲评。 |
