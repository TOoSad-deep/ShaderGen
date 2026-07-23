# States 架构

`src/agent/app/states/` 保存 PNG-to-Shader LangGraph State，区分任务内 checkpoint 字段和当前调用的 `UntrackedValue` 字段。

## 当前状态

- `PngToShaderV1State`：M3 自动闭环状态，区分小型路由摘要和当前调用的大对象/证据。
- `PngToShaderMinState`：`scene_mvp` 的最小 12 节点状态；checkpoint 只保留阶段、预算、停止原因和 best MAE 摘要，图片、scene、GLSL、RGB、render、trace 与 final 结果均为 `UntrackedValue`。

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

## `PngToShaderMinState` 边界

- 轻量字段：`project_id`、`phase`、`status`、`stop_reason`、run classification/experiment/report/config fingerprint、draw/LLM/Refine 计数与预算、`target_mae`、`current_best_mae`、`feature_queue` 和 Refine branch 是否已完成选择的过桥标志。
- `UntrackedValue`：`run_id`、输入图片、确定性感知和目标 RGB、工作 scene、物化模板、当前 GLSL/render/MAE、不可被失败候选覆盖的 `current_best`、空间残差摘要、待选 Patch 安全摘要、最近拒绝窗口、Patch 证据、Author 实际模型/安全错误码、路由动作、阶段 trace 与 final manifest/result。
- `render_budget`、`llm_budget`、`refine_budget`、`target_mae` 和 `target_loss` 由 `src/agent/app/config/png_to_shader_min.yaml` 选定档位注入；`llm_budget` 再受该 YAML 三档最大值限制，语义调用和结构修复共用 `llm_call_count`。供应商错误会回退到确定性 scene，不覆盖 `current_best`。
- `pending_patch_summary` 和拒绝历史只保存 typed operation、feature id/type、SHA-256 指纹、数值 delta、拒绝原因与耗时；完整 Patch value、图片/RGB、GLSL、用户输入、模型原始响应和 reasoning 均不得进入 State 摘要或终态 trace。
- 私有 replay 只通过两个 `UntrackedValue` 字段传递引用：`pending_replay_step` 保存当前 refine step 的 `private/replay/steps/refine-NNN/` 目录与 patch draft 的 path/SHA-256/size 引用（`render_and_evaluate` 消费后清空），`replay_step_refs` 累积各 step 的 patch/record 引用供 `finalize` 汇总 bundle；完整 typed patch、anchor/candidate/raw/matured scene 与渲染 PNG 只落盘到 run 目录 `private/replay/`，不进入 State 值、trace、进度事件或公开账本。这些 State 引用只作提示：消费方只从 `refine_count` 派生 step 目录并按 `decode_verified_replay_json()` fail-closed 校验 path/sha256/size/schema/refine_count，State 中路径被篡改只会触发拒绝，不会重定向读写。
