# 进度

最后更新：2026-07-22

> 本文件是有界的当前交接页，不是逐会话追加日志。功能状态以 `docs/FEATURES.md` 为准，长期取舍以 `docs/DECISIONS.md` 为准，完整旧记录见 `docs/progress/archive/`。

## 当前状态

- 产品默认仍是 PNG-to-Shader V1；`langgraph.json` 现并行注册 `png_to_shader_v1` 与 `png_to_shader_min`。Backend/Frontend 保留默认 `procedural_v1`，并增加显式实验模式 `scene_mvp`；旧基础对话图、legacy 生成和独立 Review 入口保持删除。
- 全部 V1 Node 工厂及其支持实现已收拢到 `agent.app.nodes.png_to_shader_v1` 功能命名空间，内部按 `model`、`deterministic` 和 `integrations/node_lab` 分层；`nodes/` 根目录不再保留实际绑定 V1 的伪通用模块，两个纯 decision callable 继续与条件边规则共同归属 Graph routing。
- `F09` 已完成 M0–M5、M6.0 可靠性修复、M6.1 离线质量改进及 Node Lab API v1 阶段 A–D。Node Lab 由生产 `NodeProvider` 暴露 20/20 节点，Harness 不维护具体 Graph/Node 的平行语义；`H02` 的 AI-off、五模型角色离线 fixture 与页面验收均已通过并标记为 `passing`。
- Backend 已在组合根冻结数据库、日志、CORS 与 Node Lab 配置；数据库和 Agent Memory 的半初始化、取消及关闭失败均进入补偿清理。主 CI 使用 Python/Node 锁定安装执行完整 `make check` 与 `mypy --strict src backend`，普通 Integration 不持有真实模型凭据。
- 仓库结构边界已加固：五模型角色离线 benchmark 与在线 Agent Service 分离，共享测试样本归入 `tests/fixtures`，轻量包导入不再 eager-load 浏览器/Runner/V1 契约；前端统一通过 API client 访问后端，Node Lab 只对选中步骤加载完整明细。
- 正式 run `m5-20260715T023445Z` 的自动质量检查 12/12 通过，但独立人工盲评 final/initial/tie 为 `3/4/3`，final 偏好率 `30%` 低于冻结的 `50%` 门槛；最终 gate 为 `failed`，F09 继续 active、灰度 no-go。
- 用户已确定《PNG 转无贴图 GLSL Agent—目标架构（详细版）》取代旧 V2–V5 路线，成为 F09 后续算法与演进的权威目标；旧 V2–V5 和最初架构 SVG 均保留为历史/概念参考，不再覆盖后续决策、当前架构或实现事实。该决定不表示详细版能力已经实现，也不改变 F02–F05 的 `not_started` 状态。
- 《PNG 转无贴图 GLSL Agent—最小骨架（快速版）》已完成固定模板扩展：`png_to_shader_min_scene_v3` 严格区分 solid/radial/linear 并校验 circle 等轴；`png_to_shader_min_template_v3` 使用四个紧凑 feature 槽与 15/16 fragment uniform vectors，支持六类主体内外效果；Refine 支持原子 replace feature/color-field；`min_scene_composite_v3` 改用 global/foreground/background/geometry/edge/worst-tile 通用分量，并按固定 7 例 fallback 内部 loss 中位数冻结 target loss=`0.04`。Initial/fallback 真实仲裁、三档预算、同 run 单 prepared program 和 12 节点 Graph 拓扑不变。
- `scene_mvp` 新增运行时可观测：前端预生成 `run_id` 随 POST 发送并轮询 `/api/shader/runs/{run_id}/progress` 增量事件与 `/progress/render` 实时渲染帧，运行中展示 12 节点时间线、render/LLM/Refine 预算、best loss/MAE 对 target、路由决策和事件流；Agent service 以 `astream(stream_mode="updates")` 产出严格白名单事件（图片/Scene/GLSL/渲染字节不进入事件），事件缓冲是单进程内存语义、重启即失，终态 `agent_events` 账本路径不变。
- `scene_mvp` 固定模板扩展已完成无模型工程验收：真实 Chromium 证明三类颜色场、六类 feature、四槽、prepared/baked 像素语义与固定 program 签名；固定 7 例按外部 `png_to_shader_score_v1` 对照 v2 fallback 为 6/7 改善，其余 global/ROI/bbox 回归低于预设容差。该 v3 只表示 Scene/template/metric 各自顺序升级，不是旧 V3 Oracle/Search 阶段；真实模型和人工质量门禁仍未执行。
- 已形成 `docs/superpowers/plans/2026-07-22-png-to-shader-v1-retirement.md` 分阶段退役计划：先抽离 min 仍使用的消息/WebGL1 共享契约，再分别完成 min benchmark、Node Lab、Memory 与默认产品路径门禁，最后由独立下线决策授权删除 V1 可执行代码；该计划不授权当前立即删除，历史决策、benchmark 和失败证据继续只增不改保留。

## 当前 active 功能

- `F09`：PNG 转无贴图 Shader Agent V1。权威状态、验证命令和发布证据见 `docs/FEATURES.md`；在新的真实模型 run 和独立人工门禁通过前，不得标记 `passing`。

## 下一步

- 使用固定 7 例和显式真实模型开关运行新的 `scene_mvp` v3 benchmark，核对模型完整 Scene、fallback 仲裁、Refine replace patch、外部同口径指标与人工偏好是否同向；无模型 6/7 改善只证明确定性下界，不证明模型视觉达标。
- 为 v3 结果制作独立匿名盲评包并执行既有人工门禁；通过前不得调整 `0.04` target、外部 baseline 容差或把 F09 标为 passing。
- 后续再独立评估 CMA-ES/2000 draw 搜索；当前 48/96/160 draw 确定性搜索已验证参数接线、累计接受、预算记账和 best 单调性，但不替代更大搜索空间的时延、取消和质量 benchmark。
- `scene_mvp` 继续作为显式实验模式，`procedural_v1` 保持默认；在新 benchmark 与人工门禁通过前不删除 V1、Memory、Node Lab 或既有证据。
- V1 退役准备必须作为独立增量执行：优先完成通用消息/WebGL1 契约解耦和机器可读依赖 inventory；固定模板扩展、质量 benchmark 与大规模删除不得混在同一改动中。

## 未解决缺口

- `scene_mvp` v3 尚未运行真实模型 7 例 benchmark 和独立人工盲评；当前 6/7 改善来自确定性感知 fallback，不能证明模型会正确选择 linear/lobe/glow 或 replace patch，也不能作为发布通过证据。
- `scene_mvp` 已具备 prepared program、严格 typed uniform 热上传、原始 RGB 热路径和 100 draw 显式性能探针，但尚无 CMA-ES、2000 draw 生产预算、优化中断/恢复或对应质量证据，不能声称已完成性能版优化器。
- 当前产品接入覆盖显式模式、Backend/Frontend、账本摘要、三种 Artifact、分档 render/LLM/Refine 预算和专用浏览器 E2E；Memory、Node Lab、独立 benchmark 和真实模型质量验证尚未迁移，V1 垂直切片与冻结失败证据不得删除。
- `scene_mvp` 的专用浏览器 E2E 使用隔离假 API 验证“达标”和“流程完成但质量未达标”两种成功响应都会显示结果；它不证明真实模型视觉质量、数据库耐久性或线上网络配置。
- `min_scene_composite_v3` 已去除亮度分位数伪语义，但仍未通过真实模型 benchmark 证明 geometry/edge/worst-tile 与人类偏好的视觉拓扑和高光/阴影层次一致，这是 F09 当前的质量发布阻塞项。
- 正式 M5 与 Node Lab real-model 的完整报告仍位于被忽略的本地 `output/benchmarks/`；`docs/evidence/registry.json` 已登记摘要、字节数和 SHA-256，但耐久性仍为 `partial`。在完整脱敏证据进入 Git LFS、Release 或不可变对象存储前，不能仅凭本地路径独立复验。
- 服务端仍是阻塞式 API；浏览器停止等待不等于服务端取消。端到端 deadline、任务化/cancel、outbox/reaper、多 worker 分布式锁和真实发生顺序事件属于后续可靠性设计，不与 M6.2 混写。
- 目标架构详细版当前只有设计方案；多假设、置信度标定、特征块调度、硬约束、两层一致性、沙箱和完整评测体系均未实现。旧 V2–V5 中未落地的契约不再是当前阶段门禁，但相应能力仍不得表述为完成。

## 当前验证基线

- 2026-07-22 当前工作树通过 `make check`：462 个 Python 单元测试、`docs-check`、LangGraph validate（2 个 Graph）和 Frontend production build；全仓 `ruff check`、`mypy --strict src backend`（147 个源文件）与 `git diff --check` 通过。普通 Integration 为 37 passed/1 skipped，另有 1 个环境性失败：本地 `.env` 的 `TEST_DATABASE_URL` 指向无法解析的占位主机 `HOST`，与本次改动无关。`scene_mvp` 专用浏览器 E2E 通过。
- v3 Renderer/质量基线：真实 Chromium 4 passed/1 显式性能探针 skipped，覆盖 prepared/baked 一致、三颜色场、六 feature、四槽和固定签名；固定 7 例质量回归 2 passed，按外部 `png_to_shader_score_v1` 对照 v2 fallback 为 6/7 改善，`solid_circle` total-loss 回归 `0.000217` 低于冻结的 `0.001` 容差，所有 ROI 与 geometry 回归均低于 `0.01` 容差。未调用真实模型。
- 2026-07-16 当前工作树在 `UV_LOCKED=1` 下通过 `make check`：414 个 Python 单元测试、`docs-check`、LangGraph validate（1 个 Graph）与 Frontend production build 均成功；Integration 为 27 passed、1 skipped，全仓 Ruff、`mypy --strict src backend` 与 `git diff --check` 通过。未运行真实模型。
- 数据库和浏览器追加验收通过：`make test-memory-postgres` 为 1 passed；产品 `npm --prefix frontend run e2e:procedural-v1` 与 Node Lab `make test-node-lab-ui` 均通过，使用隔离资源且没有真实模型调用。
- `H02` 权威验收命令 `make benchmark-node-lab-ai-off`、`make benchmark-node-lab-model`、`make test-node-lab-ui` 均通过；本次离线五角色 run id 为 `node-lab-model-78520d334d0a`。这些结果只覆盖 AI-off、离线 fixture 和页面流程，不构成真实模型质量证明。
- 2026-07-16 wheel 审计确认 `backend.sql`、V1 嵌套包、Prompt、许可证和三个 `py.typed` 均进入发布包且无 package-discovery 警告；独立导入探针确认 `shaderforge.contracts`、`agent.app.contracts.llm` 与 `agent.app.lab.models` 不再 eager-load Renderer、Runner、Playwright 或 V1 Agent 契约，同时根包兼容导出的对象 identity 保持不变。
- 2026-07-15 离线基线：产品 AI-off 10 例 smoke `m5-20260715T155850Z` 完成且质量 gate 按设计为 `not-evaluated`；最终源码指纹下的 Node Lab capability/node、scenario、Renderer warm、transport 四组 suite-run `node-lab-21616b814e33`、`node-lab-164f8c9687af`、`node-lab-5a386968babc`、`transport-6176202caddc` 全部 passed；五模型角色 fixture `node-lab-model-fccee6297b34` passed。未调用真实模型。
- 发布基线：`m5-20260715T023445Z` 自动检查 12/12 通过，人工完整度 10/10 通过；评审原始 JSON SHA-256 为 `74e02ac9e423637938b182fa3767c53c148058ec1dfcd4adf147c0e1191cc782`，人工偏好门禁失败，产物必须只增不改保留。
- Node Lab 真实模型基线：`node-lab-model-real-review-20260715-v1` 使用 `dashscope:qwen3.7-plus`，五角色 5/5 completed/correctness passed、0 timeout、0 JSON repair；首次 Parser 通过率为 `0.8`，Initial Author 依赖一次受限本地 fixed-binding 修复，不得表述为模型原始输出 5/5 合法。

## 最近重要变更

- 2026-07-22：新增 PNG-to-Shader V1 分阶段退役计划，冻结“先解耦、再迁移 benchmark/Node Lab/Memory、切换默认、最后删除”的顺序；当前未授权删除 V1，F09、默认模式和发布门禁均未改变。
- 2026-07-22：完成 `scene_mvp` 固定模板扩展：分别冻结 v3 Scene/template/metric、四槽 15/16 WebGL1 资源布局、三类颜色场、六类 feature、replace patch、typed optimizer 与通用区域 objective；真实 Chromium 像素/签名测试通过，固定 7 例外部 objective 对照 v2 fallback 为 6/7 改善。它不是旧 V3 阶段，真实模型与人工门禁仍待执行，决策见 D058。
- 2026-07-22：`scene_mvp` 新增运行时可观测：Agent service 以 `astream` 逐节点产出白名单进度事件，Backend 新增进程内存 `RunProgressRegistry` 与 `/progress`、`/progress/render` 只读端点，前端运行中展示节点时间线、预算、质量进度、路由决策、事件流和实时渲染帧；终态账本与 Graph 拓扑不变，决策见 D057。
- 2026-07-22：保持 `scene_mvp` Graph 拓扑不变，完成模型/fallback 真实仲裁、WebGL1 最低 uniform 容量安全的 v2 packed 三槽模板、rim/arc/line 独立几何、动态 feature queue、累计候选优化、三档硬预算和局部复合 loss；API、账本、Artifact 与 UI 同步公开模板、指标和预算，F09 仍为 active/no-go。
- 2026-07-21：保持 `scene_mvp` 12 节点拓扑不变，接入完整 MinScene Initial Author、单个 typed patch Refine Author、一次结构修复、40 draw 确定性参数搜索及达标/未达标双路径 UI E2E；显式产品模式最多 6 次调用和 1 轮 Refine，失败/非法/较差候选不能覆盖 `current_best`。

## 历史索引

- 结构化整理前的完整原始记录：`docs/progress/archive/PROGRESS-2026-07-07--2026-07-15.md`。
- 截止 2026-07-10 的阶段总结：`docs/progress/archive/STAGE-SUMMARY-2026-07-10.md`。
- 验收证据及耐久性状态：`docs/evidence/registry.json`；维护规则见 `docs/evidence/README.md`。
- 代码演进以 Git commit 为准；本地 benchmark、盲评和逐例失败原件仍保存在 `output/benchmarks/`，只有 registry 标为 `durable` 的文件才可视为跨环境可获得。历史快照只用于审计，不得作为当前事实来源。

## 维护规则

- 会话结束时原地刷新“当前状态、下一步、未解决缺口、当前验证基线”；例行重复验证覆盖旧基线，不新增逐会话流水账。
- 只有功能状态、架构/契约、质量门禁、阶段里程碑或重要未决缺口发生变化时，才在“最近重要变更”新增一条；该区最多保留 5 条。
- 主文件 UTF-8 体量上限为 20,000 bytes。超出条目移入 `docs/progress/archive/`，归档必须注明时间范围和“非当前事实”，且不得删除冻结失败证据。
