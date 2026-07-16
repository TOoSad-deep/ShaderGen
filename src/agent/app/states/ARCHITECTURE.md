# States 架构

`src/agent/app/states/` 保存 PNG-to-Shader V1 的 LangGraph State，区分任务内 checkpoint 字段和当前调用的 `UntrackedValue` 字段。

## 当前状态

- `PngToShaderV1State`：M3 自动闭环状态，区分小型路由摘要和当前调用的大对象/证据。

## State 规则

- checkpoint 只保存下文列出的轻量路由 allowlist；其余 State 字段必须显式使用 `UntrackedValue`。
- 模型 reasoning 原文不进入 State；当前角色默认不捕获 reasoning，若受控日志开关显式开启，也只记录字符数和 SHA-256，不保存原文。
- 不把数据库连接、Store 实例或对象存储客户端放进 State。
- 新增跨节点字段时，先更新 `agent_state.py`，再更新相关节点、service 映射和测试。
- 后端需要持久化的数据，由 Agent service 返回结构化摘要，再由后端统一写库。
- 依赖模型的测试必须使用模拟对象，不通过 State 触发真实模型调用。
- 模型运行配置由 Node 配置和 Graph 依赖注入提供，不放进 State；State 只保存当前图计算出的业务中间结果和摘要。

## `PngToShaderV1State` 边界

- checkpoint allowlist：`project_id`、`phase`、`quality_preset`、`iteration`、`current_candidate_id`、`current_best_id`、`current_best_glsl_sha256`、`current_best_total_loss`、`current_best_score_summary`、`compile_repair_count`、`visual_refinement_count`、`no_improvement_count`、`model_call_count`、`candidate_sequence`、`measurement_seed_attempted`、`stop_reason`、`cancelled`。
- `UntrackedValue` 运行输入与预算：`run_id`、`image`、`content_type`、`instruction`、`render_contract`、`budget_policy`、`acceptance_policy`、`started_at`、`reference_ref`、`target_measurements`。
- `UntrackedValue` 模型与候选事实：`visual_analysis`、`visual_analysis_model`、`author_result`、`previous_author_result`、`author_model`、`candidate_provenance`、`candidate_origin`、`candidate_generator_version`、`glsl`、`static_validation`、`compile_result`、`render_status`、`rendered_image`、`rendered_content_type`、`score_breakdown`、`residual_summary`、`candidate_record`、`current_best_record`、`candidate_records`、`current_candidate`、`current_best_candidate`、`render_evidence_binding`、`visual_review`、`visual_critic_model`、`repair_budget`、`structured_output_max_attempts`、`next_action`、`selection_decision`、`selection_ref`。
- `UntrackedValue` 输出、Context 与审计：`final_result`、`final_manifest_ref`、`context_pack`、`selected_memory_ids`、`memory_status`、`model_calls`、`events`、`logs`。
- Candidate 的可恢复真相源是 `LocalArtifactStore`，不是 checkpoint 中的大对象。M3 是同步闭环；跨进程中断恢复和异步 Run API 属于 V1.1，不能假定 `UntrackedValue` 可恢复。
- Graph State 不保存 Renderer、Artifact Store 或 Gateway 实例；这些依赖由 Builder/运行时 registry 持有。
