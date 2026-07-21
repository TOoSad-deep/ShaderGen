# 进度

最后更新：2026-07-21

> 本文件是有界的当前交接页，不是逐会话追加日志。功能状态以 `docs/FEATURES.md` 为准，长期取舍以 `docs/DECISIONS.md` 为准，完整旧记录见 `docs/progress/archive/`。

## 当前状态

- 产品与 LangGraph 已收敛为 PNG-to-Shader V1：`langgraph.json` 只注册 `png_to_shader_v1`，产品只保留 `procedural_v1`；旧基础对话图、legacy 生成和独立 Review 入口已删除。
- 全部 V1 Node 工厂及其支持实现已收拢到 `agent.app.nodes.png_to_shader_v1` 功能命名空间，内部按 `model`、`deterministic` 和 `integrations/node_lab` 分层；`nodes/` 根目录不再保留实际绑定 V1 的伪通用模块，两个纯 decision callable 继续与条件边规则共同归属 Graph routing。
- `F09` 已完成 M0–M5、M6.0 可靠性修复、M6.1 离线质量改进及 Node Lab API v1 阶段 A–D。Node Lab 由生产 `NodeProvider` 暴露 20/20 节点，Harness 不维护具体 Graph/Node 的平行语义；`H02` 的 AI-off、五模型角色离线 fixture 与页面验收均已通过并标记为 `passing`。
- Backend 已在组合根冻结数据库、日志、CORS 与 Node Lab 配置；数据库和 Agent Memory 的半初始化、取消及关闭失败均进入补偿清理。主 CI 使用 Python/Node 锁定安装执行完整 `make check` 与 `mypy --strict src backend`，普通 Integration 不持有真实模型凭据。
- Backend 在线 `procedural_v1` 现提供 `fast|balanced|high|ultra` 四档 budget/acceptance，并在启动时严格加载、冻结 v2 YAML；Ultra 硬上限为 10 次视觉优化、5 次编译修复、40 次模型调用和 2400 秒，停滞窗口为 6，Graph recursion limit 为 256。配置 Schema、内容 SHA-256 和生效策略贯穿数据库总账、Agent State、`run-config.json` 与 final manifest；Node Lab 继续显式使用自己的预算，冻结 M5 benchmark 新运行与 resume 均拒绝 Ultra。
- 根目录 `dataset/` 的 16 张固定 PNG 已完成一次 exploratory Ultra 产品链路实跑：双进程主批中的 2 例 visual-analysis 60 秒超时经单进程原样重试均恢复，最终 16/16 获得 HTTP 200、数据库成功终态和完整 manifest/metrics/final-render；5/16 达到 `total_loss <= 0.12`，202 次有效模型调用，平均最终 loss `0.19546495`。该 run 只用于运行性和质量诊断，不是冻结 M5、V2 release gate 或发布证据。
- 仓库结构边界已加固：五模型角色离线 benchmark 与在线 Agent Service 分离，共享测试样本归入 `tests/fixtures`，轻量包导入不再 eager-load 浏览器/Runner/V1 契约；前端统一通过 API client 访问后端，Node Lab 只对选中步骤加载完整明细。
- 正式 run `m5-20260715T023445Z` 的自动质量检查 12/12 通过，但独立人工盲评 final/initial/tie 为 `3/4/3`，final 偏好率 `30%` 低于冻结的 `50%` 门槛；最终 gate 为 `failed`，F09 当前 blocked、灰度 no-go。
- M6.2 已完成 capability-v2 诊断和 admission 离线增量：通用纯契约从 benchmark case 解耦，`select_current_best()` 增加默认关闭的 keyword-only policy/evidence 集成点；旧正式 run 的 strict counterfactual replay v2 复验 source report/config SHA、suite/run acceptance policy、run-evidence、Candidate manifest、metrics/score、成功 compile 语义、GLSL/render，并用完整交叉字段校验防止重算 hash 后篡改。6 个 affine 选择点在旧 Selector 下均 accepted，离线 admission 拒绝 5 个 unsupported，覆盖全部 4 个 initial-win，唯一 supported 的 `ellipse_gradient` 仍 accepted。报告固定 `production_enabled=false`，不改变 V1 Graph、生产 Selector 默认语义或旧 gate；缺少 strict compile/config 的 v1 replay 作为错误产物只读保留。
- V2–V5 实施方案已完成拆分后正式 Review，形成总纲、四个版本方案和 Review 报告；结论为 Conditional Go，仅允许在 F09 M6.2 证据冻结后进入 V2.0 契约冻结，不代表 F02–F05 或异步产品能力已经实现。
- V2 要素闭包已扩展到 ownership/radial evidence：State v4/Graph 2.4 不变；`stable_instance_ordinal_first_match_v1`、Diagnostic/RenderPlan V3、Rendered Evidence/Verification V4、metric v3.2 已贯穿 Candidate、replay 和 formal gate。Measurements v2.2、TargetHypothesis/hash v3 与 Intent/Builder v3 可从 source alpha 重放 12/18 段 raw segment、semantic ownership 和 radial frame，并在 runtime/Candidate/Service resume 中复验正文；production admission 仍未启用。
- 开发侧 CC0 候选池现有 974 张可解码 PNG、894 个唯一哈希，与已登记图片碰撞为 0；原始包、许可/来源台账和总览见 `benchmarks/png_to_shader_v2/candidate_pool/`。20 组/80 个重复文件须在选样前去重；该池已公开给开发侧，不能作为 sealed release。

## 当前 active 功能

- `F03`：V2.3 State、Graph、Routing 与 Harness。权威状态和严格实施清单见 `docs/FEATURES.md` 与 `docs/V2-STRICT-GATES-AND-IMPLEMENTATION.md`。F02 已 passing；F09 因 production admission、新真实模型 M5 和独立盲评未完成而保持 `blocked`。

## 下一步

- 为 12/18 段 ring 实现消费 `ObjectIntent.radial_segment_evidence_ref` 的 typed segment primitive/Genome 节点与 Compiler lowering；当前 ownership bbox fallback 只保证可运行和 fail closed，不保证生成段形状。
- 完成 segment primitive 与一般 seed 质量优化后，以新 exclusive output 重跑 51 例 actual-visible；继续保持 0.90 union IoU、全 attempt 分母和旧 receipt 拒绝，不用标签特判或 verifier 回填。
- production admission 真正启用后，必须使用新 suite-run-id、完整硬预算重新运行真实模型 M5，并进行新一轮独立盲评；不得用本次 counterfactual replay 或当前人工结果宣称修复通过。
- 只有 visible actual gate 达标并冻结 RC 后，才由独立保管人补齐 release-held-out 并一次性执行；开发者已见的 40 张/候选池/validation 均不得冒充 release。
- 用本次 Ultra dataset 的 11 个未达阈值样本优先定位 objective、protected-region 晋升和 Critic/Author 收敛问题；将双并发下出现、单并发下消失的 visual-analysis 60 秒尾延迟作为在线 timeout/并发策略输入，但不得把 exploratory dataset 直接并入冻结 M5。

## 未解决缺口

- 自动 objective 尚不能充分保护人类偏好的视觉拓扑、实例数量和高光/阴影层次，这是 F09 当前的质量发布阻塞项。2026-07-17 本地 `pink_gel` High 真实链路再次显示：自动分数接受了未保留明显高光的 measurement seed，并在 Critic 前提前停止。
- breaking schema 后尚未重跑 51 例 actual-visible；旧 strict-v3 的 development 8/10、validation 11/41 只说明 metric v3.1/Evidence V3 的变更前质量，不可准入 V4。ownership 的结构缺口已实现，segmented-ring 的证据缺口已实现但 Compiler 表达仍缺 segment primitive，fixture/no-model 不能替代真实 VLM 质量。
- Ultra 扩大预算但没有保证达标：本次 16 例只有 5 例达到 `0.12`，停止路径还包括 stagnation 6 例、visual budget 2 例、best-effort 2 例和 compile-repair exhausted 1 例；双并发主批另有 2 次 visual-analysis 60 秒超时，串行重试恢复。阶段 timeout、结构化输出尾部失败和高预算下的收敛率仍需继续治理。
- 正式 M5 与 Node Lab real-model 的完整报告仍位于被忽略的本地 `output/benchmarks/`；`docs/evidence/registry.json` 已登记摘要、字节数和 SHA-256，但耐久性仍为 `partial`。在完整脱敏证据进入 Git LFS、Release 或不可变对象存储前，不能仅凭本地路径独立复验。
- 服务端仍是阻塞式 API；浏览器停止等待不等于服务端取消。端到端 deadline、任务化/cancel、outbox/reaper、多 worker 分布式锁和真实发生顺序事件属于后续可靠性设计，不与 M6.2 混写。2026-07-17 新项目 High 首次请求在 Memory 阶段 41.45 秒后返回可重试 503，`/health/db` 恢复后原样重试成功；仍需定位远程 Memory 连接的瞬时不可用。
- V2.3 仍需完成上述 actual-render 质量门禁和 RC 冻结；V4–V5 的 Oracle perturbation、SearchJournal failpoint、HumanEvaluation 和 V5 BenchmarkManifest 尚未完成。之后才可要求独立 release-held-out、显式真实模型预算和新一轮独立盲评；这三项继续阻塞 F09 `passing`。

## 当前验证基线

- 2026-07-17 M6.2 diagnostic-only v2 定向回归覆盖 source/case 错绑、normalized reference、suite/Artifact render、GLSL/provenance、run-evidence、交叉字段矛盾、重算 report hash 和 exclusive-create；旧正式 run 只读生成 `report-capability-v2.json`，10/10 输入/候选/人工选择/标签锚点通过，汇总为 initial-win `4`、capability unsupported `5`、initial-win 交集 `4`，canonical report hash 为 `ec8c3cd8a691ebcf1a990d8c326e874835c57cc3a26ea09bef5da3ef63b6ed61`。错误的早期 v1 报告只读保留但不再作为当前证据。未调用真实模型、未修改旧 suite，证据耐久性为 `partial`。
- 2026-07-17 M6.2 admission 定向回归覆盖默认生产兼容、model 不受影响、supported 正向、multi/ring/required-layer unsupported、missing/unknown/candidate hash mismatch fail-closed、runtime verifier unavailable、topology/hole 矛盾、只传 evidence、事实层 reason 优先、protected-region 继续生效、strict config/compile/Artifact replay、重算 hash 语义篡改和 exclusive-create。旧正式 run 当前只认 `report-admission-v2.json`：6 个 affine baseline accepted、5 个 admission rejected、4/4 initial-preferred unsupported rejected、1/1 supported admitted，canonical hash 为 `a3168f2b7c2cb73a20e2ae262db5f4ca7b420eb6383a33cf2c978c24ddc2b1ab`；v1 报告作为错误产物只读保留。v2 固定 `production_enabled=false`，未调用模型、未修改旧 suite/Artifact，证据耐久性为 `partial`。
- 2026-07-19 V2 数据集验证：visible validation 现有 41 张 CC0 实图，基础 30 张外新增 6 张中等金属按钮与 5 张困难爆炸/烟雾样本；三组 FreeGameUI 与 OpenGameArt 来源均以完整 visual family/hash group 隔离，并逐文件核验 SHA-256 与尺寸。关键类实际分母为 multi-instance 11、ring 20、hollow 10、required-highlight 16、required-rim 26、required-outline 36；困难有机噪声边界明确不伪标为当前 Genome primitive。来源与标注边界见 `benchmarks/png_to_shader_v2/sources/`。release-held-out 不复用此开发可见语料，仍保持封存前空集。
- 2026-07-20 V2 候选池验证：974/974 PNG 可解码、894 个唯一 SHA-256、20 组/80 个重复文件，与已登记图片碰撞为 0；Kenney 两个 ZIP 通过内容测试，CC0 来源台账与总览已落库。本结果不改变 release-held-out `0/10` 或 readiness 结论。
- 2026-07-20 V2.1 严格门禁基线：真实 `fixture/no-model` runner 为 `ready=true`，51/51 Intent 合法，current 10 为 10/10、validation/instance exact 为 41/41，六类 recall/F1 与 macro 均为 1.0；outcomes SHA-256 为 `cbd83ca7cfa9eb818e906b34e40027180f8531eeae9b12e50f79245c7d492918`。闭集/Intent/runtime/Candidate/recovery/admission 定向门禁通过；`make check` 通过 668 个单元测试、docs-check、LangGraph validate（1 个 V1 Graph）和前端生产构建。未调用真实模型、未读取或填充 release-held-out、未启用 production admission。
- 2026-07-21 V2.3 ownership/radial 聚焦基线：producer/Intent/Compiler/RenderedStructure 38 项、Candidate/recovery/Graph/Service 112 项、gate/replay/runner/Node Lab 31 项、contracts/runner/seed/runtime/package 60 项通过；相关 47 个源码 strict mypy、compileall、Ruff、docs-check、LangGraph validate 通过，沙箱外单例 production Graph actual Chromium 为 1 passed。segmented Service invoke 与 restart resume 均 finalized。未运行全量、51 例 strict gate 或真实模型。旧 strict-v3 的 config/outcomes/report hash `2b6666...`/`a8a543...`/`4e58a5...` 仅作 V4 前历史诊断。
- 2026-07-17 exploratory Ultra dataset 使用固定 `benchmarks/ultra_dataset/manifest.yaml` 和产品 `/api/shader/generate` 完成 16 例真实模型链路：canary `ultra-dataset-20260717T024627Z` 2/2 成功，双进程主批 `ultra-dataset-20260717T025921Z` 12/14 首次成功、2 例 504 证据保留，串行 retry `ultra-dataset-20260717T033759Z` 2/2 成功；按 retry 覆盖后的最终结果为 16/16 成功、5/16 达标、202 次模型调用、平均 loss `0.19546495`，runtime-policy SHA-256 均为 `5b7853a509e3901c0f24483b57af108108aa9f38ae48051373d37457fcc18421`。runner/manifest 8 项单测、Ruff 和 diff-check 通过；完整逐例响应、manifest、metrics、final-render 与 SHA-256 保存在被忽略的本地 `output/benchmarks/ultra-dataset/`，耐久性仍为 `partial`。
- 同日 `pink_gel.png` High 新项目首次 run `3b2c1dbf-f507-4ebe-ba2d-24e666fcb915` 在 Memory 阶段返回可重试 `503 memory_unavailable`，PostgreSQL 账本正确记录 `failed`。健康检查恢复后原样重试 run `32124a4c-93a4-47aa-ba03-328a2c418fb8` 成功：3 次模型调用、3 个候选、一次 compile failure/repair、100.42 秒，`candidate-0003` Total loss `0.04154829`，客户端/服务端 Render RMSE `0.0000`，成功账本 error/critical 日志为 0。该结果覆盖 compile-repair 恢复分支，但由于自动分数已达冻结阈值，仍未进入 Critic/visual-refine 分支。
- 数据库和浏览器追加验收通过：`make test-memory-postgres` 为 1 passed；产品 `npm --prefix frontend run e2e:procedural-v1` 与 Node Lab `make test-node-lab-ui` 均通过，使用隔离资源且没有真实模型调用。
- `H02` 权威验收命令 `make benchmark-node-lab-ai-off`、`make benchmark-node-lab-model`、`make test-node-lab-ui` 均通过；本次离线五角色 run id 为 `node-lab-model-78520d334d0a`。这些结果只覆盖 AI-off、离线 fixture 和页面流程，不构成真实模型质量证明。
- 2026-07-16 wheel 审计确认 `backend.sql`、V1 嵌套包、Prompt、许可证和三个 `py.typed` 均进入发布包且无 package-discovery 警告；独立导入探针确认 `shaderforge.contracts`、`agent.app.contracts.llm` 与 `agent.app.lab.models` 不再 eager-load Renderer、Runner、Playwright 或 V1 Agent 契约，同时根包兼容导出的对象 identity 保持不变。
- 2026-07-15 离线基线：产品 AI-off 10 例 smoke `m5-20260715T155850Z` 完成且质量 gate 按设计为 `not-evaluated`；最终源码指纹下的 Node Lab capability/node、scenario、Renderer warm、transport 四组 suite-run `node-lab-21616b814e33`、`node-lab-164f8c9687af`、`node-lab-5a386968babc`、`transport-6176202caddc` 全部 passed；五模型角色 fixture `node-lab-model-fccee6297b34` passed。未调用真实模型。
- 发布基线：`m5-20260715T023445Z` 自动检查 12/12 通过，人工完整度 10/10 通过；评审原始 JSON SHA-256 为 `74e02ac9e423637938b182fa3767c53c148058ec1dfcd4adf147c0e1191cc782`，人工偏好门禁失败，产物必须只增不改保留。
- Node Lab 真实模型基线：`node-lab-model-real-review-20260715-v1` 使用 `dashscope:qwen3.7-plus`，五角色 5/5 completed/correctness passed、0 timeout、0 JSON repair；首次 Parser 通过率为 `0.8`，Initial Author 依赖一次受限本地 fixed-binding 修复，不得表述为模型原始输出 5/5 合法。

## 最近重要变更

- 2026-07-21：完成 deterministic ownership 与 segmented-ring raw/radial evidence 要素闭包；Diagnostic/RenderPlan V3、Rendered Evidence/Verification V4、metric v3.2 及 Measurements/Intent breaking 版本已贯穿恢复和 formal gate。单例 actual Chromium 可形成 Candidate；segment primitive 与新 51 例效果门禁留待后续。
- 2026-07-20：F03/V2.2 通过：冻结 16 类 typed Genome、三个确定性 Seed、typed AST/stdlib/SourceMap/CompilationBundle、typed Candidate 重放和静态/WebGL 门禁；F03 继续进入 V2.3 Graph/State/Routing/Harness，production admission 仍关闭。
- 2026-07-17：完成 M6.2 capability-v2 与 admission 离线增量：版本化绑定正式 run 的输入、config/compile、结构标签与 Candidate 身份，抽取通用纯策略并用真实 Selector strict replay v2；5 个 unsupported seed 被 opt-in admission 拒绝且 `ellipse_gradient` 正向保留。V1 缺 runtime verifier，production admission 默认关闭，不改 Prompt/Graph 或发布结论。
- 2026-07-19：补齐 V2 visible validation 的中等/困难覆盖：FreeGameUI 金属按钮提供渐变、rim/outline 与高光，OpenGameArt CC0 爆炸图集与固定像素裁切提供多实例烟雾、色团、Glow 与叠加。validation 现为 41 张；有机噪声边界明确作为当前 Genome/Compiler 缺口保留，release-held-out 仍待独立封存。
- 2026-07-20：F02/V2.1 通过：完成 deterministic conformance、required-layer 闭集、runtime/Candidate/provenance 重启恢复及 sealed Selector adapter；F03 接替为唯一 active。

## 历史索引

- 结构化整理前的完整原始记录：`docs/progress/archive/PROGRESS-2026-07-07--2026-07-15.md`。
- 截止 2026-07-10 的阶段总结：`docs/progress/archive/STAGE-SUMMARY-2026-07-10.md`。
- 验收证据及耐久性状态：`docs/evidence/registry.json`；维护规则见 `docs/evidence/README.md`。
- 代码演进以 Git commit 为准；本地 benchmark、盲评和逐例失败原件仍保存在 `output/benchmarks/`，只有 registry 标为 `durable` 的文件才可视为跨环境可获得。历史快照只用于审计，不得作为当前事实来源。

## 维护规则

- 会话结束时原地刷新“当前状态、下一步、未解决缺口、当前验证基线”；例行重复验证覆盖旧基线，不新增逐会话流水账。
- 只有功能状态、架构/契约、质量门禁、阶段里程碑或重要未决缺口发生变化时，才在“最近重要变更”新增一条；该区最多保留 5 条。
- 主文件 UTF-8 体量上限为 20,000 bytes。超出条目移入 `docs/progress/archive/`，归档必须注明时间范围和“非当前事实”，且不得删除冻结失败证据。
