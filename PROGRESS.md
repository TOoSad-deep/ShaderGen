# 进度

最后更新：2026-07-24

> 本文件是有界的当前交接页，不是逐会话追加日志。功能状态以 `docs/FEATURES.md` 为准，长期取舍以 `docs/DECISIONS.md` 为准，完整旧记录见 `docs/progress/archive/`。

## 当前状态

- 产品默认仍是 PNG-to-Shader V1；`langgraph.json` 现并行注册 `png_to_shader_v1` 与 `png_to_shader_min`。Backend/Frontend 保留默认 `procedural_v1`，并增加显式实验模式 `scene_mvp`；旧基础对话图、legacy 生成和独立 Review 入口保持删除。
- 全部 V1 Node 工厂及其支持实现已收拢到 `agent.app.nodes.png_to_shader_v1` 功能命名空间，内部按 `model`、`deterministic` 和 `integrations/node_lab` 分层；`nodes/` 根目录不再保留实际绑定 V1 的伪通用模块，两个纯 decision callable 继续与条件边规则共同归属 Graph routing。
- `F09` 已完成 M0–M5、M6.0 可靠性修复、M6.1 离线质量改进及 Node Lab API v1 阶段 A–D。Node Lab 由生产 `NodeProvider` 暴露 20/20 节点，Harness 不维护具体 Graph/Node 的平行语义；`H02` 的 AI-off、五模型角色离线 fixture 与页面验收均已通过并标记为 `passing`。
- Backend 已在组合根冻结数据库、日志、CORS 与 Node Lab 配置；数据库和 Agent Memory 的半初始化、取消及关闭失败均进入补偿清理。主 CI 使用 Python/Node 锁定安装执行完整 `make check` 与 `mypy --strict src backend`，普通 Integration 不持有真实模型凭据。
- 仓库结构边界已加固：五模型角色离线 benchmark 与在线 Agent Service 分离，共享测试样本归入 `tests/fixtures`，轻量包导入不再 eager-load 浏览器/Runner/V1 契约；前端统一通过 API client 访问后端，Node Lab 只对选中步骤加载完整明细。
- 正式 run `m5-20260715T023445Z` 的自动质量检查 12/12 通过，但独立人工盲评 final/initial/tie 为 `3/4/3`，final 偏好率 `30%` 低于冻结的 `50%` 门槛；最终 gate 为 `failed`，F09 继续 active、灰度 no-go。
- 用户已确定《PNG 转无贴图 GLSL Agent—目标架构（详细版）》取代旧 V2–V5 路线，成为 F09 后续算法与演进的权威目标；旧 V2–V5 和最初架构 SVG 均保留为历史/概念参考，不再覆盖后续决策、当前架构或实现事实。该决定不表示详细版能力已经实现，也不改变 F02–F05 的 `not_started` 状态。
- 《PNG 转无贴图 GLSL Agent—最小骨架（快速版）》已完成固定模板扩展：`png_to_shader_min_scene_v3` 严格区分 solid/radial/linear 并校验 circle 等轴；`png_to_shader_min_template_v3` 使用四个紧凑 feature 槽与 15/16 fragment uniform vectors，支持六类主体内外效果；Refine 支持原子 replace feature/color-field；`min_scene_composite_v3` 改用 global/foreground/background/geometry/edge/worst-tile 通用分量。MAE/loss 目标和 fast/balanced/high/manual render/LLM/Refine 预算由严格 YAML 加载，当前独立实验为 `0.04/0.02` 与 `48/2/1`、`96/4/2`、`640/9/9`、manual `1000/32/30`；manual 不进入冻结 gate，同 run 单 prepared program 和 12 节点 Graph 拓扑不变。
- `scene_mvp` 新增运行时可观测：前端预生成 `run_id` 随 POST 发送并轮询 `/api/shader/runs/{run_id}/progress` 增量事件与 `/progress/render` 实时渲染帧，运行中展示 12 节点时间线、render/LLM/Refine 预算、best loss/MAE 对 target、路由决策和事件流；Agent service 以 `astream(stream_mode="updates")` 产出严格白名单事件（图片/Scene/GLSL/渲染字节不进入事件），事件缓冲是单进程内存语义、重启即失，终态 `agent_events` 账本路径不变。
- `scene_mvp` 固定模板扩展已完成无模型工程验收：真实 Chromium 证明三类颜色场、六类 feature、四槽、prepared/baked 像素语义与固定 program 签名；固定 7 例按外部 `png_to_shader_score_v1` 对照 v2 fallback 为 6/7 改善，其余 global/ROI/bbox 回归低于预设容差。该 v3 只表示 Scene/template/metric 各自顺序升级，不是旧 V3 Oracle/Search 阶段；真实模型和人工质量门禁仍未执行。
- 已完成真实单例 run `85506ab8-12c4-4a20-8940-824875ea0f97` 的只读复盘：流程成功但以 `render_budget_exhausted` 停止、`target_reached=false`；五次 Refine 全部被拒绝，最终四槽中只有 shadow 实际生效。根因与待验证建议已沉淀到 `docs/superpowers/specs/2026-07-23-scene-mvp-run-85506ab8-agent-optimization.md`，该单例和实验配置不构成冻结 benchmark 或发布证据。
- 已实施单例复盘建议的 P0 最小增量：YAML 强制声明冻结 benchmark 或独立实验，冻结身份对 D058/D059 配置漂移 fail closed；Refine 获得 worst-tile signed residual、active feature 和最近拒绝摘要，Patch 以规范 SHA-256 脱敏审计。非重复合法 typed Patch 在独立 branch 内使用最多 12 次现有 draw 做范围受限成熟，只有 matured loss 严格改善才提交；重复、非法和 Renderer 失败不能污染 best。实现报告见 `docs/superpowers/specs/2026-07-23-scene-mvp-agent-optimization-implementation-report.md`。
- 私有 replay bundle v1（D066）已落地并经审查修订：typed Patch 与 anchor/candidate/raw/matured 证据只写 `private/replay/`，读回 verified fail-closed（路径/hash/size/schema 校验、render/bundle write-once、分域与身份聚合）；公开 manifest 仅 hash 级摘要，Graph 拓扑与预算不变。
- 已完成 run `79f51d8a-1aaa-4f92-b806-cd8a44ddf297` 联合评估、P0 诊断及 D067 maturity 12/32 单因素重放：原 run loss `0.048350` 未达 `0.02` 且缺关键高光；两个冻结合成 feature fixture 的前 11 实际 draw 完全同前缀，87 次 Chromium draw/0 模型调用下 Arm-32 clean rescue 1 例，gate=`budget32_supported`、每 rescue 额外 20 draw。该 local/partial 小样本不授权改生产 12 draw，报告见 `docs/superpowers/specs/2026-07-23-scene-mvp-maturity-budget-replay.md`。
- 用户已确认后续分支会调整 Feature 逻辑和架构；旧 `png_to_shader_min_scene_v3` 上的真实模型实验、Patch 12/32 重放、Prompt 优化、固定 7 例 benchmark 与盲评不再执行。D063–D067 只保留为历史机制证据；新分支完成差异审计和基线冻结后重建正式质量 benchmark（D068）。
- 固定 7 例 scorer 校准已完成：geometry-first 在 462 次真实 Chromium draw 中令 7/7 的 geometry、内部复合 loss 和外部 objective 改善，但 6/7 存在至少一个局部补偿，`ellipse_gradient/upper_color` 与 `arc_highlight_orb/highlight_upper_left` 两例超过实质回退阈值。看片同时确认整体轮廓/错误 shadow 多数改善，但 rim、弧形高光和 pink-gel 双高光仍未恢复；结论是保留 geometry 的整体作用，下一步验证多尺度 tile no-regression guard。报告见 `docs/superpowers/specs/2026-07-23-scene-mvp-fixed-7-scorer-calibration.md`。
- 多尺度 tile no-regression guard A/B 已完成，其 offline replay 形式未通过预声明接入门禁：在固定 7 例、相同 geometry-first 候选机制和相同 draw 预算（455 次真实 Chromium draw、0 模型调用）下，Arm A（total_loss 严格改善即接受）的 7 例 ROI 回退全部低于冻结 `0.01` 容差，两个 watch ROI 无回退可保护；Arm B 在预声明 `0/0.001/0.0025/0.005/0.01` 容差上要么零保护收益，要么连首个改善候选都整体拦截（`t≤0.005` 误拒 4/7，`t=0.01` 仍误拒 2/7），吞掉 `color_lobes` 等明确改善；live guard 轨迹未验证。本离线形式不采用，生产未改，报告见 `docs/superpowers/specs/2026-07-23-scene-mvp-tile-guard-ab.md`。
- acceptance live 单因素直接 A/B 已完成：两臂共享候选生成器、阶段顺序、每 stage 32 draw 预算与同一 fallback 快照，各自 live 搜索（各 448 候选、无 replay，共 903 draw、0 模型调用）。strict total-loss 在 6/7 内部 total loss 与外部 objective 严格更优（`solid_circle` 相同），aggregate 双优，且不复现 geometry-first 的两例实质 ROI 回退（`upper_color +0.019894`、`highlight_upper_left +0.011853`）；在固定 7 例/32+32 draw 契约内实质回退归因于 geometry-first 诊断语义（唯一实验变量），不外推。生产 acceptance 已确认本来即为 strict total-loss、未发生任何算法改动，见 D064/D065 与 `docs/superpowers/specs/2026-07-23-scene-mvp-acceptance-live-ab.md`。
- run `9d10b919-25f6-41a2-a2cf-e88c23ad78be` 的 `GraphRecursionError` 已正式修复；Refine 后不再重复完整 base/feature sweep，合法路径公式为 `9+2F+6R`，high/manual 最坏分别为 65/197 步、注入 69/201。配置拒绝超过 256 的组合，异常保持 fail-closed，失败账本只保存安全进度快照。
- 已形成 `docs/superpowers/plans/2026-07-22-png-to-shader-v1-retirement.md` 分阶段退役计划：先抽离 min 仍使用的消息/WebGL1 共享契约，再分别完成 min benchmark、Node Lab、Memory 与默认产品路径门禁，最后由独立下线决策授权删除 V1 可执行代码；该计划不授权当前立即删除，历史决策、benchmark 和失败证据继续只增不改保留。

## 当前 active 功能

- `F09`：PNG 转无贴图 Shader Agent V1。权威状态、验证命令和发布证据见 `docs/FEATURES.md`；在新的真实模型 run 和独立人工门禁通过前，不得标记 `passing`。

## 下一步

- 当前分支停止继续运行旧 Feature 契约的真实模型 independent experiment、真实 Patch 12/32 重放、旧 Prompt 优化、固定 7 例 benchmark 与盲评；生产 maturity 保持 12 draw，不根据旧架构的小样本证据调整。
- 切换到用户后续 Feature 分支后，先审计 Feature Schema、模板、typed Patch、Prompt、scorer、Graph 路由、预算和证据版本的差异，再决定哪些 D058–D067 基础设施可以迁移；不得直接继承旧质量结论。
- 新 Feature 方案稳定后，重新冻结与其匹配的 manifest、目标、预算和报告格式，再运行显式真实模型 benchmark 与独立匿名人工门禁；通过前不得调整外部 baseline 容差或把 F09 标为 passing。
- 后续再独立评估 CMA-ES/2000 draw 搜索；当前小批确定性搜索与单 Patch 12 draw 成熟已验证参数接线、累计接受、预算记账和 best 单调性，但实验 high=640/manual=1000 均不得冒充冻结 high=160，也不替代更大搜索空间的时延、取消和质量 benchmark。
- `scene_mvp` 继续作为显式实验模式，`procedural_v1` 保持默认；在新 benchmark 与人工门禁通过前不删除 V1、Memory、Node Lab 或既有证据。
- V1 退役准备必须作为独立增量执行：优先完成通用消息/WebGL1 契约解耦和机器可读依赖 inventory；固定模板扩展、质量 benchmark 与大规模删除不得混在同一改动中。

## 未解决缺口

- 固定生产方向顺序的两个旧架构合成 Patch fixture 表明 32 draw 可降低 loss 并 clean rescue overfit 候选，但 Arm-32 内部不劣近乎由前缀+strict best 构造保证；该证据不再补跑旧架构真实 Patch，也不能直接迁移到后续 Feature 方案或修改生产 maturity 策略。
- replay bundle 已含 steps 聚合的模型/Prompt 身份与可重放 typed Patch（D066），历史 run（含 79f）不可补值；bundle 仍为 `local_ignored`，缺 durable 路由和源码 revision。进程若在 patch draft 与 record 之间崩溃会留下 write-once orphan，原 run_id 重试 fail-closed，须改用新 run_id；Progress snapshot 仍无累计 LLM/Refine counters。
- 旧 `scene_mvp` v3 未运行真实模型 7 例 benchmark 和独立人工盲评，且因后续 Feature 架构调整不再补跑；当前 6/7 改善只属于旧确定性感知 fallback，不能作为新架构或发布通过证据。新 Feature 方案仍须重建并通过真实模型 benchmark 与独立人工门禁。
- `scene_mvp` 已具备 prepared program、严格 typed uniform 热上传、原始 RGB 热路径和 100 draw 显式性能探针，但尚无 CMA-ES、2000 draw 生产预算、优化中断/恢复或对应质量证据，不能声称已完成性能版优化器。
- 当前产品接入覆盖显式模式、Backend/Frontend、账本摘要、三种 Artifact、分档 render/LLM/Refine 预算和专用浏览器 E2E；Memory、Node Lab、独立 benchmark 和真实模型质量验证尚未迁移，V1 垂直切片与冻结失败证据不得删除。
- `scene_mvp` 的专用浏览器 E2E 使用隔离假 API 验证“达标”和“流程完成但质量未达标”两种成功响应都会显示结果；它不证明真实模型视觉质量、数据库耐久性或线上网络配置。
- `min_scene_composite_v3` 已去除亮度分位数伪语义；固定 7 例证明 geometry 对整体轮廓/错误 shadow 有效，但 aggregate loss 会补偿局部结构回退，且不保证 rim/arc/双高光出现。4×4/8×8 tile guard 离线 replay 形式未通过接入门禁（无可保护回退且高误拒），live guard 轨迹未验证；两例实质 ROI 回退已归因到 geometry-first 诊断语义（D064），生产 acceptance 经 D065 核实自始为 strict total-loss、无切换对象，真实模型固定 7 例和独立人工偏好仍是发布阻塞项。
- 正式 M5 与 Node Lab real-model 的完整报告仍位于被忽略的本地 `output/benchmarks/`；`docs/evidence/registry.json` 已登记摘要、字节数和 SHA-256，但耐久性仍为 `partial`。在完整脱敏证据进入 Git LFS、Release 或不可变对象存储前，不能仅凭本地路径独立复验。
- 服务端仍是阻塞式 API；浏览器停止等待不等于服务端取消。端到端 deadline、任务化/cancel、outbox/reaper、多 worker 分布式锁和真实发生顺序事件属于后续可靠性设计，不与 M6.2 混写。
- 目标架构详细版当前只有设计方案；多假设、置信度标定、特征块调度、硬约束、两层一致性、沙箱和完整评测体系均未实现。旧 V2–V5 中未落地的契约不再是当前阶段门禁，但相应能力仍不得表述为完成。

## 当前验证基线

- 2026-07-23 当前工作树通过 `make check`：571 个 Python 单元测试、`docs-check`、2 个 Graph validate 与 Frontend build 均成功；Integration 为 39 passed/2 skipped（未配置 PostgreSQL）。high=`640/9/9`、manual=`1000/32/30` 递归预算/Progress API 为 6 passed。`mypy --strict src backend`、`git diff --check`、`ruff check` 全仓通过。maturity 12/32 重放为 87/87 Chromium draw、0 模型调用，gate=`budget32_supported`，权威报告 SHA-256=`ed8caf780554ef527935c82e78bbf4926e1b09d0beb0e9df47f3db396021f15d`。
- v3 Renderer/质量基线：真实 Chromium 4 passed/1 显式性能探针 skipped，覆盖 prepared/baked 一致、三颜色场、六 feature、四槽和固定签名；固定 7 例质量回归 2 passed，按外部 `png_to_shader_score_v1` 对照 v2 fallback 为 6/7 改善，`solid_circle` total-loss 回归 `0.000217` 低于冻结的 `0.001` 容差，所有 ROI 与 geometry 回归均低于 `0.01` 容差。未调用真实模型。
- 数据库和浏览器追加验收通过：`make test-memory-postgres` 为 1 passed；产品 `npm --prefix frontend run e2e:procedural-v1` 与 Node Lab `make test-node-lab-ui` 均通过，使用隔离资源且没有真实模型调用。
- `H02` 权威验收命令 `make benchmark-node-lab-ai-off`、`make benchmark-node-lab-model`、`make test-node-lab-ui` 均通过；本次离线五角色 run id 为 `node-lab-model-78520d334d0a`。这些结果只覆盖 AI-off、离线 fixture 和页面流程，不构成真实模型质量证明。
- 2026-07-16 wheel 审计确认 `backend.sql`、V1 嵌套包、Prompt、许可证和三个 `py.typed` 均进入发布包且无 package-discovery 警告；独立导入探针确认 `shaderforge.contracts`、`agent.app.contracts.llm` 与 `agent.app.lab.models` 不再 eager-load Renderer、Runner、Playwright 或 V1 Agent 契约，同时根包兼容导出的对象 identity 保持不变。
- 2026-07-15 离线基线：产品 AI-off 10 例 smoke `m5-20260715T155850Z` 完成且质量 gate 按设计为 `not-evaluated`；最终源码指纹下的 Node Lab capability/node、scenario、Renderer warm、transport 四组 suite-run `node-lab-21616b814e33`、`node-lab-164f8c9687af`、`node-lab-5a386968babc`、`transport-6176202caddc` 全部 passed；五模型角色 fixture `node-lab-model-fccee6297b34` passed。未调用真实模型。
- 发布基线：`m5-20260715T023445Z` 自动检查 12/12 通过，人工完整度 10/10 通过；评审原始 JSON SHA-256 为 `74e02ac9e423637938b182fa3767c53c148058ec1dfcd4adf147c0e1191cc782`，人工偏好门禁失败，产物必须只增不改保留。
- Node Lab 真实模型基线：`node-lab-model-real-review-20260715-v1` 使用 `dashscope:qwen3.7-plus`，五角色 5/5 completed/correctness passed、0 timeout、0 JSON repair；首次 Parser 通过率为 `0.8`，Initial Author 依赖一次受限本地 fixed-binding 修复，不得表述为模型原始输出 5/5 合法。

## 最近重要变更

- 2026-07-24：D068 停止在旧 Feature 契约上继续运行真实模型实验、12/32 重放、Prompt 优化和固定 7 例质量门禁；D063–D067 保留为历史机制证据，后续分支先做 Feature 架构差异审计，再为新方案重建 benchmark，F09 继续 active/no-go。
- 2026-07-23：D067 完成两个冻结合成 feature Patch 的 12/32 单因素真实 Chromium 重放：前 11 draw 完全同前缀，32 draw clean rescue 1 例，gate=`budget32_supported`、每 rescue 额外 20 draw；local/partial 小样本不授权改生产 12 draw，F09 继续 active/no-go。
- 2026-07-23：D066 落地并经审查修订私有 replay bundle v1：证据只写 `private/replay/`、读回 verified fail-closed、render 复验、RGB/PNG 分域、模型身份 steps 聚合；Graph/预算/scorer/acceptance 不变，F09 继续 active/no-go。
- 2026-07-23：D065 纠正事实——生产 acceptance 自始为 strict total-loss，D064 Arm G 仅是诊断脚本语义、无生产切换对象；五处比较收口到有效域内行为等价的纯函数并补聚焦回归测试，Graph/预算/候选/失败语义不变，F09 继续 active/no-go。
- 2026-07-23：acceptance live A/B 在固定搜索契约内建立因果：strict total-loss aggregate 双优且不复现两例实质 ROI 回退；生产 acceptance 已确认本来即为 strict、未发生算法改动，见 D064，F09 继续 active/no-go。

## 历史索引

- 结构化整理前的完整原始记录：`docs/progress/archive/PROGRESS-2026-07-07--2026-07-15.md`。
- 截止 2026-07-10 的阶段总结：`docs/progress/archive/STAGE-SUMMARY-2026-07-10.md`。
- 验收证据及耐久性状态：`docs/evidence/registry.json`；维护规则见 `docs/evidence/README.md`。
- 代码演进以 Git commit 为准；本地 benchmark、盲评和逐例失败原件仍保存在 `output/benchmarks/`，只有 registry 标为 `durable` 的文件才可视为跨环境可获得。历史快照只用于审计，不得作为当前事实来源。

## 维护规则

- 会话结束时原地刷新“当前状态、下一步、未解决缺口、当前验证基线”；例行重复验证覆盖旧基线，不新增逐会话流水账。
- 只有功能状态、架构/契约、质量门禁、阶段里程碑或重要未决缺口发生变化时，才在“最近重要变更”新增一条；该区最多保留 5 条。
- 主文件 UTF-8 体量上限为 20,000 bytes。超出条目移入 `docs/progress/archive/`，归档必须注明时间范围和“非当前事实”，且不得删除冻结失败证据。
