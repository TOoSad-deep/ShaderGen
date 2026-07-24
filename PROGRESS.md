# 进度

最后更新：2026-07-24

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
- 已落地 `shader_graph_v1` 严格 Layer/CSG 契约、确定性 Compiler、稳定 hash/parameter manifest 和 run-scoped 有界多 program registry；默认 `scene_mvp` 已切换为 ShaderGraph 产品真相源，贯通完整 Initial Author、单 typed layer patch、不可变 CandidateSnapshot、node/layer 参数 block、真实 WebGL1、manifest/API 和只读 Layer inspector。
- 感知仍复用 MinScene 作为确定性 seed，但在 Author 前转换为 ShaderDocument；旧固定模板与 shadow runner 只保留给显式 legacy Builder 测试/兼容审计，不参与默认产品 `current_best` 或 final GLSL。

## 当前 active 功能

- `F09`：以 `scene_mvp` 最小骨架继续完成可发布的 PNG-to-Shader 质量与可靠性闭环。

## 下一步

- 审核本轮产品切换的 `png_to_shader_graph_manifest_v1`、API 兼容 `scene` 字段、Layer inspector 和一次 raw-draw typed patch 语义；确认后提交当前增量。
- 在根 `.env` 配置生产 Provider 后，直接通过 `LangChainLLMGateway` 跑 3–5 个固定小样例；当前 Kimi Code canary 是真实模型输出经中立 Gateway 契约注入，不等于 Provider HTTP 直连。
- 决定旧 `polar_arc`、`edge_line`、`gaussian_lobe` 是新增兼容节点、以 segment/layer 显式重写，还是接受质量回退；旧 radial object-local 语义也需单独验证。
- 用相同候选与 draw 预算执行 geometry-first 字典序和 strict total-loss 的 live 单因素 A/B；离线 tile guard replay 不接入生产。
- 为最小骨架重新定义版本中立的 benchmark manifest、质量指标和人工门禁，不恢复旧 V1 benchmark 包或复用其历史 gate 名义。
- 扩展当前单例 local canary 为版本中立的固定小样例，并冻结 Compiler/Renderer/metric/config/hash；之后再运行匿名人工盲评。通过前 F09 保持 `active`。
- 若未来启用 Memory，以 scene_mvp 新契约和新 namespace 重新设计，同时保留当前休眠实现与 PostgreSQL 数据。

## 未解决缺口

- 当前最小骨架缺少新的冻结 benchmark 与人工偏好门禁；旧本地报告已删除，registry 摘要只能用于审计定位，不能复验或证明当前代码发布质量。
- tile guard offline replay 没有保护收益且存在高误拒；当前仅有一个 Kimi Code 输出经 `LLMGateway` 契约注入的 local/partial canary，生产 Provider Gateway、固定多样例和独立人工偏好仍未验证。
- `scene_mvp` 仍没有 CMA-ES、2000 draw 生产预算、优化中断/恢复和对应质量证据。
- 服务端仍是阻塞式 API；浏览器停止等待不等于服务端取消。任务化/cancel、outbox/reaper 和多 worker 分布式锁属于后续可靠性设计。
- 历史 real-model 完整报告及公开 review package 已随 `output/` 删除；registry 对应条目为 `missing`，只剩不可复验的摘要与原 SHA-256。
- ShaderGraph 已成为产品真相源，但确定性感知 seed 仍通过 MinScene 适配；旧 radial 的 object-local 椭圆坐标暂以短轴近似为 Canvas radial，`polar_arc`、`edge_line`、`gaussian_lobe` 尚无产品等价映射。模型可直接生成 V1 图，但这些兼容边界未通过 benchmark，不得宣称迁移无损或质量提升。
- node-id 优化首版只做单参数 current±step，并跳过需成对归一化的 rotation 分量；typed layer patch 首版只做一次 raw draw，尚未建立 Graph 专用局部成熟策略。

## 当前验证基线

- 2026-07-24 `make check` 通过：294 个单元测试、docs-check、LangGraph validate（1 个 Graph）和前端生产构建均成功；`mypy --strict src backend` 通过 100 个源文件，变更范围 Ruff 与 `git diff --check` 通过。
- 两条产品真实 Chromium 集成通过：一条执行 fallback→Compiler→program cache→6 draw 参数优化→CandidateSnapshot→final Artifact；一条把本次真实 Kimi Code 多模态输出经 `LLMGateway` 契约注入 Initial Author，严格 Parser、模型/fallback 仲裁和产品 final 均成功。
- 全量集成测试为 14 passed、1 skipped，scene_mvp 浏览器 E2E 通过；其中两条新增产品真实 Chromium 集成分别覆盖无模型优化链路与 Kimi Code 输出注入 Author 链路。
- 本地单例 Kimi Code canary（`kimi-canary-20260724-2`）通过：Graph 记录 `author_source=model/model_calls=1`，严格 MinScene Parser 通过；产品按真实 loss 选择较优 fallback，完成 48/48 draw；ShaderGraph 以 2 Layer/2 primitive 编译和实渲染成功。产品 MAE `0.037981`，shadow 对参考图 RGB MAE `0.037659`，产品与 shadow RGB MAE `0.014101`。证据为 local/partial；未直连生产 Provider Gateway，也不构成正式 benchmark 或发布门禁。
- 上游 tile guard 增量的原始基线为 11 个纯函数测试、455 次真实 Chromium draw、0 模型调用；代码、规格和本地报告因依赖旧 V1 benchmark 边界而在重构工作树删除，结论仅由 D063 与 Git 历史追溯。

## 最近重要变更

- 2026-07-24：按 D070 把默认产品真相源切换到有界 ShaderGraph，保持 12 节点拓扑不变，贯通 Initial Author、typed layer patch、CandidateSnapshot、node-id 参数优化、多 program cache、Backend/UI 和 final Artifact；真实 WebGL1 与 Kimi Code 输出注入 canary 已通过，正式 Provider/benchmark/人工 gate 尚未完成。
- 2026-07-24：按 D069 落地最小 Shader DSL/Compiler、有界多 program registry 和 finalize-only shadow 纵向切片；Kimi 只读复审无 P0，指出的 3 项 P1 已修复。随后单例模型 canary 跑通全链路，并据其结果把旧 shadow footprint 改为独立 Layer，产品与 shadow RGB MAE 从 `0.032279` 降至 `0.014101`。
- 2026-07-23：修复 PR #1 CI：用 `timezone.utc` 保持 Python 3.10 兼容，恢复当前 YAML 配置所需的 `types-pyyaml` 开发依赖，并统一 asyncio timeout 兼容断言。
- 2026-07-23：保留 Memory/checkpoint Python/SQL 实现与 PostgreSQL 数据，并将分支快进同步至 `origin/mvp@a39e676`；其仍绑定 V1 benchmark 的 tile guard runner/测试/规格继续删除，历史结论见 D063，Memory 策略见 D068。
- 2026-07-23：按用户明确授权删除整个本地 `output/`，包括旧 Node Lab、V1/V2/M5 benchmark、历史 run、截图和 review package；同步清理工具缓存与陈旧打包元数据，见 D067。

## 历史索引

- 完整原始记录：`docs/progress/archive/PROGRESS-2026-07-07--2026-07-15.md`。
- 阶段总结：`docs/progress/archive/STAGE-SUMMARY-2026-07-10.md`。
- 验收证据及耐久性状态：`docs/evidence/registry.json`；维护规则见 `docs/evidence/README.md`。
- 代码演进以 Git commit 为准；历史快照只用于审计，不得作为当前事实来源。

## 维护规则

- 会话结束时原地刷新当前状态、下一步、缺口和验证基线。
- “最近重要变更”最多保留 5 条。
- 主文件 UTF-8 体量不得超过 20,000 bytes；超出内容移入历史归档。冻结失败证据默认不得删除，精确范围的一次性退役清理必须由用户明确授权并记录。
