# 进度

最后更新：2026-07-27

> 本文件是有界的当前交接页，不是逐会话追加日志。功能状态以 `docs/FEATURES.md` 为准，长期取舍以 `docs/DECISIONS.md` 为准，完整旧记录见 `docs/progress/archive/`。

## 当前状态

- 已完成项目结构只读审阅，并形成尚未纳入 Git 的 `docs/PROJECT_STRUCTURE_REFACTOR_PLAN.md`；当前只记录候选阶段与决策门，尚未移动代码或改变任何运行契约。
- 已按 D085 完成前端运行可观测性收口：`frontend/src/runStages.ts` 单一纯函数视图模型统一推导运行状态（含 pending/unknown 解释）、12 节点阶段（耗时、Graph 事件累计、trace 摘要、路由/停止原因）、失败定位、预算/current_best 质量进度与 Initial Author 输出来源；事件只在节点完成时发出，UI 只展示“预计下一节点（未确认开始）”，不存在“执行中”阶段；`author_source` 不再被表述为最终 current_best provenance，Graph 耗时缺失和预算缺失均显示“—”。轮询保持 single-flight，对失败/连续 pending capped backoff，并在 POST 结算后有界观察。产品 API/Graph/事件契约未变，`make check` 新增前端 vitest。
- `origin/codex/refactor-node-lab-generic@222ea96` 已向前移植到当前 `main`：保留通用 `nodelab`、独立 `nodelab_service`、受信任 Application factory 和 `/lab` 工作台；产品 Backend 仍不注册 Lab route，默认服务为空安全 Application。
- 当前 `main` 已先快进吸收 `TOoSad-deep/feature-improve@4768aa5`，再合并 `origin/mvp@6d4aac6`；代码、Graph 文档、ADR、进度和证据冲突已按当前 ShaderGraph 产品事实收口。
- 产品仍只有 `scene_mvp`，`langgraph.json` 只注册 `png_to_shader_min`。默认组合根使用 `shader_graph_v1`：Initial/Refine Author、specialized Compiler、真实 WebGL1、多 program cache、CandidateSnapshot、node/layer 参数 block、Backend/API/UI 已贯通，12 节点拓扑未改变。
- 感知阶段同时保留 legacy MinScene 测量与产品 `fallback_shader_graph`；默认产品使用 ShaderDocument。Memory/checkpoint Python/SQL 与 PostgreSQL 数据继续休眠保留；旧 V1 产品、Adapter、manifest 与 benchmark 入口仍保持删除，通用 Node Lab 不改变该边界。
- mvp 的 acceptance live A/B、strict total-loss 防漂移 helper、私有 MinScene Patch replay 和 12/32 maturity fixture 已合入。由于两分支复用了 `D064–D068`，本次把 mvp 五项决策顺延为 D072–D076，保留现有清理/Memory 决策编号不变。
- D072/D073 证明旧 MinScene 诊断候选空间内 strict total-loss 优于 geometry-first，并纠正生产原本即为 strict total-loss 的事实；当前 ShaderGraph 同样执行 strict total-loss，但旧实验不能外推为 ShaderGraph 质量结论。
- D074 的 replay 契约、legacy `make_min_nodes` 实现和聚焦测试已保留；默认 `make_shader_graph_nodes` 尚未迁移 typed layer patch replay，当前产品 manifest 不包含 `private_replay_bundle`。D075 的 `budget32_supported` 只适用于两个旧 Feature 合成 fixture，D076 明确不授权当前产品预算变化。
- acceptance 与 maturity 两份报告仍存在于来源 mvp worktree 的本地忽略目录，registry 为 `partial`；当前工作树未复制报告，不得把 registry 当作 durable 发布证据。
- 已修复 run `362d2164-3438-4e53-b784-7104d7c269e7` 暴露的 ShaderGraph compile 预算错配：旧固定 16 无法覆盖 manual 30 轮 Refine，D077 改为按 run 推导（manual=45），意外耗尽也会收敛为稳定候选失败而非未分类 500。
- 已修复 run `04b7b4af-2dd0-495d-9ac6-0b34f1eeca23` 暴露的 recursion 上界失真：无效 Refine 过去会重建参数队列，D078 改为复用 current best 的 no-op 过桥，high 连续失败 patch 不再重复优化或撞 85 步上限。
- `codex/layerplan-glsl-shadow` 分支已按 D084 实现独立离线 LayerPlan/direct GLSL shadow A/B harness：`shaderforge.program_spec` 是唯一 canonical LayerPlan/ProgramSpec/哈希/attestation 真相，三类 Author 直读参考图，LayerPlan 永久 advisory；VisualAnalysis 使用独立 `plan_llm_budget/plan_ledger`，不挤占两臂相同的 direct Author 预算；候选必须通过 canonical 安全校验与真实 WebGL1 prepare/draw，才能按真实 metric 更新 arm-local `current_best`。详细证据只写显式本地私有目录（同根 staging + 原子 rename、0700/0600、`verify_shadow_run` 可复验），不进入生产 Artifact/evidence registry；生产 Graph、API、ShaderDocument/Compiler 和 FEATURE 状态均未变。Codex 复审后身份已改为事实制：`author_identity` 只记录 Gateway effective 采样身份（kimi 实际 temperature=1），缺有效身份 fail-closed；Initial/Refine/LayerPlan 哈希绑定 content_type，Refine 另绑定 current_render 与 canonical 评估上下文；显式画布在 resize 前受 Renderer 上限约束，可信装配错误收敛为安全结果，token usage 缺失保持 `null`，证据目录名必须匹配报告内容 run id。无 seed 的温度 1 A/B 只具探索性，结论必须多轮重复并做 AB/BA 交叉平衡。
- 已按 D086 完成冻结 LayerPlan shadow suite，并按 D087 完成首轮有效真实模型运行。正式 suite `shadow-suite-43a0748fa395` 与 8 个单 run 递归复验通过：Arm B 成功 `7/8`、Arm A `5/8`，5 个可比较 run 中 B 4 胜 1 负，AB/BA 子集方向均有利于 B；但 3/4 样本至少一轮因 `glsl_renderer_contract_violation` 无法配对，inconclusive ratio=`0.75`，只有 `rimmed_disk` 两轮可比较，improved sample ratio=`0.25`，自动 gate=`not_supported`，生产结论明确 no-go。报告 SHA-256 为 `43a0748fa39525b0c44106b2ffc323557e29fc1cb553300cb60408af39ee1075`，仍为 `local_private_not_registered`。首次配置失败 suite `4cd3b45d7644` 继续保留不覆盖。

## 当前 active 功能

- `F09`：以 `scene_mvp` ShaderGraph 最小骨架继续完成可发布的 PNG-to-Shader 质量与可靠性闭环。

## 下一步

- 先确认结构重构与 F09 的 active 关系、兼容范围、目录策略、前后端契约生成方式、Backend 分层和 Ruff/Mypy 门禁策略；确认前不开始目录迁移。
- 若要让 Node Lab 执行当前 `png_to_shader_min` 节点，需要为当前 ShaderGraph 契约新增独立 Provider/Executor factory；不得恢复已退役的 V1 Adapter。
- 为 ShaderGraph 重新设计 typed layer patch replay、冻结 benchmark manifest、质量指标和人工门禁；不得直接复用旧 MinScene replay/12–32 draw 的发布含义。
- 保留“模型 Initial 仍输给 fallback”的负面质量事实，继续用版本中立的固定小样例验证 Prompt/搜索，不通过放宽 Schema 掩盖问题。
- 参数优化继续评估 rotation/成组参数、typed layer patch 局部成熟和更大搜索；任何预算变化必须使用 ShaderGraph 候选空间重新建立证据。
- 项目结构重构计划尚待确认，确认前不开始目录迁移；若未来启用 Memory，必须建立 scene_mvp 新契约、namespace 和迁移验收。
- 为 direct GLSL Initial/Refine/repair 新增版本化的 Renderer contract 遵循改进，重点消除 `glsl_renderer_contract_violation`；不得放宽 Validator 或静默修补越权 GLSL。改动完成后重新冻结 manifest/gate/实现身份并运行新 suite，不覆盖 v1 证据；只有新自动 gate 通过才进入人工盲评与 durable evidence。

## 未解决缺口

- 项目结构重构与当前唯一 `active` 功能 F09 的关系尚未确认；公共 import、Graph ID、HTTP/Artifact 契约和 Memory 语义也尚未形成重构期冻结清单。
- 当前 ShaderGraph 产品没有 D074 等价的私有 typed layer patch replay；legacy bundle 不能恢复或证明当前产品候选过程。
- 当前产品缺少 durable 冻结 benchmark 与独立人工偏好门禁；生产模型已切换为 `kimi:k3-256k`（D080），旧 Qwen 证据不外推，且 Initial 仍常由 scorer 判定不如 fallback。
- D072/D075 报告仅为 `partial` 且候选空间已被 D070 替换，不能用于 ShaderGraph 发布或直接把 patch maturity 从 1/12 调到 32 draw。
- `scene_mvp` 仍没有 CMA-ES、2000 draw 生产预算、优化中断/恢复和对应质量证据。
- 服务端仍是阻塞式 API；浏览器停止等待不等于服务端取消。任务化/cancel、outbox/reaper 和多 worker 分布式锁属于后续可靠性设计。
- 进度注册表是单进程内存态、重启即失且无历史 run 查询：终态后前端只能展示本次会话已缓存事件，无法回放历史 run 阶段视图；后端也不提供节点开始/进行中百分比、完整 run 时长或最终 current_best provenance，前端按 D085 只展示事件级真实事实。
- 历史 V1/Node Lab real-model 完整报告与公开 review package 已按授权随旧 `output/` 删除；registry 对应条目为 `missing`，只能审计定位。
- Node Lab 工作台已通过 TypeScript/生产构建和 HTTP/service 单测，但当前没有与新通用空服务匹配的浏览器 E2E；旧 V1 假 API/E2E 未恢复。
- LayerPlan shadow 首轮有效真实 suite 已完成但自动 gate 失败；主要缺口是 direct GLSL Author/repair 的 Renderer contract 遵循不稳定，以及尚无新版本重跑、durable 跨环境证据或人工偏好结论。当前不得影响生产候选选择、scorer、预算或 `current_best`。

## 当前验证基线

- 2026-07-27 D086 suite 实现后：`make check` 全绿（552 个 Python 单测、docs-check、LangGraph validate（1 个 Graph）、21 个前端 vitest 单测、前端生产构建）；新增 17 个 suite 单测与 1 个 fake LLM + 真实 Chromium 2×2 集成测试通过；前端可观测性 `make test-scene-mvp-ui` 基线继续通过。
- `kimi:k3-256k` 真实连通性已验证：文本、JSON mode、`max_output_tokens` 路径均正常，family 固定 temperature=1 并按 D081 默认下发 `reasoning_effort=low`。
- 全量集成测试为 17 passed、1 skipped；全仓 Ruff、`mypy --strict src backend`（123 个源文件）、`uv lock --check` 与 `git diff --check` 通过。
- LayerPlan shadow 聚焦门禁：canonical ProgramSpec、三类 Author、A/B runner、receipt/timeout 共 161 个单测通过；真实 Chromium WebGL1 集成验收 3 个通过（契约形状 prepare/draw + 固定 fake LLM 全 runner 链 ×2）。审查修复后已重跑：`make check` 全绿（535 个单测、docs-check、1 个 LangGraph validate、前端生产构建），全量集成 20 passed/1 skipped，全仓 Ruff、`mypy --strict src backend`（134 个源文件）、`uv lock --check` 与 `git diff --check` 全部通过。

## 最近重要变更

- 2026-07-27：按 D087 完成首轮有效 LayerPlan 真实 suite 并作 no-go 决策：8 个 run 全部递归复验，B 在可配对 run 中 4 胜 1 负且成功率高于 A，但 3/4 样本受 `glsl_renderer_contract_violation` 影响而 inconclusive，自动 gate 明确失败；不进入人工晋升盲评，生产路径不变，下一步先做版本化 direct GLSL 契约稳定性改进。
- 2026-07-27：按 D086 冻结并实现 LayerPlan shadow suite：四张版本中立参考图以新 manifest/hash 链进入新候选空间，两轮按 AB/BA 交叉平衡；预声明 gate 绑定 manifest、完整 config fingerprint、metric、自动阈值、人工偏好和 durable 要求。新增 fail-closed 加载器、顺序调度、配对聚合、私有递归复验、无模型单测和 fake LLM + 真实 Chromium 2×2 集成验收；首次 live suite 因根 `.env` 缺失产生 8/8 配置失败的可复验 no-go 证据，未形成质量 A/B，生产 Graph/API/current_best 不变。
- 2026-07-27：按 D085 完成前端运行可观测性并经多轮审计收口证据语义：新增 `frontend/src/runStages.ts` 单一可测试阶段视图模型与 vitest 单测，覆盖五态运行状态、阶段摘要、失败定位与真实计时；阶段不再标“执行中”（改为“预计下一节点（未确认开始）”），12 节点标签按产品事实更新（生成 ShaderDocument/编译 ShaderGraph/node/layer 参数块优化），`author_source` 只称“Initial Author 输出来源”且不代表最终 current_best provenance，`render_seq` 为实时帧刷新序号，缺失预算/Graph 耗时显示“—”。轮询保持 single-flight，对失败/连续 pending capped backoff，并在结算后有界观察；`make check` 接入前端单测，E2E 断言同步，生产 Graph/API/事件契约不变。
- 2026-07-27：按 Codex 审查关闭安全与证据缺口：spec hash 绑定完整 author/repair 身份；receipt 拆成 Renderer 私有 signer 与公共 verify-only verifier，并强制绑定具体 Spec/PNG/runtime；ProgramSpec 拒绝宏循环绕过且 for 循环上限为 1024；renderer prepare/draw/legacy render 有界超时并安全重置；显式画布在 resize 前受限，可信装配异常收敛为安全 Author 结果，未知 token usage 不再伪装为 0，私有 evidence 拒绝路径穿越、任意 symlink 与 run 目录改名。生产 Graph/API/current_best 不变。
- 2026-07-27：按 D084 实现独立 LayerPlan/direct GLSL shadow A/B harness；修复真实 renderer 兼容声明契约与 VisualAnalysis 预算污染；并按 Codex 独立审查收口身份/证据高风险项（effective 调用身份 fail-closed、content_type/current_render/评估上下文输入绑定、私有证据原子写入与 `verify_shadow_run`、探索性措辞冻结）。生产 Graph/API/current_best 保持不变。

## 历史索引

- 完整原始记录：`docs/progress/archive/PROGRESS-2026-07-07--2026-07-15.md`。
- 阶段总结：`docs/progress/archive/STAGE-SUMMARY-2026-07-10.md`。
- 验收证据及耐久性状态：`docs/evidence/registry.json`；维护规则见 `docs/evidence/README.md`。
- 代码演进以 Git commit 为准；历史快照只用于审计，不得作为当前事实来源。

## 维护规则

- 会话结束时原地刷新当前状态、下一步、缺口和验证基线。
- “最近重要变更”最多保留 5 条。
- 主文件 UTF-8 体量不得超过 20,000 bytes；超出内容移入历史归档。冻结失败证据默认不得删除，精确范围的一次性退役清理必须由用户明确授权并记录。
