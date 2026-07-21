# States 架构

`src/agent/app/states/` 保存 PNG-to-Shader V1 的 LangGraph State，区分任务内 checkpoint 字段和当前调用的 `UntrackedValue` 字段。

## 当前状态

- `PngToShaderV1State`：M3 自动闭环状态，区分小型路由摘要和当前调用的大对象/证据。
- `PngToShaderV2State`：V2 strict/frozen checkpoint envelope，只保存版本、run/hypothesis/seed 游标、当前 seed/genome/compilation/render/evaluation、Renderer request/evidence 与 objective best 的 `ArtifactRefV2`、稳定 attempt id/semantic hash、小型 objective 指针、Budget 和停止原因；不保存图片、GLSL、Genome、评价对象或 Store/Renderer 实例。
- `LocalPngToShaderV2StateStore`：V2.4 的单机文件 checkpoint 实现；按 run 派生不含原始 id 的文件名，以 `flock` 串行化同一文件系统上的进程，使用临时文件、文件 `fsync`、原子 `os.replace`、目录 `fsync` 和确认回读发布最后确认状态。

## V2.4 恢复契约

- V2.4 multi-capture recovery 是 breaking checkpoint contract：namespace 必须精确为 `png-to-shader-v2.4:{run_id}`，state/graph/checkpoint 固定为 `state_v4`、`png_to_shader_v2@2.4`、`checkpoint_v4`，本地 envelope 为 `local_state_checkpoint_v4`。旧 `state_v2/state_v3`、`checkpoint_v2/checkpoint_v3` 与 V1 payload 均严格拒绝，不做默认补字段或原地升级。
- `active_render_plan_ref`、`active_render_progress_ref`、`active_render_repeatability_ref` 与 rendered-structure evidence/verification refs 形成恢复前缀；`active_render_call_ordinal` 在 Renderer budget reservation 和真实调用前持久化稳定 call intent。同一时刻最多一个 reserved render call，且 reservation 必须绑定 active physical-call intent；`promotion_operation_ref`/`promotion_receipt_ref` 分别表示 promotion outbox intent 与可证明完成的 commit receipt。
- `evolve_state_v2()` 保持为纯 transition helper：检查调用方给出的 `run_revision`，严格重验更新后返回新对象；版本、Graph/checkpoint namespace、project/run identity 和 Budget 不得由它修改。
- Budget reservation/commit helper 使用独立 `budget_state.revision` 做期望版本检查并返回新对象；每一维始终满足 `used + reserved <= limits`。run CAS 只推进 `run_revision`，Budget CAS 只推进 `budget_state.revision`，二者共享文件锁但不是同一个 revision 域。
- `LocalPngToShaderV2StateStore.compare_and_swap_run()`、`reserve_budget()` 和 `commit_budget()` 在进程锁内先恢复并校验最后确认 State，再检查对应旧 revision、写入和确认回读；陈旧 run/budget revision 均 fail closed。reservation 会随 checkpoint 跨进程重启恢复，重启后只能用当前 Budget revision 显式 commit 或由上层恢复策略处理，不会静默转成 used 或丢弃。
- checkpoint envelope 保存 State JSON 的 SHA-256，外层和内层 JSON 都拒绝 duplicate key、NaN/Infinity、未知字段、错误版本、错误 run identity 与摘要篡改；孤立临时文件不是已确认 checkpoint，不参与恢复。SHA-256 只提供内容完整性，不是带密钥的来源认证或防回滚机制。
- 该实现只承诺同一台机器、同一文件系统、共同锁文件下的进程安全和崩溃后一致文件恢复；Renderer request 与已完成 call evidence 会持久化，未知崩溃 reservation 保守计费，但不宣称数据库级事务、分布式 CAS、跨机器互斥或产品级异步 Run 恢复。

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
- `UntrackedValue` 运行输入与预算：`run_id`、`image`、`content_type`、`instruction`、`render_contract`、`budget_policy`、`acceptance_policy`、`runtime_policy_schema_version`、`runtime_policy_sha256`、`started_at`、`reference_ref`、`target_measurements`。
- `UntrackedValue` 模型与候选事实：`visual_analysis`、`visual_analysis_model`、`author_result`、`previous_author_result`、`author_model`、`candidate_provenance`、`candidate_origin`、`candidate_generator_version`、`glsl`、`static_validation`、`compile_result`、`render_status`、`rendered_image`、`rendered_content_type`、`score_breakdown`、`residual_summary`、`candidate_record`、`current_best_record`、`candidate_records`、`current_candidate`、`current_best_candidate`、`render_evidence_binding`、`visual_review`、`visual_critic_model`、`repair_budget`、`structured_output_max_attempts`、`next_action`、`selection_decision`、`selection_ref`。
- `UntrackedValue` 输出、Context 与审计：`final_result`、`final_manifest_ref`、`context_pack`、`selected_memory_ids`、`memory_status`、`model_calls`、`events`、`logs`。
- Candidate 的可恢复真相源是 `LocalArtifactStore`，不是 checkpoint 中的大对象。M3 是同步闭环；跨进程中断恢复和异步 Run API 属于 V1.1，不能假定 `UntrackedValue` 可恢复。
- Graph State 不保存 Renderer、Artifact Store 或 Gateway 实例；这些依赖由 Builder/运行时 registry 持有。
