# 进度

最后更新：2026-07-24

> 本文件是有界的当前交接页，不是逐会话追加日志。功能状态以 `docs/FEATURES.md` 为准，长期取舍以 `docs/DECISIONS.md` 为准，完整旧记录见 `docs/progress/archive/`。

## 当前状态

- 当前 `main` 已先快进吸收 `TOoSad-deep/feature-improve@4768aa5`，再合并 `origin/mvp@6d4aac6`；代码、Graph 文档、ADR、进度和证据冲突已按当前 ShaderGraph 产品事实收口。
- 产品仍只有 `scene_mvp`，`langgraph.json` 只注册 `png_to_shader_min`。默认组合根使用 `shader_graph_v1`：Initial/Refine Author、specialized Compiler、真实 WebGL1、多 program cache、CandidateSnapshot、node/layer 参数 block、Backend/API/UI 已贯通，12 节点拓扑未改变。
- 感知阶段同时保留 legacy MinScene 测量与产品 `fallback_shader_graph`；默认产品使用 ShaderDocument。Memory/checkpoint Python/SQL 与 PostgreSQL 数据继续休眠保留，V1、Node Lab 和旧 benchmark 运行面仍保持删除。
- mvp 的 acceptance live A/B、strict total-loss 防漂移 helper、私有 MinScene Patch replay 和 12/32 maturity fixture 已合入。由于两分支复用了 `D064–D068`，本次把 mvp 五项决策顺延为 D072–D076，保留现有清理/Memory 决策编号不变。
- D072/D073 证明旧 MinScene 诊断候选空间内 strict total-loss 优于 geometry-first，并纠正生产原本即为 strict total-loss 的事实；当前 ShaderGraph 同样执行 strict total-loss，但旧实验不能外推为 ShaderGraph 质量结论。
- D074 的 replay 契约、legacy `make_min_nodes` 实现和聚焦测试已保留；默认 `make_shader_graph_nodes` 尚未迁移 typed layer patch replay，当前产品 manifest 不包含 `private_replay_bundle`。D075 的 `budget32_supported` 只适用于两个旧 Feature 合成 fixture，D076 明确不授权当前产品预算变化。
- acceptance 与 maturity 两份报告仍存在于来源 mvp worktree 的本地忽略目录，registry 为 `partial`；当前工作树未复制报告，不得把 registry 当作 durable 发布证据。

## 当前 active 功能

- `F09`：以 `scene_mvp` ShaderGraph 最小骨架继续完成可发布的 PNG-to-Shader 质量与可靠性闭环。

## 下一步

- 为 ShaderGraph 重新设计 typed layer patch replay、冻结 benchmark manifest、质量指标和人工门禁；不得直接复用旧 MinScene replay/12–32 draw 的发布含义。
- 保留“模型 Initial 仍输给 fallback”的负面质量事实，继续用版本中立的固定小样例验证 Prompt/搜索，不通过放宽 Schema 掩盖问题。
- 参数优化继续评估 rotation/成组参数、typed layer patch 局部成熟和更大搜索；任何预算变化必须使用 ShaderGraph 候选空间重新建立证据。
- 项目结构重构计划尚待确认，确认前不开始目录迁移；若未来启用 Memory，必须建立 scene_mvp 新契约、namespace 和迁移验收。

## 未解决缺口

- 当前 ShaderGraph 产品没有 D074 等价的私有 typed layer patch replay；legacy bundle 不能恢复或证明当前产品候选过程。
- 当前产品缺少 durable 冻结 benchmark 与独立人工偏好门禁；生产 Qwen 小样例链路可运行，但 Initial 仍常由 scorer 判定不如 fallback。
- D072/D075 报告仅为 `partial` 且候选空间已被 D070 替换，不能用于 ShaderGraph 发布或直接把 patch maturity 从 1/12 调到 32 draw。
- `scene_mvp` 仍没有 CMA-ES、2000 draw 生产预算、优化中断/恢复和对应质量证据。
- 服务端仍是阻塞式 API；浏览器停止等待不等于服务端取消。任务化/cancel、outbox/reaper 和多 worker 分布式锁属于后续可靠性设计。
- 历史 V1/Node Lab real-model 完整报告与公开 review package 已按授权随旧 `output/` 删除；registry 对应条目为 `missing`，只能审计定位。

## 当前验证基线

- 两分支合并后 `make check` 通过：362 个单元测试、docs-check、LangGraph validate（1 个 Graph）和前端生产构建全部成功。
- 全量集成测试为 15 passed、1 skipped，scene_mvp 浏览器 E2E 通过；全仓 Ruff、`mypy --strict src backend`（102 个源文件）与 `git diff --check` 通过。

## 最近重要变更

- 2026-07-24：完成 `feature-improve@4768aa5` 与 `mvp@6d4aac6` 合并；保留 ShaderGraph 产品链路和旧 MinScene 诊断/replay 审计实现，并将冲突的 mvp ADR 编号顺延为 D072–D076。
- 2026-07-24：ShaderGraph Author Prompt 升级 v1_2，感知阶段直接提供产品 `fallback_shader_graph`，迁移映射归入 ShaderForge typed 边界；参数优化转为跨分支 TODO。
- 2026-07-24：按 D070 把默认产品真相源切换到有界 ShaderGraph，保持 12 节点拓扑不变，贯通 Author、typed layer patch、CandidateSnapshot、多 program cache、Backend/UI 和 final Artifact。
- 2026-07-24：生产 `dashscope:qwen3.7-plus` 直连三个小样例与一次 Refine 成功；三例 Initial 均输给 fallback，而 Refine 高光层小幅改善。
- 2026-07-24：按 D069 落地最小 Shader DSL/Compiler、有界多 program registry 和 finalize-only shadow 纵向切片，随后由 D070 升级为产品真相源。

## 历史索引

- 完整原始记录：`docs/progress/archive/PROGRESS-2026-07-07--2026-07-15.md`。
- 阶段总结：`docs/progress/archive/STAGE-SUMMARY-2026-07-10.md`。
- 验收证据及耐久性状态：`docs/evidence/registry.json`；维护规则见 `docs/evidence/README.md`。
- 代码演进以 Git commit 为准；历史快照只用于审计，不得作为当前事实来源。

## 维护规则

- 会话结束时原地刷新当前状态、下一步、缺口和验证基线。
- “最近重要变更”最多保留 5 条。
- 主文件 UTF-8 体量不得超过 20,000 bytes；超出内容移入历史归档。冻结失败证据默认不得删除，精确范围的一次性退役清理必须由用户明确授权并记录。
