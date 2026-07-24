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
- 感知内部仍复用 MinScene 测量结果以兼容 legacy Builder，但同阶段已直接产出 `fallback_shader_graph`；产品 Author 不再自行执行 MinScene 转换。迁移映射移入 ShaderForge typed 边界，旧固定模板与 shadow runner 不参与默认产品 `current_best` 或 final GLSL。
- 根 `.env` 已配置 `dashscope:qwen3.7-plus`，生产 `LangChainLLMGateway` 直连成功。三个固定小样例的 Initial Author 均可得到合法文档（卡片样例需要一次带脱敏字段诊断的结构修复），但三者最终都由真实 scorer 选择了感知 fallback；独立 Refine canary 的 `add_layer_bundle` 经真实 draw 接受。
- ShaderGraph Author Prompt 已升级为 v1_2：Initial 从可靠 fallback 按后到前拆 Layer，Refine 按主导问题选择单个 typed op；旧 `edge_line`、`gaussian_lobe`、`polar_arc` 只提供现有 segment/ellipse/radial/CSG 的保守表达规则，不新增专用节点。满 8 Layer、transform、CSG 和层序已新增真实 WebGL1 边界验证。

## 当前 active 功能

- `F09`：以 `scene_mvp` 最小骨架继续完成可发布的 PNG-to-Shader 质量与可靠性闭环。

## 下一步

- 参数优化相关改动统一保留为 TODO：rotation、成组参数、typed layer patch 局部成熟和更大搜索均不在当前分支重复实施；待另一分支成果可审查后再决定择入范围并做冲突/契约复核。
- 保留“模型初稿仍输给 fallback”的负面质量事实；如需固化 Qwen canary，只允许薄复用现有 Service，不接受复制 Graph/生成器的独立 runner。
- 继续以现有 segment/ellipse/radial/CSG 表达旧局部特征；只有固定样例证明 `polar_arc` 的弯月近似不可接受时才评估通用 `arc`，旧 radial object-local 语义也需单独验证。
- 用相同候选与 draw 预算执行 geometry-first 字典序和 strict total-loss 的 live 单因素 A/B；离线 tile guard replay 不接入生产。
- 为最小骨架重新定义版本中立的 benchmark manifest、质量指标和人工门禁，不恢复旧 V1 benchmark 包或复用其历史 gate 名义。
- 扩展当前单例 local canary 为版本中立的固定小样例，并冻结 Compiler/Renderer/metric/config/hash；之后再运行匿名人工盲评。通过前 F09 保持 `active`。
- 若未来启用 Memory，以 scene_mvp 新契约和新 namespace 重新设计，同时保留当前休眠实现与 PostgreSQL 数据。

## 未解决缺口

- 当前最小骨架缺少新的冻结 benchmark 与人工偏好门禁；旧本地报告已删除，registry 摘要只能用于审计定位，不能复验或证明当前代码发布质量。
- tile guard offline replay 没有保护收益且存在高误拒；生产 Qwen Provider 已直连，Prompt v1_2 的新增 orb canary 也仍由 scorer 选择 fallback，说明链路正确不等于 Author 质量达标。独立人工偏好仍未完成。
- `scene_mvp` 仍没有 CMA-ES、2000 draw 生产预算、优化中断/恢复和对应质量证据。
- 服务端仍是阻塞式 API；浏览器停止等待不等于服务端取消。任务化/cancel、outbox/reaper 和多 worker 分布式锁属于后续可靠性设计。
- 历史 real-model 完整报告及公开 review package 已随 `output/` 删除；registry 对应条目为 `missing`，只剩不可复验的摘要与原 SHA-256。
- ShaderGraph 已成为产品真相源；感知阶段同时输出 legacy MinScene 与产品 ShaderDocument，旧 radial 的 object-local 椭圆坐标暂以短轴近似为 Canvas radial。legacy 迁移遇到 `polar_arc`、`edge_line`、`gaussian_lobe` 仍 fail closed，模型 Prompt 只能用现有节点近似；不得宣称迁移无损或质量提升。
- node-id 优化首版只做单参数 current±step，并跳过需成对归一化的 rotation 分量；typed layer patch 首版只做一次 raw draw。全部参数优化相关后续已按用户要求转为跨分支 TODO，当前分支不继续修改。
- Qwen 在 `rounded_box` 样例首次输出违反领域校验；原 repair 只有笼统错误码而失败。Parser 现向 repair 提供不含原始值的 `location/type/message` 后修复成功，但 repaired 模型候选仍输给 fallback，需要后续从 Prompt/搜索而非放宽 Schema 解决质量。

## 当前验证基线

- 2026-07-24 本轮最终 `make check` 通过：302 个单元测试、docs-check、LangGraph validate（1 个 Graph）和前端生产构建均成功；`mypy --strict src backend` 通过 101 个源文件，变更范围 Ruff 与 `git diff --check` 通过。
- 两条产品真实 Chromium 集成通过：一条执行 fallback→Compiler→program cache→6 draw 参数优化→CandidateSnapshot→final Artifact；一条把本次真实 Kimi Code 多模态输出经 `LLMGateway` 契约注入 Initial Author，严格 Parser、模型/fallback 仲裁和产品 final 均成功。
- 全量集成测试为 15 passed、1 skipped，scene_mvp 浏览器 E2E 通过；真实 Chromium 额外覆盖满 8 Layer、translate/scale/rotate、CSG 与可观测图层顺序。
- 本地单例 Kimi Code canary（`kimi-canary-20260724-2`）通过：Graph 记录 `author_source=model/model_calls=1`，严格 MinScene Parser 通过；产品按真实 loss 选择较优 fallback，完成 48/48 draw；ShaderGraph 以 2 Layer/2 primitive 编译和实渲染成功。产品 MAE `0.037981`，shadow 对参考图 RGB MAE `0.037659`，产品与 shadow RGB MAE `0.014101`。证据为 local/partial；未直连生产 Provider Gateway，也不构成正式 benchmark 或发布门禁。
- 生产 Qwen local/partial canary：orb、card、mark 三例均完成 Provider HTTP→严格 Author→真实 WebGL1→Artifact；orb/mark 首次结构合法，card 经脱敏校验详情 repair 后合法，但三例最终均选择 fallback。独立 orb Refine 以 `add_layer_bundle` 增加高光层并被严格接受，loss `0.052422→0.051995`、3 draw、2 次模型调用。Artifact 位于 `/tmp/shadergen-provider-canary-20260724/provider-canary/`，不是 durable 发布证据。
- Prompt v1_2 单例 Qwen orb canary 通过：Initial 一次生成合法 ShaderDocument、无需 repair，产品 43 draw 后完成，最终仍选择 perception fallback；MAE `0.019252`、loss `0.048094`。Artifact 位于系统临时目录，只证明当前代码链路，不是 benchmark 或 durable 发布证据。
- 上游 tile guard 增量的原始基线为 11 个纯函数测试、455 次真实 Chromium draw、0 模型调用；代码、规格和本地报告因依赖旧 V1 benchmark 边界而在重构工作树删除，结论仅由 D063 与 Git 历史追溯。

## 最近重要变更

- 2026-07-24：ShaderGraph Author Prompt 升级 v1_2，感知阶段直接提供产品 `fallback_shader_graph`，迁移映射归入 ShaderForge typed 边界；新增满 8 Layer/transform/CSG/层序真实 WebGL 验证。参数优化统一转为跨分支 TODO，本分支不重复修改。
- 2026-07-24：按 D070 把默认产品真相源切换到有界 ShaderGraph，保持 12 节点拓扑不变，贯通 Initial Author、typed layer patch、CandidateSnapshot、node-id 参数优化、多 program cache、Backend/UI 和 final Artifact；真实 WebGL1 与 Kimi Code 输出注入 canary 已通过，正式 Provider/benchmark/人工 gate 尚未完成。
- 2026-07-24：生产 `dashscope:qwen3.7-plus` 直连三个小样例与一次 Refine 成功；新增仅含校验位置/类型/安全消息的 repair 诊断后，卡片 ShaderDocument 可修复为合法。三例 Initial 均输给 fallback，而 Refine 高光层小幅改善，链路与质量结论明确分离。
- 2026-07-24：按 D069 落地最小 Shader DSL/Compiler、有界多 program registry 和 finalize-only shadow 纵向切片；Kimi 只读复审无 P0，指出的 3 项 P1 已修复。随后单例模型 canary 跑通全链路，并据其结果把旧 shadow footprint 改为独立 Layer，产品与 shadow RGB MAE 从 `0.032279` 降至 `0.014101`。
- 2026-07-23：修复 PR #1 CI：用 `timezone.utc` 保持 Python 3.10 兼容，恢复当前 YAML 配置所需的 `types-pyyaml` 开发依赖，并统一 asyncio timeout 兼容断言。

## 历史索引

- 完整原始记录：`docs/progress/archive/PROGRESS-2026-07-07--2026-07-15.md`。
- 阶段总结：`docs/progress/archive/STAGE-SUMMARY-2026-07-10.md`。
- 验收证据及耐久性状态：`docs/evidence/registry.json`；维护规则见 `docs/evidence/README.md`。
- 代码演进以 Git commit 为准；历史快照只用于审计，不得作为当前事实来源。

## 维护规则

- 会话结束时原地刷新当前状态、下一步、缺口和验证基线。
- “最近重要变更”最多保留 5 条。
- 主文件 UTF-8 体量不得超过 20,000 bytes；超出内容移入历史归档。冻结失败证据默认不得删除，精确范围的一次性退役清理必须由用户明确授权并记录。
