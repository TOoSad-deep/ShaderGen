# Services 架构

当前产品只通过 `png_to_shader_min.py` 调用 `scene_mvp` Graph。

- Service 组合 Graph、`LLMGateway`、`LocalArtifactStore` 和 run 级 Renderer registry。
- `generate_png_to_shader_min()` 接收简单 Python 参数并返回稳定 dataclass，不向 Backend 暴露 LangChain 类型。
- `read_public_artifact()` 只接受 `final-render`、`metrics`、`manifest` 白名单名。
- Graph 正常终止由 `finalize` 关闭 Renderer，Service `finally` 对越过 Graph 的异常执行幂等兜底。
- 默认组合根执行 ShaderGraph 产品 Node，并通过同一 `MinRendererRegistry` 持有 run-scoped 有界 program cache；Prepared handle 不进入 State。显式 legacy Builder 才可注入非权威 `ShaderGraphShadowRunner`。
- Backend 只依赖本包公共接口；Agent Service 不持有数据库连接池。
- 旧 V1 Memory/checkpoint 基础设施未删除，但不再由产品 Service 打开或调用。

## layerplan_glsl_shadow.py（D084 shadow A/B，非产品）

- `LayerPlanGlslShadowRunner` 是独立离线 shadow A/B harness：Arm A 不注入 LayerPlan，Arm B 注入 VisualAnalysisAuthor 直读参考图生成的同一份 LayerPlanV1；两臂尽量共用同一模型、Prompt 主体、请求采样参数与 `ShadowABConfig` 预算，预期控制差异只有 LayerPlan。VisualAnalysis/LayerPlan 使用独立的 `plan_llm_budget` 与 `PlanLedger`，不消耗任一臂的 direct GLSL Author 预算，保证两臂 Author 预算语义完全一致。配置中的 `requested_sampling_params` 只是请求值；每次调用实际生效的 provider/model/temperature/reasoning_effort 由 Gateway 记录为 `effective_identity` 并绑定进 `author_identity.sampling_params`（例如 kimi 实际 temperature=1），真实响应缺有效身份时 Author fail-closed（`author_identity_unavailable`），绝不记录 unknown 或请求假值。由于无 seed 且 kimi 端点强制 temperature=1，模型采样、执行顺序和服务端漂移仍是混杂因素；任何结论必须多轮重复并做 AB/BA 交叉平衡，单 run 只具探索性，不得声称 LayerPlan 是唯一因果变量。
- 两臂各自持有独立 ledger（LLM/token/repair/compile/draw/wall-clock/program cache/`current_best`）；执行顺序由 `ShadowABConfig.arm_order` 在查看结果前冻结并写入报告；预算耗尽与失败收敛为预声明 `INCONCLUSIVE_CODES`。program cache 只绑定可编译 program（`source_sha256` + uniform schema 类型 + canvas/contract）：命中只跳过 compile，每个新 Spec 仍各自 draw、签发自己的 attestation、形成自己的候选，prepared handle 在臂结束时统一关闭。
- 候选管线 fail-closed：Author 输出的 canonical `ShaderProgramSpecV1` → `validate_program_spec_safety` 全量执行 canonical WebGL1 静态规则（包含 `v_uv/u_image/u_resolution/u_time` 兼容声明；`u_image` 只允许声明、禁止采样；for 循环必须可静态证明且迭代数 ≤ `max_loop_iterations`；宏类 token 改写预处理指令禁止）→ 真实或协议注入的 WebGL1 prepare+draw → 成功 draw 后由 Renderer 私有 signer 就地签发绑定具体 Spec/RGB/PNG/runtime 的 `ExecutionReceipt`（runner 只有 verify-only capability，绝不自行签发；attestation/receipt 只在同进程内可验证，**不是 durable 证据**）→ `issue_attestation` 并校验 matching → metric 使用 receipt 绑定的同一 rgb/png bytes，以 `min_scene_composite_v3` strict total-loss 更新 arm-local `current_best`；接受谓词 `is_strict_improvement` 只读取 loss，绝不读取 LayerPlan。结构修复产物另以 `repair_context_sha256` 绑定 repair Prompt、首轮输出/错误、Schema 以及两次实际调用身份。
- `write_shadow_run` 把 LayerPlan、Spec、render、metric、ledger、arm identity、执行顺序与全部内容 hash 只写显式指定的私有 run 目录（`shadow-<内容id>/`）：同根 `.shadow-*.staging-*` 暂存 + 原子 rename，崩溃不留下占用最终 run_id 的半成品；目录 0700、文件 0600，拒绝 symlink 与覆盖既有 run；报告携带 `report_sha256`、evaluation 身份（metric version/preprocess/background）、候选 metric/residual 哈希与探索性 validity notes。`verify_shadow_run`（CLI `--verify`）重算全部文件哈希与报告哈希，要求规范 POSIX 相对路径及 resolved containment，并检查权限、额外文件、staging 半成品和整棵树任意 symlink（含 dangling link），篡改即 fail-closed。不调用 `LocalArtifactStore.register_run`，不接产品 API/manifest，不登记 durable evidence。CLI 入口 `scripts/run_layerplan_glsl_shadow_ab.py` 必须显式 `--allow-live-model` 才运行真实模型。
