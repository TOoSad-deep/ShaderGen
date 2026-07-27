# Services 架构

当前产品只通过 `png_to_shader_min.py` 调用 `scene_mvp` Graph。

- Service 组合 Graph、`LLMGateway`、`LocalArtifactStore` 和 run 级 Renderer registry。
- `generate_png_to_shader_min()` 接收简单 Python 参数并返回稳定 dataclass，不向 Backend 暴露 LangChain 类型。
- `read_public_artifact()` 只接受 `final-render`、`metrics`、`manifest` 白名单名。
- `create_isolated_png_to_shader_min_service()` 只供已授权 rollout 的 fresh
  ShaderGraph child 使用：绑定独立 private `LocalArtifactStore`、Renderer registry
  和生产预算，不复用公开组合根的可变 cache；private store 显式启用 restrictive
  权限，所有 rollout attempt 文件/新建子目录分别为 0600/0700。
- Graph 正常终止由 `finalize` 关闭 Renderer，Service `finally` 对越过 Graph 的异常执行幂等兜底。
- 默认组合根执行 ShaderGraph 产品 Node，并通过同一 `MinRendererRegistry` 持有 run-scoped 有界 program cache；Prepared handle 不进入 State。显式 legacy Builder 才可注入非权威 `ShaderGraphShadowRunner`。
- Backend 只依赖本包公共接口；Agent Service 不持有数据库连接池。
- 旧 V1 Memory/checkpoint 基础设施未删除，但不再由产品 Service 打开或调用。

## layerplan_glsl_shadow.py（D088 shadow A/B，非产品）

- `LayerPlanGlslShadowRunner` 是独立离线 shadow A/B harness：Arm A 不注入 LayerPlan，Arm B 注入 VisualAnalysisAuthor 直读参考图生成的同一份 LayerPlanV1；两臂尽量共用同一模型、Prompt 主体、请求采样参数与 `ShadowABConfig` 预算，预期控制差异只有 LayerPlan。VisualAnalysis/LayerPlan 使用独立的 `plan_llm_budget` 与 `PlanLedger`，不消耗任一臂的 direct GLSL Author 预算，保证两臂 Author 预算语义完全一致。配置中的 `requested_sampling_params` 只是请求值；每次调用实际生效的 provider/model/temperature/reasoning_effort 由 Gateway 记录为 `effective_identity` 并绑定进 `author_identity.sampling_params`（例如 kimi 实际 temperature=1），真实响应缺有效身份时 Author fail-closed（`author_identity_unavailable`），绝不记录 unknown 或请求假值。由于无 seed 且 kimi 端点强制 temperature=1，模型采样、执行顺序和服务端漂移仍是混杂因素；任何结论必须多轮重复并做 AB/BA 交叉平衡，单 run 只具探索性，不得声称 LayerPlan 是唯一因果变量。
- 两臂各自持有独立 ledger（LLM/token/repair/compile/draw/wall-clock/program cache/`current_best`）；执行顺序由 `ShadowABConfig.arm_order` 在查看结果前冻结并写入报告；预算耗尽与失败收敛为预声明 `INCONCLUSIVE_CODES`。token usage 只有在每次供应商响应都提供 usage 时才汇总为整数，任一次缺失即保持 `null`，不得把未知成本记成 0 或部分总数。program cache 只绑定可编译 program（`source_sha256` + uniform schema 类型 + canvas/contract）：命中只跳过 compile，每个新 Spec 仍各自 draw、签发自己的 attestation、形成自己的候选，prepared handle 在臂结束时统一关闭。
- 候选管线 fail-closed：Author 输出的 canonical `ShaderProgramSpecV1` → `validate_program_spec_safety` 全量执行 canonical WebGL1 静态规则（包含 `v_uv/u_image/u_resolution/u_time` 兼容声明；`u_image` 只允许声明、禁止采样；for 循环必须可静态证明且迭代数 ≤ `max_loop_iterations`；宏类 token 改写预处理指令禁止）→ 真实或协议注入的 WebGL1 prepare+draw → 成功 draw 后由 Renderer 私有 signer 就地签发绑定具体 Spec/RGB/PNG/runtime 的 `ExecutionReceipt`（runner 只有 verify-only capability，绝不自行签发；attestation/receipt 只在同进程内可验证，**不是 durable 证据**）→ `issue_attestation` 并校验 matching → metric 使用 receipt 绑定的同一 rgb/png bytes，以 `min_scene_composite_v3` strict total-loss 更新 arm-local `current_best`；接受谓词 `is_strict_improvement` 只读取 loss，绝不读取 LayerPlan。结构修复产物另以 `repair_context_sha256` 绑定 repair Prompt、首轮输出/错误、Schema 以及两次实际调用身份。
- `ShadowABConfig` 在任何图片 resize 前把显式画布限制在 Renderer contract 长边上限内；三类 Author 的可信 identity/Spec 装配错误统一收敛为安全结果，不得越过预声明 `inconclusive` 机制冒泡。`write_shadow_run` 把 LayerPlan、Spec、render、metric、ledger、arm identity、执行顺序与全部内容 hash 只写显式指定的私有 run 目录（`shadow-<内容id>/`）：同根 `.shadow-*.staging-*` 暂存 + 原子 rename，崩溃不留下占用最终 run_id 的半成品；目录 0700、文件 0600，拒绝 symlink 与覆盖既有 run；报告携带 `report_sha256`、evaluation 身份（metric version/preprocess/background）、候选 metric/residual 哈希与探索性 validity notes。`verify_shadow_run`（CLI `--verify`）重算全部文件哈希与报告哈希，要求目录名与报告主体重算的内容寻址 run id 一致、路径为规范 POSIX 相对路径且 resolved containment 成立，并检查权限、额外文件、staging 半成品和整棵树任意 symlink（含 dangling link），篡改或目录改名即 fail-closed。不调用 `LocalArtifactStore.register_run`，不接产品 API/manifest，不登记 durable evidence。CLI 入口 `scripts/run_layerplan_glsl_shadow_ab.py` 必须显式 `--allow-live-model` 才运行真实模型。

## layerplan_glsl_shadow_review.py（D094 人工盲评，非产品）

- `write_blind_review_package()` 只接受已通过当前 v2 suite verifier 的目录，以 `suite_report_sha256 + sample_id + round_index` 稳定派生匿名 A/B。公开 `reviewer/` 只含 `item-*` reference/A/B PNG、静态页面和 review template；sample、round、run 与真实 Arm 映射留在父目录 `mapping.private.json`。
- 只有同时存在 A/B `current_best` 的 round 生成图片；不可配对 round 只在 package manifest 记录 schedule index 与安全原因，不伪造候选、不进入公开页面。人工 JSON 必须完整覆盖全部可评项且 choice 只允许 `A/B/tie`；偏好率分母仍是 manifest 预定的全部 round，tie 与不可评项均不计 Arm B 胜。
- package 采用同根 staging + 原子 rename、0700/0600、write-once；每个文件记录 SHA-256/size，manifest 另绑定自身 canonical payload。`verify_blind_review_package()` 先递归复验原 suite，再拒绝盲评树的 symlink、额外文件/目录、改名、权限或内容漂移；`evaluate_blind_review()` 只有在 suite/package 全部通过后才读取人工 JSON，输出只保留 reviewer alias hash。Agent 不得生成或代填人工选择。

## layerplan_glsl_direct.py（D095 单 engine 内核，受 policy 选择）

- `LayerPlanGlslDirectRunner` 只运行 VisualAnalysis LayerPlan + direct Initial/Refine，不运行 A/B 对照 Arm A。它与 shadow Arm B 共同调用 `LayerPlanGlslShadowRunner.execute_layerplan_direct_arm()`，因此 canonical safety、真实 Renderer receipt、metric、program cache、预算与 strict incumbent 选择只有一份实现。
- `LayerPlanGlslDirectConfig` 独立冻结 plan/direct/repair/compile/draw/refine 预算和 implementation identity；`DirectAttemptResult` 是不可变内存结果，保留 canonical LayerPlan、ProgramSpec、render bytes、receipt 与完整 metric，供后续私有 attempt Artifact 使用。`to_safe_summary()` 只暴露 engine/representation/hash、loss、状态、安全失败码与 ledger，不含 GLSL、LayerPlan 正文、Prompt、render bytes 或原始错误。
- 本模块本身不装配 LangGraph、不注册 Backend/API、不写产品 Artifact，也不更新
  D070 `current_best`；production shadow 和已授权 rollout runtime 均从 Backend
  组合根调用它。`engine_rollout_artifacts.py` 负责 public/private store 隔离，
  只有父协调器可把被选 child 的三个白名单文件原子发布为
  `png_to_shader_manifest_v2`；发布前强制 engine/representation 合法配对且与
  `engine_run.selected_representation` 一致。当前真实 registry 缺 durable entry，因此该
  canary runtime 已实现但不会在实际启动中取得 authority。

## layerplan_glsl_promotion_evidence.py（D096 私有晋升证据，非 registry）

- `build_promotion_evidence_bundle()` 先递归复验当前 v2 suite 与盲评包，再从人工 `A/B/tie` 原始 JSON 重算 canonical evaluation；自动与人工 gate 任一不为 `supported`、evaluation 非逐字节 canonical 或 hash 不匹配时均不创建 bundle。
- bundle 固定包含 suite、8 个完整 run、盲评 package、人工 review/evaluation、冻结 manifest/gate 和自描述 manifest；采用内容寻址目录、同根 staging + rename、0700/0600、write-once，离线 verifier 拒绝 symlink、路径越界、额外/缺失/改名、权限或 hash/size 漂移。
- 本模块不调用模型、浏览器、产品 Graph 或 evidence registry。D096 bundle `f42aefb…` 仍是 `local_private_not_registered`；只有迁入用户授权的不可变跨环境介质并登记 registry 后，才可能成为 `PromotionAuthorizationV1` 的 durable 输入。
