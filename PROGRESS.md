# 进度

最后更新：2026-07-22

> 本文件是有界的当前交接页，不是逐会话追加日志。功能状态以 `docs/FEATURES.md` 为准，长期取舍以 `docs/DECISIONS.md` 为准，完整旧记录见 `docs/progress/archive/`。

## 当前状态

- 产品与 LangGraph 已收敛为 PNG-to-Shader V1：`langgraph.json` 只注册 `png_to_shader_v1`，产品只保留 `procedural_v1`；旧基础对话图、legacy 生成和独立 Review 入口已删除。
- 全部 V1 Node 工厂及其支持实现已收拢到 `agent.app.nodes.png_to_shader_v1` 功能命名空间，内部按 `model`、`deterministic` 和 `integrations/node_lab` 分层；`nodes/` 根目录不再保留实际绑定 V1 的伪通用模块，两个纯 decision callable 继续与条件边规则共同归属 Graph routing。
- `F09` 已完成 M0–M5、M6.0 可靠性修复、M6.1 离线质量改进及 Node Lab API v1 阶段 A–D。Node Lab 现分为 transport-free 的顶层 `nodelab` typed 内核和可独立启动的 `nodelab_service` FastAPI 进程；服务默认空安全，通过受信任的启动时 Application factory 注入任意 Pipeline 的 Node/Capability/Fixture/Suite/reducer，不导入 Agent、Backend 或 ShaderForge。普通 JSON-safe Node 无需改造即可接入，复杂副作用只需在 Pipeline 保留薄 Adapter；V1 的 20 节点、八个 capability 和三个 AI-off suite 只作为显式插件/benchmark，不再是 HTTP 默认语义。
- Backend 已在组合根冻结产品数据库、日志与 CORS 配置，Node Lab Route、Schema、Service、环境开关和生命周期已全部移出；数据库和 Agent Memory 的半初始化、取消及关闭失败均进入补偿清理。主 CI 使用 Python/Node 锁定安装执行完整 `make check` 与 `mypy --strict src backend`，普通 Integration 不持有真实模型凭据。
- 仓库结构边界已加固：五模型角色离线 benchmark 与在线 Agent Service 分离，共享测试样本归入 `tests/fixtures`，轻量包导入不再 eager-load 浏览器/Runner/V1 契约；前端统一通过 API client 访问后端，Node Lab 只对选中步骤加载完整明细。
- 正式 run `m5-20260715T023445Z` 的自动质量检查 12/12 通过，但独立人工盲评 final/initial/tie 为 `3/4/3`，final 偏好率 `30%` 低于冻结的 `50%` 门槛；最终 gate 为 `failed`，F09 继续 active、灰度 no-go。
- V2–V5 实施方案已完成拆分后正式 Review，形成总纲、四个版本方案和 Review 报告；结论为 Conditional Go，仅允许在 F09 M6.2 证据冻结后进入 V2.0 契约冻结，不代表 F02–F05 或异步产品能力已经实现。

## 当前 active 功能

- `F09`：PNG 转无贴图 Shader Agent V1。权威状态、验证命令和发布证据见 `docs/FEATURES.md`；在新的真实模型 run 和独立人工门禁通过前，不得标记 `passing`。

## 下一步

- 执行 `F09 M6.2 人类偏好对齐`：在 Node Lab 中优先诊断 topology、实例数量、轮廓/镂空、高光/阴影语义层保留，以及 Selector 的结构感知不足。
- 重点解释 deterministic affine seed 为何在自动 objective 上晋升，却在 `rimmed_disk`、`arc_highlight_orb`、`dual_disks`、`pink_gel` 上被人工选择 initial；不得加入 benchmark case、manifest、golden 或 gate 特判。
- 离线回归完成后，必须使用新 suite-run-id、完整硬预算重新运行真实模型 M5，并进行新一轮独立盲评；不得复用当前人工结果宣称修复通过。
- M6.2 诊断与证据冻结后，按 `human_doc/png-to-shader-v2-v5-plan/` 从 V2.0 开始：先冻结 TargetHypothesis、RequestConstraintSet、Artifact、Genome Hash、Candidate、State/Budget Schema、golden fixture 和数据 Manifest；不得跳过 V2.0 同时启动 Intent Prompt、Compiler 或新 Graph。

## 未解决缺口

- 自动 objective 尚不能充分保护人类偏好的视觉拓扑、实例数量和高光/阴影层次，这是 F09 当前的质量发布阻塞项。
- 正式 M5 与 Node Lab real-model 的完整报告仍位于被忽略的本地 `output/benchmarks/`；`docs/evidence/registry.json` 已登记摘要、字节数和 SHA-256，但耐久性仍为 `partial`。在完整脱敏证据进入 Git LFS、Release 或不可变对象存储前，不能仅凭本地路径独立复验。
- 服务端仍是阻塞式 API；浏览器停止等待不等于服务端取消。端到端 deadline、任务化/cancel、outbox/reaper、多 worker 分布式锁和真实发生顺序事件属于后续可靠性设计，不与 M6.2 混写。
- V2–V5 当前只有 Review 通过的设计契约；release-held-out 清单、canonical hash golden、Oracle perturbation、SearchJournal failpoint、HumanEvaluation 和 V5 BenchmarkManifest 尚未实现或冻结，因此所有对应功能继续保持 `not_started`。
- Node Lab 当前已是独立进程/部署边界，但仍与内核一起由 `shadergen` wheel 发布，factory 和 Node 必须安装在同一 Python 环境；跨进程/多语言 Remote Executor、远程鉴权、队列取消和资源隔离协议尚未设计。

## 当前验证基线

- 2026-07-22 当前工作树通过 `make check`：429 个 Python 单元测试、`docs-check`、LangGraph validate（1 个 Graph）与 Frontend production build 均成功；Integration 为 27 passed、1 skipped，全仓 Ruff、`mypy --strict src backend` 与 `git diff --check` 通过。独立 `nodelab-service` 进程 smoke 在未启动 Backend/Agent/数据库时完成 health 与 LabRun 创建；`uv build --wheel` 确认 `nodelab_service`、CLI entry point 和 `py.typed` 进入发布物，旧 Backend Node Lab 文件不在 wheel。未运行真实模型。
- 数据库和浏览器追加验收通过：`make test-memory-postgres` 为 1 passed；产品 `npm --prefix frontend run e2e:procedural-v1` 通过；2026-07-22 Node Lab 工作台前端重构后，`npm --prefix frontend run build` 与 Node Lab `make test-node-lab-ui` 通过，使用隔离资源且没有真实模型调用。
- `H02` 权威验收命令 `make benchmark-node-lab-ai-off`、`make benchmark-node-lab-model`、`make test-node-lab-ui` 均通过；独立服务拆分后 capability/node、scenario、warm resource、transport 和离线五角色 run id 分别为 `node-lab-aa58da7bb2ed`、`node-lab-82357bb10add`、`node-lab-261c67b93db0`、`transport-c84acfa237ed`、`node-lab-model-3ec9ff76d542`。这些结果只覆盖 AI-off、离线 fixture 和页面流程，不构成真实模型质量证明。
- 2026-07-16 wheel 审计确认 `backend.sql`、V1 嵌套包、Prompt、许可证和 typed 标记均进入发布包且无 package-discovery 警告；独立导入探针确认 `shaderforge.contracts`、`agent.app.contracts.llm` 与 `nodelab.models` 不再 eager-load Renderer、Runner、Playwright 或 V1 Agent 契约，同时根包兼容导出的对象 identity 保持不变。
- 2026-07-15 离线基线：产品 AI-off 10 例 smoke `m5-20260715T155850Z` 完成且质量 gate 按设计为 `not-evaluated`；最终源码指纹下的 Node Lab capability/node、scenario、Renderer warm、transport 四组 suite-run `node-lab-21616b814e33`、`node-lab-164f8c9687af`、`node-lab-5a386968babc`、`transport-6176202caddc` 全部 passed；五模型角色 fixture `node-lab-model-fccee6297b34` passed。未调用真实模型。
- 发布基线：`m5-20260715T023445Z` 自动检查 12/12 通过，人工完整度 10/10 通过；评审原始 JSON SHA-256 为 `74e02ac9e423637938b182fa3767c53c148058ec1dfcd4adf147c0e1191cc782`，人工偏好门禁失败，产物必须只增不改保留。
- Node Lab 真实模型基线：`node-lab-model-real-review-20260715-v1` 使用 `dashscope:qwen3.7-plus`，五角色 5/5 completed/correctness passed、0 timeout、0 JSON repair；首次 Parser 通过率为 `0.8`，Initial Author 依赖一次受限本地 fixed-binding 修复，不得表述为模型原始输出 5/5 合法。

## 最近重要变更

- 2026-07-22：Node Lab 前端重构为“页面容器 + `frontend/src/components/nodelab/` 纯展示组件”的三栏工作台：顶部状态栏直接展示 health 的 pipeline_id、节点/capability/suite 计数和真实模型门禁，服务不可达给出启动指引，空 Application 在目录内显示 factory 接入引导，输入 JSON 带实时合法性提示；三栏布局桌面优先，1100px 以下才退化为两栏/单栏。同时修复 Artifact 上传误用产品 API base URL 的缺陷。产品页面与 Node Lab API 契约不变。
- 2026-07-22：Node Lab HTTP 从产品 Backend 拆为可独立运行的 `nodelab_service`，默认端口 8090、默认空安全 Application，并通过仅启动时可配置的可信 factory 接入项目 Node；前端使用独立 base URL，Backend 删除全部 Lab route/schema/service/config，V1 只保留显式插件路径。
- 2026-07-22：完成 Node Lab 任意 Node 薄接入与证据加固：移除通用包对 ShaderForge 存储和固定 benchmark 环境的依赖，新增 Provider Builder、三类标准 Executor、Command-like 归一化、完整 JSON Schema 与可注入 reducer；委托实现/reducer 源码和归并 State 均进入 fingerprint，reducer 失败可持久化；warm benchmark 改为通用 resource 语义，V1 行为和 Graph 不变。
- 2026-07-21：完成 Node Lab 通用化与包边界重构：Harness 移除 V1 pipeline/capability/fixture/suite 默认语义，并从 `agent.app.lab` 迁为顶层 `nodelab` typed Python 包；ShaderForge Executor 与固定 suite 归属 V1 功能命名空间，Agent Service 只负责组合，暂不拆独立部署服务，V1 行为和 Graph 不变。
- 2026-07-16：完成 V2–V5 实施方案的拆分后正式 Review，关闭多假设、ConstraintSet、版本化 State、SearchJournal、SelectionSnapshot、双 fencing 和可重复量化协议等设计缺口；结论仅允许从 V2.0 契约冻结开始实施。

## 历史索引

- 结构化整理前的完整原始记录：`docs/progress/archive/PROGRESS-2026-07-07--2026-07-15.md`。
- 截止 2026-07-10 的阶段总结：`docs/progress/archive/STAGE-SUMMARY-2026-07-10.md`。
- 验收证据及耐久性状态：`docs/evidence/registry.json`；维护规则见 `docs/evidence/README.md`。
- 代码演进以 Git commit 为准；本地 benchmark、盲评和逐例失败原件仍保存在 `output/benchmarks/`，只有 registry 标为 `durable` 的文件才可视为跨环境可获得。历史快照只用于审计，不得作为当前事实来源。

## 维护规则

- 会话结束时原地刷新“当前状态、下一步、未解决缺口、当前验证基线”；例行重复验证覆盖旧基线，不新增逐会话流水账。
- 只有功能状态、架构/契约、质量门禁、阶段里程碑或重要未决缺口发生变化时，才在“最近重要变更”新增一条；该区最多保留 5 条。
- 主文件 UTF-8 体量上限为 20,000 bytes。超出条目移入 `docs/progress/archive/`，归档必须注明时间范围和“非当前事实”，且不得删除冻结失败证据。
