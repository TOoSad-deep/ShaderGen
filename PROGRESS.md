# 进度

最后更新：2026-07-26

> 本文件是有界的当前交接页，不是逐会话追加日志。功能状态以 `docs/FEATURES.md` 为准，长期取舍以 `docs/DECISIONS.md` 为准，完整旧记录见 `docs/progress/archive/`。

## 当前状态

- 已完成项目结构只读审阅，并形成尚未纳入 Git 的 `docs/PROJECT_STRUCTURE_REFACTOR_PLAN.md`；当前只记录候选阶段与决策门，尚未移动代码或改变任何运行契约。
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
- `codex/layerplan-glsl-shadow` 分支完成 LayerPlan 第一阶段修订版设计基线：首稿经审阅不予验收并已移除；D083 修订为只授权 shadow 实验，新基线为 `docs/superpowers/specs/2026-07-26-layerplan-glsl-shadow-design.md`——LayerPlanV1 由独立视觉分析 Author 直读参考图生成、永久 advisory；ShaderProgramSpecV1 是模型生成并经安全校验的执行真相；CandidateSnapshotV2 不保留 document/compiled 双真相；shadow A/B 两臂预算与状态完全隔离；晋升要求 durable 内容寻址证据。本阶段只改文档，生产 Graph、代码、API、FEATURE 状态均未变。

## 当前 active 功能

- `F09`：以 `scene_mvp` ShaderGraph 最小骨架继续完成可发布的 PNG-to-Shader 质量与可靠性闭环。

## 下一步

- 先确认结构重构与 F09 的 active 关系、兼容范围、目录策略、前后端契约生成方式、Backend 分层和 Ruff/Mypy 门禁策略；确认前不开始目录迁移。
- 若要让 Node Lab 执行当前 `png_to_shader_min` 节点，需要为当前 ShaderGraph 契约新增独立 Provider/Executor factory；不得恢复已退役的 V1 Adapter。
- 为 ShaderGraph 重新设计 typed layer patch replay、冻结 benchmark manifest、质量指标和人工门禁；不得直接复用旧 MinScene replay/12–32 draw 的发布含义。
- 保留“模型 Initial 仍输给 fallback”的负面质量事实，继续用版本中立的固定小样例验证 Prompt/搜索，不通过放宽 Schema 掩盖问题。
- 参数优化继续评估 rotation/成组参数、typed layer patch 局部成熟和更大搜索；任何预算变化必须使用 ShaderGraph 候选空间重新建立证据。
- 项目结构重构计划尚待确认，确认前不开始目录迁移；若未来启用 Memory，必须建立 scene_mvp 新契约、namespace 和迁移验收。
- LayerPlan 第二阶段（shadow 实现）开工前需另立 ADR：三类 Author（VisualAnalysis/InitialGLSL/RefineGLSL）、Spec 校验器、私有证据写入与 A/B 隔离记账均只在 `docs/superpowers/specs/2026-07-26-layerplan-glsl-shadow-design.md` 中有设计基线，尚无实现授权。

## 未解决缺口

- 项目结构重构与当前唯一 `active` 功能 F09 的关系尚未确认；公共 import、Graph ID、HTTP/Artifact 契约和 Memory 语义也尚未形成重构期冻结清单。
- 当前 ShaderGraph 产品没有 D074 等价的私有 typed layer patch replay；legacy bundle 不能恢复或证明当前产品候选过程。
- 当前产品缺少 durable 冻结 benchmark 与独立人工偏好门禁；生产模型已切换为 `kimi:k3-256k`（D080），旧 Qwen 证据不外推，且 Initial 仍常由 scorer 判定不如 fallback。
- D072/D075 报告仅为 `partial` 且候选空间已被 D070 替换，不能用于 ShaderGraph 发布或直接把 patch maturity 从 1/12 调到 32 draw。
- `scene_mvp` 仍没有 CMA-ES、2000 draw 生产预算、优化中断/恢复和对应质量证据。
- 服务端仍是阻塞式 API；浏览器停止等待不等于服务端取消。任务化/cancel、outbox/reaper 和多 worker 分布式锁属于后续可靠性设计。
- 历史 V1/Node Lab real-model 完整报告与公开 review package 已按授权随旧 `output/` 删除；registry 对应条目为 `missing`，只能审计定位。
- Node Lab 工作台已通过 TypeScript/生产构建和 HTTP/service 单测，但当前没有与新通用空服务匹配的浏览器 E2E；旧 V1 假 API/E2E 未恢复。
- LayerPlan 只有修订版文档级设计基线（D083），没有实现、benchmark 或人工证据；晋升前不得影响候选选择、scorer、预算或 `current_best`。

## 当前验证基线

- 合并通用 Node Lab 并恢复 kimi/ShaderGraph 本地改动后 `make check` 通过：385 个单元测试、docs-check、LangGraph validate（1 个 Graph）和前端生产构建全部成功。
- `kimi:k3-256k` 真实连通性已验证：文本、JSON mode、`max_output_tokens` 路径均正常，family 固定 temperature=1 并按 D081 默认下发 `reasoning_effort=low`。
- 全量集成测试为 17 passed、1 skipped；全仓 Ruff、`mypy --strict src backend`（123 个源文件）、`uv lock --check` 与 `git diff --check` 通过。scene_mvp 浏览器 E2E 未在本次合并后重跑。
- LayerPlan 第一阶段只改文档（D083、设计文档、本文件），修订后 `make docs-check` 与 `git diff --check` 通过；未触碰代码与 Graph，单测/构建基线沿用上一行结论。

## 最近重要变更

- 2026-07-26：按审阅意见修订 D083 与 LayerPlan 设计：移除不予验收的首稿，新增 `docs/superpowers/specs/2026-07-26-layerplan-glsl-shadow-design.md`；生产 Graph、代码、API 与 FEATURE 状态不变。
- 2026-07-26：按 D082 向前移植 `refactor-node-lab-generic@222ea96`；恢复通用 Node Lab 内核、独立服务和新版工作台，同时保持旧 V1 Graph、专用 Adapter、manifest、benchmark 脚本与历史证据退役。
- 2026-07-26：按 D081 为 kimi family 增加 `reasoning_effort` 支持（`SHADER_GEN_KIMI_REASONING_EFFORT`，low/high/max，默认 low），生产 k3-256k 已以 low 真实验证。
- 2026-07-26：按 D080 新建 kimi 独立 model family（端点仅允许 temperature=1，family 固定温度），生产默认模型切换为 `kimi:k3-256k` 并通过真实调用验证文本/JSON mode/`max_output_tokens` 路径。
- 2026-07-26：按 D079 新增 kimi 模型 provider（`KIMI_API_KEY`/`KIMI_BASE_URL`，默认 `https://api.kimi.com/coding/v1`）；`.env.example`、`.env` 与 README 清单同步。

## 历史索引

- 完整原始记录：`docs/progress/archive/PROGRESS-2026-07-07--2026-07-15.md`。
- 阶段总结：`docs/progress/archive/STAGE-SUMMARY-2026-07-10.md`。
- 验收证据及耐久性状态：`docs/evidence/registry.json`；维护规则见 `docs/evidence/README.md`。
- 代码演进以 Git commit 为准；历史快照只用于审计，不得作为当前事实来源。

## 维护规则

- 会话结束时原地刷新当前状态、下一步、缺口和验证基线。
- “最近重要变更”最多保留 5 条。
- 主文件 UTF-8 体量不得超过 20,000 bytes；超出内容移入历史归档。冻结失败证据默认不得删除，精确范围的一次性退役清理必须由用户明确授权并记录。
