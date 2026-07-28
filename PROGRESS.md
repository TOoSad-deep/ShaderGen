# 进度

最后更新：2026-07-28

> 本文件是有界的当前交接页，不是逐会话追加日志。功能状态以 `docs/FEATURES.md` 为准，长期取舍以 `docs/DECISIONS.md` 为准，完整旧记录见 `docs/progress/archive/`。

## 当前状态

- 已按 D098 把 direct-first 长任务的等待窗口按内小外大统一扩大，并收敛到 Python/Vite 共读的 `src/shaderforge/config/runtime_timeouts.yaml`：模型单次 HTTP 默认 3600 秒，Renderer prepare/draw 默认 300/120 秒，单 engine attempt 默认 7200 秒；前端四档 POST 默认 5/6/8/12 小时、进度 GET 60 秒、POST 后观察 2 小时。YAML 严格校验正有限数、未知字段和内外层覆盖关系，未引入无限等待或取消语义。
- 已按 D089 完成前端运行可观测性收口：`frontend/src/runStages.ts` 单一纯函数视图模型统一推导运行状态、12 节点事实、失败定位、预算/current_best、Initial Author 输出来源与首轮真实选择；`author_source`/`selected_source` 均不冒充最终 provenance。轮询 single-flight、失败/连续 pending capped backoff、事件按 seq 去重排序；只有匹配 run_id 的稳定应用错误或 run 创建前的 `client_validation/request_validation` 停止观察，状态 live region 不包含每秒计时。产品 API/Graph/事件契约未变。
- 已按 D083–D085 完成 Node Lab 结构收拢：HTTP transport 已迁入 `src/nodelab/http/`，Schema 与 Route 分层，稳定 API/CLI/8090 端口保持不变；干净 wheel 门禁确认旧 `nodelab_service` 不再打包，产品 Backend 仍不注册 Lab route。
- 已按 D086 将当前 `scene_mvp` 12 个生产 Node 接入独立 Node Lab：Provider/Executor 位于 `agent.app.nodes.png_to_shader_min.node_lab`，受信任组合根位于 `agent.app.services.node_lab`；Artifact hydration、带指纹状态恢复、AI-off 与真实模型门禁均已贯通。`/lab` 已支持 descriptor 驱动 Artifact 输入、等价 SHA 合并、推荐父 Step、空目录/离线恢复与响应式可访问性。
- 当前 `main` 已先快进吸收 `TOoSad-deep/feature-improve@4768aa5`，再合并 `origin/mvp@6d4aac6`；代码、Graph 文档、ADR、进度和证据冲突已按当前 ShaderGraph 产品事实收口。
- 产品仍只有 `scene_mvp`，`langgraph.json` 只注册 `png_to_shader_min`。D097 后 Backend 无 policy 文件时默认使用 `direct_glsl_layerplan_v1`，失败才创建独立的私有 `shader_graph_v1` fallback attempt；旧引擎的 Initial/Refine Author、specialized Compiler、真实 WebGL1、多 program cache、CandidateSnapshot 与 12 节点 Graph 均保留为 fallback，Graph 拓扑未改变。
- 感知阶段同时保留 legacy MinScene 测量与产品 `fallback_shader_graph`；默认产品使用 ShaderDocument。Memory/checkpoint Python/SQL 与 PostgreSQL 数据继续休眠保留；旧 V1 产品、Adapter、manifest 与 benchmark 入口仍保持删除，通用 Node Lab 不改变该边界。
- mvp 的 acceptance live A/B、strict total-loss 防漂移 helper、私有 MinScene Patch replay 和 12/32 maturity fixture 已合入。由于两分支复用了 `D064–D068`，本次把 mvp 五项决策顺延为 D072–D076，保留现有清理/Memory 决策编号不变。
- D072/D073 证明旧 MinScene 诊断候选空间内 strict total-loss 优于 geometry-first，并纠正生产原本即为 strict total-loss 的事实；当前 ShaderGraph 同样执行 strict total-loss，但旧实验不能外推为 ShaderGraph 质量结论。
- D074 的 replay 契约、legacy `make_min_nodes` 实现和聚焦测试已保留；默认 `make_shader_graph_nodes` 尚未迁移 typed layer patch replay，当前产品 manifest 不包含 `private_replay_bundle`。D075 的 `budget32_supported` 只适用于两个旧 Feature 合成 fixture，D076 明确不授权当前产品预算变化。
- acceptance 与 maturity 两份报告仍存在于来源 mvp worktree 的本地忽略目录，registry 为 `partial`；当前工作树未复制报告，不得把 registry 当作 durable 发布证据。
- 已修复 run `362d2164-3438-4e53-b784-7104d7c269e7` 暴露的 ShaderGraph compile 预算错配：旧固定 16 无法覆盖 manual 30 轮 Refine，D077 改为按 run 推导（manual=45），意外耗尽也会收敛为稳定候选失败而非未分类 500。
- 已修复 run `04b7b4af-2dd0-495d-9ac6-0b34f1eeca23` 暴露的 recursion 上界失真：无效 Refine 过去会重建参数队列，D078 改为复用 current best 的 no-op 过桥，high 连续失败 patch 不再重复优化或撞 85 步上限。
- `codex/layerplan-glsl-shadow` 分支已按 D088 实现独立离线 LayerPlan/direct GLSL shadow A/B harness：`shaderforge.program_spec` 是唯一 canonical LayerPlan/ProgramSpec/哈希/attestation 真相，三类 Author 直读参考图，LayerPlan 永久 advisory；VisualAnalysis 使用独立 `plan_llm_budget/plan_ledger`，不挤占两臂相同的 direct Author 预算；候选必须通过 canonical 安全校验与真实 WebGL1 prepare/draw，才能按真实 metric 更新 arm-local `current_best`。详细证据只写显式本地私有目录（同根 staging + 原子 rename、0700/0600、`verify_shadow_run` 可复验），不进入生产 Artifact/evidence registry；生产 Graph、API、ShaderDocument/Compiler 和 FEATURE 状态均未变。Codex 复审后身份已改为事实制：`author_identity` 只记录 Gateway effective 采样身份（kimi 实际 temperature=1），缺有效身份 fail-closed；Initial/Refine/LayerPlan 哈希绑定 content_type，Refine 另绑定 current_render 与 canonical 评估上下文；显式画布在 resize 前受 Renderer 上限约束，可信装配错误收敛为安全结果，token usage 缺失保持 `null`，证据目录名必须匹配报告内容 run id。无 seed 的温度 1 A/B 只具探索性，结论必须多轮重复并做 AB/BA 交叉平衡。
- 已按 D090 完成冻结 LayerPlan shadow suite，并按 D091 完成首轮有效真实模型运行。正式 suite `shadow-suite-43a0748fa395` 与 8 个单 run 递归复验通过：Arm B 成功 `7/8`、Arm A `5/8`，5 个可比较 run 中 B 4 胜 1 负，AB/BA 子集方向均有利于 B；但 3/4 样本至少一轮因 `glsl_renderer_contract_violation` 无法配对，inconclusive ratio=`0.75`，只有 `rimmed_disk` 两轮可比较，improved sample ratio=`0.25`，自动 gate=`not_supported`，生产结论明确 no-go。报告 SHA-256 为 `43a0748fa39525b0c44106b2ffc323557e29fc1cb553300cb60408af39ee1075`，仍为 `local_private_not_registered`。首次配置失败 suite `4cd3b45d7644` 继续保留不覆盖。
- 已按 D092 完成 direct GLSL 契约稳定性 v2：shadow Initial/Refine 改用独立 v2 Prompt 与 GLSL repair v2，生产 scene_mvp 和 VisualAnalysis 继续默认 repair v1。Author Parser 复用完整 `validate_program_spec_safety`，把安全违规收敛为最多 12 条 `code + line`，repair 上下文哈希绑定实际 repair Prompt 和安全 hints；不放宽 Validator、不静默改写 GLSL。shadow 编译失败事件不再保存原始 compiler log 或 Validator message，只记录存在性、日志哈希及安全类别。
- 已按 D093/D094 完成冻结 v2 真实 suite `shadow-suite-d03e2224684b`，并按 D096 完成人工盲评与本地 promotion bundle。bundle 仍是 `local_private_not_registered`，但 D097 明确它不再作为单人单环境默认切换的前置条件；历史证据原样保留。
- D095 的替换 runtime 已按 D097 直接启用为默认路径：无授权 `direct_default` 不读取 registry，direct 与 fresh ShaderGraph fallback 使用确定性 UUID5、独立 Renderer/cache/预算和 write-once private store；选中结果原子发布 v2 父 manifest/API discriminator，历史 v1 reader与 kill switch 保留。显式 canary 或携带授权的 policy 仍执行原严格校验。

## 当前 active 功能

- `F09`：以 `scene_mvp` ShaderGraph 最小骨架继续完成可发布的 PNG-to-Shader 质量与可靠性闭环。

## 下一步

- 继续拆分 Node Lab 中耦合较高的 `runner.py`，先抽离 JSON Schema 校验、Fixture executor 与 state diff 等纯逻辑；随后再处理 `benchmark.py`。
- 为 Node Lab OpenAPI 与 `frontend/src/api/nodeLab.ts` 增加自动 parity gate，并补齐真实 Chromium Renderer、Artifact 上传、逐节点 DAG、断线恢复和 finalize 的浏览器 E2E。
- 为 ShaderGraph 重新设计 typed layer patch replay、冻结 benchmark manifest、质量指标和人工门禁；不得直接复用旧 MinScene replay/12–32 draw 的发布含义。
- 保留“模型 Initial 仍输给 fallback”的负面质量事实，继续用版本中立的固定小样例验证 Prompt/搜索，不通过放宽 Schema 掩盖问题。
- 参数优化继续评估 rotation/成组参数、typed layer patch 局部成熟和更大搜索；任何预算变化必须使用 ShaderGraph 候选空间重新建立证据。
- 用日常本地请求观察 direct 成功率、fallback 原因与最终视觉质量；出现阻断时直接使用 `SHADERGEN_DIRECT_GLSL_KILL_SWITCH=1` 回到旧引擎，不再建设独立 canary 流程。

## 未解决缺口

- Node Lab `runner.py` 与 `benchmark.py` 仍偏大；OpenAPI 与前端 TypeScript 契约仍分别手写，没有自动 parity gate。
- 当前 ShaderGraph 产品没有 D074 等价的私有 typed layer patch replay；legacy bundle 不能恢复或证明当前产品候选过程。
- 当前产品缺少 durable 冻结 benchmark 与独立人工偏好门禁；生产模型已切换为 `kimi:k3-256k`（D080），旧 Qwen 证据不外推，且 Initial 仍常由 scorer 判定不如 fallback。
- D072/D075 报告仅为 `partial` 且候选空间已被 D070 替换，不能用于 ShaderGraph 发布或直接把 patch maturity 从 1/12 调到 32 draw。
- `scene_mvp` 仍没有 CMA-ES、2000 draw 生产预算、优化中断/恢复和对应质量证据。
- 服务端仍是阻塞式 API；D098 只扩大有界等待，浏览器停止等待仍不等于服务端取消。任务化/cancel、outbox/reaper 和多 worker 分布式锁属于后续可靠性设计。
- 进度注册表是单进程内存态、重启即失且无历史 run 查询：终态后前端只能展示本次会话已缓存事件，无法回放历史 run 阶段视图；后端也不提供节点开始/进行中百分比、完整 run 时长或最终 current_best provenance，前端按 D089 只展示事件级真实事实。
- 历史 V1/Node Lab real-model 完整报告与公开 review package 已按授权随旧 `output/` 删除；registry 对应条目为 `missing`，只能审计定位。
- 当前 scene_mvp Node Lab 尚无覆盖真实 Chromium Renderer、工作台操作、断线恢复和真实模型的自动浏览器 E2E；LabRun 也没有单 run cancel/close API，Renderer 只能在 finalize 或进程退出时回收。
- LayerPlan shadow v2 自动与人工 gate 均已通过，但完整 promotion bundle 仍是 `local_private_not_registered`；它只保留为历史质量证据，不再阻止 D097 的单环境默认 direct 路径，也不构成多环境发布授权。

## 当前验证基线

- 2026-07-28 D098 统一 timeout YAML 验证：`make check` 全绿（702 个 Python 单测、docs-check、干净 sdist→wheel 且包含 `shaderforge.config/runtime_timeouts.yaml`、1 个 LangGraph validate、32 个 Node 内置测试、32 个 Vitest、前端生产构建）；另有 75 个 timeout/provider/backend/rollout/Renderer 聚焦单测、全仓 Ruff、`mypy --strict src backend`（158 个源文件）与 `git diff --check` 通过。未调用真实模型或运行 benchmark。
- 2026-07-27 D097 直切验证：`BackendSettings.from_env()` 在无 policy/registry 配置时返回 `direct_default`、无 `PromotionAuthorizationV1`、有效阶段 `direct_default`；83 个聚焦 policy/rollout/lifecycle 单测和 `make check` 全绿（693 个 Python 单测、docs-check、干净 wheel、1 个 LangGraph validate、32 个 Node 内置测试、32 个 Vitest、前端构建），全仓 Ruff 与 `mypy --strict src backend`（156 个源文件）通过。使用无数据库、无 policy、无 registry 配置完成真实 Backend lifespan smoke，`GET /health` 返回 200 后正常关闭；未调用真实模型或运行 benchmark。
- 2026-07-27 合并主干验证：`make check` 全绿（690 个 Python 单测、docs-check、干净 sdist→wheel 边界、1 个 LangGraph validate、32 个 Node 内置测试、32 个 Vitest、前端生产构建）；真实 Chromium 全量集成 `23 passed, 1 skipped`，`make test-scene-mvp-ui` 通过。
- 全仓 Ruff、`mypy --strict src backend`（156 个源文件）、`uv lock --check`、冻结 v1/v2 manifest/gate 原字节对比分支和 `git diff --check` 通过。合并时新增 Vitest include 边界，避免 Node 内置测试被重复收集。
- D083–D096 编号已无重复：Node Lab 保留 D083–D086，LayerPlan/direct GLSL 顺延为 D087–D096；冻结 benchmark YAML 为保持历史 hash 原样保留分支内旧编号注释，外部 ADR 与实现引用使用新编号。
- `kimi:k3-256k` 真实连通性已验证：文本、JSON mode、`max_output_tokens` 路径均正常，family 固定 temperature=1 并按 D081 默认下发 `reasoning_effort=low`。

## 最近重要变更

- 2026-07-28：按 D098 将模型、Renderer、engine attempt、前端 POST/进度 GET/终态观察的超时整体放大并保持有限，再统一暴露为 Python/Vite 共读的版本化 YAML，修复浏览器 4 分钟早于服务端 direct+fallback 串行路径停止等待的边界错位。
- 2026-07-27：按 D097 将单人单环境默认路径直接切换为无授权 `direct_default`；保留 fresh old fallback、父 discriminator、历史 reader 与 kill switch，显式 canary 仍使用原严格授权边界。
- 2026-07-27：按 D095 完成不可达的生产替换 runtime：启动期 durable/identity 授权、稳定 engine 选择、direct child、fresh old fallback、父 v2 Artifact/API discriminator、历史 reader 和 kill switch 已贯通；当前 registry 无 durable entry，生产仍 old，未上传或登记私有 bundle。
- 2026-07-27：按 D089 完成前端运行可观测性并经多轮审计收口证据语义：新增 `frontend/src/runStages.ts` 单一可测试阶段视图模型与 vitest 单测，覆盖五态状态、阶段摘要、失败定位与真实计时；阶段只显示“预计下一节点（未确认开始）”，`author_source`/首轮 `selected_source` 均不冒充最终 provenance。轮询 single-flight、失败/连续 pending capped backoff、按 seq 去重；匹配 run_id 的稳定应用失败与 run 创建前的 `client_validation/request_validation` 才作为确定终态，计时与状态 live region 分离。生产 Graph/API/事件契约不变。
- 2026-07-27：按 D083–D086 完成 Node Lab transport、Schema、Route 分层及当前 scene_mvp 12 节点接入；独立 HTTP 服务、产品 Backend 隔离、干净 wheel、Artifact hydration 和 `/lab` descriptor 驱动输入均已建立门禁。

## 历史索引

- 完整原始记录：`docs/progress/archive/PROGRESS-2026-07-07--2026-07-15.md`。
- 阶段总结：`docs/progress/archive/STAGE-SUMMARY-2026-07-10.md`。
- 验收证据及耐久性状态：`docs/evidence/registry.json`；维护规则见 `docs/evidence/README.md`。
- 代码演进以 Git commit 为准；历史快照只用于审计，不得作为当前事实来源。

## 维护规则

- 会话结束时原地刷新当前状态、下一步、缺口和验证基线。
- “最近重要变更”最多保留 5 条。
- 主文件 UTF-8 体量不得超过 20,000 bytes；超出内容移入历史归档。冻结失败证据默认不得删除，精确范围的一次性退役清理必须由用户明确授权并记录。
