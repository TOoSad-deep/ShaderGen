# 进度

最后更新：2026-07-23

> 本文件是有界的当前交接页，不是逐会话追加日志。功能状态以 `docs/FEATURES.md` 为准，长期取舍以 `docs/DECISIONS.md` 为准，完整旧记录见 `docs/progress/archive/`。

## 当前状态

- 当前分支已快进同步到 `origin/mvp@a39e676`；产品已收敛为唯一 `scene_mvp` 最小骨架，`langgraph.json` 只注册 `png_to_shader_min`。
- V1 Graph、routing、State 字段、Node、Parser、Prompt、Service、业务契约，以及 ShaderForge 的 V1 TargetMeasurements/Basic Oracle/Selector/measurement-affine/benchmark 已删除。
- Backend/Frontend 不再进行 `procedural_v1|scene_mvp` 分流，不再提供 V1 Artifact、项目 Memory API/UI 或旧 score/review/current_best 展示。
- 旧 Node Lab、V1 benchmark manifest/golden/gate/runner/CI/fixture/测试与 V2–V5 方案源文件已删除。
- `build/`、`.mypy_cache/`、源码/测试 `__pycache__`、`.pytest_cache/`、`.ruff_cache/`、`shadergen.egg-info/`、frontend/dist 构建输出和 `.DS_Store` 已清理。
- Memory/checkpoint Python/SQL 实现与已有 PostgreSQL 数据按用户决定保留，但当前产品不消费；后续不得直接恢复旧 V1 Service/API。
- 本地 `output/` 已按用户明确授权整体删除，包含旧 Node Lab、V1/V2/M5 benchmark、历史 run、Playwright 截图和 review package；ADR、进度归档和 evidence registry 摘要保留。
- 已同步 `origin/mvp@a39e676` 的历史结论，但其 tile no-regression guard runner、测试和规格仍绑定已删除的 V1 benchmark/ROI/Oracle，因此在当前重构工作树继续删除；D063 保留审计结论，生产 scorer/Prompt/预算/目标未修改。

## 当前 active 功能

- `F09`：以 `scene_mvp` 最小骨架继续完成可发布的 PNG-to-Shader 质量与可靠性闭环。

## 下一步

- 用相同候选与 draw 预算执行 geometry-first 字典序和 strict total-loss 的 live 单因素 A/B；离线 tile guard replay 不接入生产。
- 为最小骨架重新定义版本中立的 benchmark manifest、质量指标和人工门禁，不恢复旧 V1 benchmark 包或复用其历史 gate 名义。
- 在新质量门禁明确后，再运行真实模型固定样例与匿名人工盲评；通过前 F09 保持 `active`。
- 若未来启用 Memory，以 scene_mvp 新契约和新 namespace 重新设计，同时保留当前休眠实现与 PostgreSQL 数据。

## 未解决缺口

- 当前最小骨架缺少新的冻结 benchmark 与人工偏好门禁；旧本地报告已删除，registry 摘要只能用于审计定位，不能复验或证明当前代码发布质量。
- tile guard offline replay 没有保护收益且存在高误拒；live acceptance 轨迹、真实模型固定样例和独立人工偏好仍未验证。
- `scene_mvp` 仍没有 CMA-ES、2000 draw 生产预算、优化中断/恢复和对应质量证据。
- 服务端仍是阻塞式 API；浏览器停止等待不等于服务端取消。任务化/cancel、outbox/reaper 和多 worker 分布式锁属于后续可靠性设计。
- 历史 real-model 完整报告及公开 review package 已随 `output/` 删除；registry 对应条目为 `missing`，只剩不可复验的摘要与原 SHA-256。

## 当前验证基线

- 同步 `origin/mvp@a39e676` 并收口冲突后，`make check` 通过：185 个单元测试、docs-check、LangGraph validate（1 个 Graph）和前端生产构建均成功。
- Python 3.10 兼容单测 185 passed；`mypy --strict src backend` 89 个源文件和全仓 Ruff 通过。集成测试基线为 10 passed、1 skipped，scene_mvp 浏览器 E2E 通过。
- 上游 tile guard 增量的原始基线为 11 个纯函数测试、455 次真实 Chromium draw、0 模型调用；代码、规格和本地报告因依赖旧 V1 benchmark 边界而在重构工作树删除，结论仅由 D063 与 Git 历史追溯。

## 最近重要变更

- 2026-07-23：修复 PR #1 CI：用 `timezone.utc` 保持 Python 3.10 兼容，恢复当前 YAML 配置所需的 `types-pyyaml` 开发依赖，并统一 asyncio timeout 兼容断言。
- 2026-07-23：保留 Memory/checkpoint Python/SQL 实现与 PostgreSQL 数据，并将分支快进同步至 `origin/mvp@a39e676`；其仍绑定 V1 benchmark 的 tile guard runner/测试/规格继续删除，历史结论见 D063，Memory 策略见 D068。
- 2026-07-23：按用户明确授权删除整个本地 `output/`，包括旧 Node Lab、V1/V2/M5 benchmark、历史 run、截图和 review package；同步清理工具缓存与陈旧打包元数据，见 D067。
- 2026-07-23：全量删除 V1 可执行链路、前后端分流、旧 benchmark/CI/测试及 V2/build/Python/Mypy 缓存，见 D066。
- 2026-07-23：删除旧 V2–V5 方案源文件并抽离中立消息/WebGL1 契约，见 D064。

## 历史索引

- 完整原始记录：`docs/progress/archive/PROGRESS-2026-07-07--2026-07-15.md`。
- 阶段总结：`docs/progress/archive/STAGE-SUMMARY-2026-07-10.md`。
- 验收证据及耐久性状态：`docs/evidence/registry.json`；维护规则见 `docs/evidence/README.md`。
- 代码演进以 Git commit 为准；历史快照只用于审计，不得作为当前事实来源。

## 维护规则

- 会话结束时原地刷新当前状态、下一步、缺口和验证基线。
- “最近重要变更”最多保留 5 条。
- 主文件 UTF-8 体量不得超过 20,000 bytes；超出内容移入历史归档。冻结失败证据默认不得删除，精确范围的一次性退役清理必须由用户明确授权并记录。
