# 进度

最后更新：2026-07-21

> 本文件是有界的当前交接页，不是逐会话追加日志。功能状态以 `docs/FEATURES.md` 为准，长期取舍以 `docs/DECISIONS.md` 为准，完整旧记录见 `docs/progress/archive/`。

## 当前状态

- 产品默认仍是 PNG-to-Shader V1；`langgraph.json` 现并行注册 `png_to_shader_v1` 与 `png_to_shader_min`。Backend/Frontend 保留默认 `procedural_v1`，并增加显式实验模式 `scene_mvp`；旧基础对话图、legacy 生成和独立 Review 入口保持删除。
- 全部 V1 Node 工厂及其支持实现已收拢到 `agent.app.nodes.png_to_shader_v1` 功能命名空间，内部按 `model`、`deterministic` 和 `integrations/node_lab` 分层；`nodes/` 根目录不再保留实际绑定 V1 的伪通用模块，两个纯 decision callable 继续与条件边规则共同归属 Graph routing。
- `F09` 已完成 M0–M5、M6.0 可靠性修复、M6.1 离线质量改进及 Node Lab API v1 阶段 A–D。Node Lab 由生产 `NodeProvider` 暴露 20/20 节点，Harness 不维护具体 Graph/Node 的平行语义；`H02` 的 AI-off、五模型角色离线 fixture 与页面验收均已通过并标记为 `passing`。
- Backend 已在组合根冻结数据库、日志、CORS 与 Node Lab 配置；数据库和 Agent Memory 的半初始化、取消及关闭失败均进入补偿清理。主 CI 使用 Python/Node 锁定安装执行完整 `make check` 与 `mypy --strict src backend`，普通 Integration 不持有真实模型凭据。
- 仓库结构边界已加固：五模型角色离线 benchmark 与在线 Agent Service 分离，共享测试样本归入 `tests/fixtures`，轻量包导入不再 eager-load 浏览器/Runner/V1 契约；前端统一通过 API client 访问后端，Node Lab 只对选中步骤加载完整明细。
- 正式 run `m5-20260715T023445Z` 的自动质量检查 12/12 通过，但独立人工盲评 final/initial/tie 为 `3/4/3`，final 偏好率 `30%` 低于冻结的 `50%` 门槛；最终 gate 为 `failed`，F09 继续 active、灰度 no-go。
- 用户已确定《PNG 转无贴图 GLSL Agent—目标架构（详细版）》取代旧 V2–V5 路线，成为 F09 后续算法与演进的权威目标；旧 V2–V5 和最初架构 SVG 均保留为历史/概念参考，不再覆盖后续决策、当前架构或实现事实。该决定不表示详细版能力已经实现，也不改变 F02–F05 的 `not_started` 状态。
- 《PNG 转无贴图 GLSL Agent—最小骨架（快速版）》已完成首个快速贯通增量：严格 scene、确定性感知、固定模板、真实 WebGL1、RGB MAE、12 节点/3 路由、Artifact/trace、Backend 分流和 Kimi 前端摘要均已落地。当前使用确定性 fallback、常量烘焙和少量有界微调；prepared program、模型 Author 与 CMA-ES 尚未实现。V1、Node Lab、benchmark 与历史证据保持不变。

## 当前 active 功能

- `F09`：PNG 转无贴图 Shader Agent V1。权威状态、验证命令和发布证据见 `docs/FEATURES.md`；在新的真实模型 run 和独立人工门禁通过前，不得标记 `passing`。

## 下一步

- 下一增量只补保持 V1 兼容的 prepared program/typed uniform 热路径和性能探针；该门禁通过前不引入 CMA-ES 或 2000 draw 默认预算。
- prepared 路径稳定后，再把 `author_initial`/`author_refine` 接入现有 Gateway 严格 JSON 契约；普通测试继续只用 Fake Gateway，真实模型必须显式开启并受 6 次调用硬预算。
- `scene_mvp` 继续作为显式实验模式，`procedural_v1` 保持默认；在新 benchmark 与人工门禁通过前不删除 V1、Memory、Node Lab 或既有证据。

## 未解决缺口

- `scene_mvp` 当前 typed uniform 会烘焙为常量，仍由现有 Renderer 每次编译并编码 PNG；prepared program、任意 typed uniform 热上传、原始像素热路径和 100 次性能探针未完成，不能声称已具备 2000 draw/CMA-ES 性能。
- 当前产品接入只覆盖显式模式、Backend/Frontend、账本摘要和三种 Artifact；Memory、Node Lab、独立 benchmark 和真实模型仍未迁移，V1 垂直切片与冻结失败证据不得删除。
- 自动 objective 尚不能充分保护人类偏好的视觉拓扑、实例数量和高光/阴影层次，这是 F09 当前的质量发布阻塞项。
- 正式 M5 与 Node Lab real-model 的完整报告仍位于被忽略的本地 `output/benchmarks/`；`docs/evidence/registry.json` 已登记摘要、字节数和 SHA-256，但耐久性仍为 `partial`。在完整脱敏证据进入 Git LFS、Release 或不可变对象存储前，不能仅凭本地路径独立复验。
- 服务端仍是阻塞式 API；浏览器停止等待不等于服务端取消。端到端 deadline、任务化/cancel、outbox/reaper、多 worker 分布式锁和真实发生顺序事件属于后续可靠性设计，不与 M6.2 混写。
- 目标架构详细版当前只有设计方案；多假设、置信度标定、特征块调度、硬约束、两层一致性、沙箱和完整评测体系均未实现。旧 V2–V5 中未落地的契约不再是当前阶段门禁，但相应能力仍不得表述为完成。

## 当前验证基线

- 2026-07-21 当前工作树通过 `make check`：421 个 Python 单元测试、`docs-check`、LangGraph validate（2 个 Graph）与 Frontend production build 均成功；全仓 Ruff、`mypy --strict src backend` 和 `git diff --check` 通过。真实 HTTP + Chromium 粉球 smoke 返回 200，MAE `0.057279`、1 draw、0 模型调用，并成功读取 `png_to_shader_min_manifest_v1`；另有 45 项核心/Backend 定向测试通过。未运行真实模型或发布 benchmark。
- 2026-07-16 当前工作树在 `UV_LOCKED=1` 下通过 `make check`：414 个 Python 单元测试、`docs-check`、LangGraph validate（1 个 Graph）与 Frontend production build 均成功；Integration 为 27 passed、1 skipped，全仓 Ruff、`mypy --strict src backend` 与 `git diff --check` 通过。未运行真实模型。
- 数据库和浏览器追加验收通过：`make test-memory-postgres` 为 1 passed；产品 `npm --prefix frontend run e2e:procedural-v1` 与 Node Lab `make test-node-lab-ui` 均通过，使用隔离资源且没有真实模型调用。
- `H02` 权威验收命令 `make benchmark-node-lab-ai-off`、`make benchmark-node-lab-model`、`make test-node-lab-ui` 均通过；本次离线五角色 run id 为 `node-lab-model-78520d334d0a`。这些结果只覆盖 AI-off、离线 fixture 和页面流程，不构成真实模型质量证明。
- 2026-07-16 wheel 审计确认 `backend.sql`、V1 嵌套包、Prompt、许可证和三个 `py.typed` 均进入发布包且无 package-discovery 警告；独立导入探针确认 `shaderforge.contracts`、`agent.app.contracts.llm` 与 `agent.app.lab.models` 不再 eager-load Renderer、Runner、Playwright 或 V1 Agent 契约，同时根包兼容导出的对象 identity 保持不变。
- 2026-07-15 离线基线：产品 AI-off 10 例 smoke `m5-20260715T155850Z` 完成且质量 gate 按设计为 `not-evaluated`；最终源码指纹下的 Node Lab capability/node、scenario、Renderer warm、transport 四组 suite-run `node-lab-21616b814e33`、`node-lab-164f8c9687af`、`node-lab-5a386968babc`、`transport-6176202caddc` 全部 passed；五模型角色 fixture `node-lab-model-fccee6297b34` passed。未调用真实模型。
- 发布基线：`m5-20260715T023445Z` 自动检查 12/12 通过，人工完整度 10/10 通过；评审原始 JSON SHA-256 为 `74e02ac9e423637938b182fa3767c53c148058ec1dfcd4adf147c0e1191cc782`，人工偏好门禁失败，产物必须只增不改保留。
- Node Lab 真实模型基线：`node-lab-model-real-review-20260715-v1` 使用 `dashscope:qwen3.7-plus`，五角色 5/5 completed/correctness passed、0 timeout、0 JSON repair；首次 Parser 通过率为 `0.8`，Initial Author 依赖一次受限本地 fixed-binding 修复，不得表述为模型原始输出 5/5 合法。

## 最近重要变更

- 2026-07-21：以 Orca 编排驱动 Backend Codex worker 与 Frontend Kimi worker，完成 `scene_mvp` 最小垂直链路；Graph、真实 Renderer、MAE、Artifact、HTTP、账本 trace 和 UI 已贯通，V1 继续作为默认路径。
- 2026-07-21：将最初架构 SVG 降为历史设计参考，取消“冲突时以 SVG 为准”；当前事实由代码/配置和架构文档共同维护，F09 目标与实施分别以详细版和快速版为准。
- 2026-07-21：确认目标架构详细版取代旧 V2–V5 路线，成为 F09 后续算法与演进的权威目标；快速版继续作为当前 MVP 实施切片，旧方案降为历史参考。
- 2026-07-21：完成 PNG-to-Shader 最小骨架快速版的实施前修订，修正为 12 节点/3 路由，补齐 prepared Renderer 性能门禁、轻量 MAE、State/Artifact、安全失败和可选产品切换边界；现有 V1 继续承担产品路径。
- 2026-07-16：完成跨模块结构修复：离线 benchmark 脱离在线 Service，共享 Fixture 脱离 unit test 层级，前端统一 API client 并消除 Node Lab 步骤 N+1 请求；新增依赖方向、直接 fetch、惰性导入和生命周期回归门禁。

## 历史索引

- 结构化整理前的完整原始记录：`docs/progress/archive/PROGRESS-2026-07-07--2026-07-15.md`。
- 截止 2026-07-10 的阶段总结：`docs/progress/archive/STAGE-SUMMARY-2026-07-10.md`。
- 验收证据及耐久性状态：`docs/evidence/registry.json`；维护规则见 `docs/evidence/README.md`。
- 代码演进以 Git commit 为准；本地 benchmark、盲评和逐例失败原件仍保存在 `output/benchmarks/`，只有 registry 标为 `durable` 的文件才可视为跨环境可获得。历史快照只用于审计，不得作为当前事实来源。

## 维护规则

- 会话结束时原地刷新“当前状态、下一步、未解决缺口、当前验证基线”；例行重复验证覆盖旧基线，不新增逐会话流水账。
- 只有功能状态、架构/契约、质量门禁、阶段里程碑或重要未决缺口发生变化时，才在“最近重要变更”新增一条；该区最多保留 5 条。
- 主文件 UTF-8 体量上限为 20,000 bytes。超出条目移入 `docs/progress/archive/`，归档必须注明时间范围和“非当前事实”，且不得删除冻结失败证据。
