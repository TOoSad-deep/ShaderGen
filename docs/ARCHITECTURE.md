# 架构

## 权威来源

最终产品架构以 `human_doc/shaderforge-technical-architecture-aligned(1).svg` 为准。如果本文档与该 SVG 不一致，以 SVG 为准。

## 产品目标

ShaderGen/ShaderForge 将用户意图、参考图、约束和验收标准转成可渲染、可评分、可调优、可评审、可存储的视效 Shader。

## 目标分层

### 1. 用户输入层

目标输入：

- 初始 Idea：用户想要的视觉效果。
- 初始需求：风格、场景、约束。
- 初始设计：参考图、草图、描述。
- 初始测试规划：验收指标、样例集。

当前仓库状态：`frontend/src/App.tsx` 支持图片上传、`legacy | procedural_v1` 模式、V1 质量档位、补充约束、服务端/客户端双渲染和 GLSL/评分/停止原因展示；通用 Idea、风格、场景和测试规划结构化输入尚未实现。

### 2. 核心处理层

目标流程：

1. `Routing`：任务拆解、智能路由。
2. `Agent 分析`：目标理解、策略选择。
3. `Intent IR`：对象、颜色、位置、约束结构化。
4. `DSL 节点图`：可搜索的 Shader 结构表示。
5. `Renderer 渲染`：DSL 转 GLSL，并产出可渲染结果。
6. `Oracle / 局部损失`：全局评分，以及颜色、形状、边缘局部损失。
7. `Search Engine 调优`：CMA-ES、MAP-Elites、结构变异。
8. `VLM / HITL`：模型评审、人工评审、Store 记录。

当前仓库状态：`src/agent/app/graphs/main_graph.py` 保留基础对话图；`src/agent/app/graphs/shader_generation_graph.py` 继续服务 legacy 生成与前端 canvas Review。独立 `png_to_shader_v1_graph.py` 先运行 `prepare_context` 和参考图确定性测量，再调用 Analyst/Author/Critic，通过静态 Validator、项目自有 Playwright/Chromium WebGL1 Renderer、Basic Oracle 和 LocalArtifactStore 形成有界 initial / compile-repair / visual-refine 循环。首个成功 model best 后还会生成一次与 case/manifest/gate 无关的 measurement affine 独立根候选；它不消耗模型或视觉迭代预算，但必须经过同一事实层与 Selector。生产 Oracle 保留确定性测量 ROI，并追加严格 VisualAnalysis 的语义 ROI。纯 Selector 只在硬约束通过、总损失达到最小改善且保护区不超退化时更新 `current_best`；Critic/refine 与 finalize 均从 best Artifact 重载 GLSL/PNG/metrics，模型或新候选失败不会覆盖已有 best。任务内轻量状态由 LangGraph Checkpointer 保存，图片、完整 GLSL、渲染图、ContextPack、Candidate 大对象和过程摘要使用 `UntrackedValue`；项目长期 Memory 只保存精炼摘要，并且只晋升确定性验证过的 best 策略。M4 通过 `agent.app.services.png_to_shader_v1` 把该图接入 Backend：V1 使用独立 checkpoint thread 前缀但共享项目 Store，Backend 生命周期统一注入 persistence；Graph 与 Service 共享 run 级 Renderer registry，正常路径由 `finalize` 关闭，Graph 外异常由 Service `finally` 限时、幂等兜底。过程事件由公共结果交给 Backend 写入账本。HTTP 只开放 final-render/metrics/manifest 白名单，前端按服务端规范化尺寸重编译 GLSL 并比较像素 RMSE。

Node Lab 以 transport-free Application API 和通用 `NodeProvider` 协议复用生产 Node。Harness 内核不导入任何具体 Node/Graph，pipeline id、descriptor、执行模式、routing capability 与 Adapter 均由生产侧 `agent.app.nodes.integrations.node_lab` Provider 提供。当前 PNG-to-Shader Provider 暴露 20 个图节点、机器可读示例和离线成功/拒绝路径；15 个非模型节点通过 Artifact facade 直接调用生产 Node factory/routing，五个模型节点调用生产角色 Node factory 与 bounded wrapper。新 Node 只在生产 Provider 登记 descriptor/binding，不修改 Node Lab 内核或 Service；Lab 只负责输入投影、私有 Artifact 和副作用门禁，不维护 initialize/materialize/render/select/finalize/promotion 的平行语义。完整 ContextPack、GLSL、图片、模型原始内容只存 Lab Artifact，策略 Memory 只 preview。八个确定性 capability、不可变步骤、真实 node target、scenario/pipeline、Renderer cold/warm、transport AI-off 和独立模型角色 benchmark 共用同一 Harness；失败/中断证据不可覆盖。可选 `/api/lab/v1/*` 仅在显式环境开关下注册，不进入产品 API；HTTP batch 只接受仓库内三个固定 AI-off suite id。`scripts/run_node_lab_cli.py`、Swagger 和 `/lab` 工作台分别提供自动化、HTTP 与人工入口，只消费公共 Application API/descriptor。

M5 以固定 10 例 manifest、AI-off smoke、成本受控 AI-on runner、运行前冻结 gate 和匿名 A/B 页面独立于产品请求执行；新 run 对 model initial 与 final 使用同一 manifest ROI objective，并严格区分 model/deterministic provenance。Node Lab benchmark 与 M5 证据互不覆盖。Intent IR、DSL、Search Engine 和完整 VLM/HITL 仍是后续工作。

### 3. 工具知识层

目标支撑能力：

- AI / Agent 知识：Prompt 策略、ReAct/LangGraph、停止条件、预算。
- 图像与颜色指标：Lab Delta E、CIEDE2000、SSIM、边缘、Mask、局部损失。
- 搜索优化工具：CMA-ES、MAP-Elites、参数归一化、分块优化。
- Shader / 渲染知识：GLSL、WebGL、SDF、Noise、Blend、渲染一致性测试。
- 数据与评测：SQLite/文件缓存、版本、谱系、评分、VLM pairwise、人工标签。

当前仓库状态：已有 LangGraph、FastAPI、React、WebGL 预览、Prompt YAML、Agent 过程数据表和单元测试。F09 M0 已冻结 WebGL1 无贴图运行契约、问题域、停止原因、预算和候选接受策略；M1 已增加 TargetMeasurements、Validator、真实 WebGL1 Renderer、Basic Oracle 和本地 Artifact Store；M2 已增加三个模型角色、严格 Parser、模型/Prompt/GLSL provenance 和一次结构化修复策略；M3 已增加独立有界 Graph、Candidate Artifact/current_best 选择、失败降级和验证后 Strategy Memory；M4 已增加产品 API、Artifact 白名单、阶段账本、结果 UI 和双端 WebGL 像素复核；M5 已增加固定 benchmark、AI-off/AI-on runner、质量门禁、盲评包和成本受控 nightly。搜索和 Effect Genome 留待后续版本，F09 是否 passing 仍由真实基准与人工盲评证据决定。

## 项目结构规范

本项目采用分层 monorepo。最终架构里的业务能力不直接映射为根目录；根目录只放工程入口、配置、文档和一级应用边界。后续新增核心能力时，优先落到 `src/shaderforge/`，而不是散落在 `backend/` 或 `src/agent/`。

目标结构：

```text
ShaderGen/
├── frontend/                 # 用户输入层、WebGL 预览、结果展示
├── backend/                  # FastAPI HTTP 边界、请求校验、应用生命周期
│   ├── app/
│   │   ├── api/              # 路由聚合和 HTTP route
│   │   ├── core/             # 应用级配置
│   │   ├── database/         # 连接池、schema 初始化、健康检查查询
│   │   ├── middleware/       # 请求日志等 FastAPI 中间件
│   │   ├── schemas/          # API 请求/响应 schema
│   │   └── services/         # 后端用例编排
│   └── sql/                  # 手写 SQL schema 记录目录
├── src/
│   ├── agent/                # LangGraph 编排、模型调用、Prompt 策略
│   │   └── app/
│   │       ├── config/       # Agent 配置
│   │       ├── graphs/       # LangGraph 图入口
│   │       ├── states/       # 图状态和运行上下文
│   │       ├── nodes/        # 图节点
│   │       ├── prompts/      # Prompt YAML 和加载器
│   │       ├── parsers/      # 模型输出解析器
│   │       ├── contracts/    # LLM Gateway 等中立契约
│   │       ├── llms/         # Gateway、provider、model-family 适配
│   │       ├── messages/     # 复用的消息构造 helper
│   │       ├── memory/       # 项目长期记忆模型和 Store 操作
│   │       ├── context/      # 纯 GSSC Context Builder
│   │       ├── services/     # 对后端开放的 Agent 用例服务
│   │       ├── tools/        # Agent 工具注册入口
│   │       └── observability/# Agent 回调、追踪、指标入口
│   └── shaderforge/          # 领域核心流水线，按真实功能逐步创建
│       ├── routing/          # 任务拆解、路由策略、阶段选择
│       ├── intent/           # Intent IR 类型、解析、约束结构化
│       ├── dsl/              # Shader DSL 节点、图结构、变异操作
│       ├── rendering/        # WebGL1 编译、渲染适配、渲染一致性检查
│       ├── evaluation/       # Oracle、全局评分、局部损失、图像指标
│       ├── generation/       # 确定性无贴图候选 seed 生成
│       ├── search/           # CMA-ES、MAP-Elites、参数归一化、搜索预算
│       ├── review/           # VLM pairwise、人工评审、评审结论
│       └── store/            # SQLite/文件缓存、版本、谱系、评分记录
├── tests/
│   ├── unit_tests/
│   └── integration_tests/
├── docs/
├── human_doc/                # 用户提供的权威材料
└── static/                   # 文档或演示静态资源
```

`src/shaderforge/` 不是现在就要创建的空框架。只有当功能进入 `docs/FEATURES.md` 且需要真实代码时，才创建对应子包和测试。

## 目录内规范入口

`docs/ARCHITECTURE.md` 只记录全局分层、跨层数据流和边界规则。模块说明和边界优先放在模块旁边的 `ARCHITECTURE.md`；没有单独模块架构文件时，再看对应目录 README：

- `frontend/README.md`：前端目录、组件、API 封装、样式、构建验证规范。
- `backend/README.md`：后端路由、service、schema、错误处理、测试规范。
- `src/agent/ARCHITECTURE.md`：Agent 总体流向、当前图和子模块规范索引。
- `src/agent/app/*/ARCHITECTURE.md`：Agent 子模块边界，例如 `nodes`、`states`、`graphs`、`services` 各自的实现规范。
- `src/agent/README.md`：Agent 模块入口、运行命令和事实来源索引。
- `src/shaderforge/ARCHITECTURE.md`：ShaderForge 当前已实现范围、公共入口和禁止依赖。

如果模块旁边的 `ARCHITECTURE.md` 或目录 README 与本文档冲突，以本文档的分层边界为准，再更新模块文档。

## 分层职责

- `frontend/` 只负责用户输入、交互状态、图片/Shader 预览和结果展示；不要放搜索、评分、Prompt 组装或后端业务规则。
- `backend/` 只负责 HTTP API、上传校验、错误响应、鉴权/限流等应用边界，以及调用 service 编排流程；不要把核心算法写在 route 中，也不要直接依赖 Agent 内部模型、Prompt 或 LangChain 消息。
- `src/agent/` 负责 LangGraph 图、模型选择、Prompt 加载、Agent 节点、Context Engineering、Memory 数据结构/Store 接口操作和对后端开放的 `agent.app.services.*` 公共用例服务；运行过程摘要通过公共服务返回给后端落库。Agent 不创建或关闭数据库连接池，具体 persistence 资源由 Backend 生命周期注入；Agent 也不承载图像指标、搜索优化或 ShaderForge 产物存储。
- `src/shaderforge/` 负责最终架构中的确定性领域能力：IR、DSL、渲染、评分、搜索、评审、存储。该层应尽量用普通 Python 类型和函数表达，不依赖 FastAPI，也不直接依赖 React。
- `tests/` 按行为归属放测试。领域核心优先写单元测试；跨 backend、agent、shaderforge 的路径放集成测试。

## 目标数据流

```text
frontend 用户输入
  -> backend HTTP 校验
  -> backend service 按 generation_mode 分流
  -> agent.app.services legacy 或 png_to_shader_v1 公共用例
  -> src/agent LangGraph 节点进行模型分析和策略选择
  -> src/shaderforge 校验 / WebGL1 渲染 / Oracle / current_best / Artifact
  -> backend 写过程账本并返回 GLSL、评分、停止原因和白名单 URL
  -> frontend 展示服务端 PNG，以 WebGL1 重编译并比较像素 RMSE
```

核心原则：`backend` 是入口，不是领域层；`agent` 是智能编排，不是算法仓库；`shaderforge` 是可测试、可复用、可脱离 HTTP 和 UI 运行的领域核心。

### 在线可靠性边界

- M5 质量门禁未通过期间，Frontend/HTTP 默认走 `legacy`；`procedural_v1` 必须由用户显式选择并显示实验/no-go 状态。
- V1 的 wall-time 预算按阶段分配，模型不得占用留给确定性修复、Renderer、Evaluator 和 finalize 的保留时间；Legacy 单次模型调用也有服务端 timeout。
- Renderer registry 的正常释放属于 Graph `finalize`，越过 Graph 的未知异常由 Agent Service `finally` 使用同一 registry 再次幂等释放；清理故障不得遮蔽原始生成结果或异常。
- `current_best` 只能来自 Selector。只有当 Evaluator 不可用且候选已经通过静态检查和真实 WebGL 时，才允许返回明确标记的 `unscored_fallback`；它没有评分、metrics、Critic 绑定或长期 Memory 晋升资格。
- API 错误必须区分请求校验、Shader validation、模型供应商/配置/响应、Renderer、persistence、timeout 和内部 pipeline 错误。未知内部异常不得伪装成用户可修复的 422。
- 过程终态在单个数据库事务中提交事件、日志和 run 状态；普通日志/事件禁止完整 GLSL、图片、reasoning、供应商原文或编译器原文。原始编译证据只进入私有 Artifact。
- 当前仍是阻塞式 API：浏览器停止等待不等于服务端取消。端到端 deadline、任务队列/cancel、outbox/reaper、多 worker 分布式锁和真实发生顺序事件属于下一可靠性阶段。

## 子包创建规则

- 先有功能项，再有目录。没有对应 `docs/FEATURES.md` 功能，不创建 `src/shaderforge/*` 空子包。
- 新建领域子包时，必须同时添加最小单元测试。
- 共享类型只在两个以上模块需要时抽出；单模块内部类型留在本模块。
- API 请求/响应 schema 放 `backend/app/schemas/`；HTTP route 放 `backend/app/api/routes/`；手写 SQL 放 `backend/sql/`。领域内部结构优先放 `src/shaderforge/*`，不要让后端 schema 泄漏进核心层。
- 后端新增 route 必须在 `backend/app/api/router.py` 注册；新增 service 必须表达一个真实后端用例；不创建 `auth`、`user`、`file` 等空包。
- Agent 新增对后端能力时，入口放在 `agent.app.services.*`；Node 只通过 `agent.app.contracts.llm.LLMGateway` 使用模型能力，具体实现由 Graph 从 `agent.app.llms` 注入。
- 如果创建 `src/shaderforge/`，必须同步更新 `pyproject.toml` 的包发现配置，确保 `uv run` 和测试环境能导入。

## 文档同步规则

- 架构分层、目录边界、跨层数据流变化：更新 `docs/ARCHITECTURE.md` 和 `docs/DECISIONS.md`。
- 常用命令、启动方式、端口、环境变量变化：更新 `README.md`、`AGENTS.md`，必要时更新目录 README。
- 前端目录、组件、API、样式规则变化：更新 `frontend/README.md`。
- 后端路由、service、schema、错误处理、测试规则变化：更新 `backend/README.md`。
- 功能状态、验证命令、完成证据变化：更新 `docs/FEATURES.md` 和 `PROGRESS.md`。
- 会话结束时无论是否改代码，都要在 `PROGRESS.md` 记录当前状态、下一步和验证结果。

## 不确定性处理规则

- 先从仓库事实来源判断：`README.md`、`AGENTS.md`、`docs/`、目录 README、现有代码和测试。
- 如果仍无法确定，并且选择会影响架构、API 契约、数据结构、安全、验证方式或用户体验验收，必须先向用户确认。
- 如果问题只影响局部实现细节，且现有规范已有默认方向，按现有规范选择最小可行方案，并在 `PROGRESS.md` 记录假设。
- 用户确认形成长期规则时，写入 `docs/DECISIONS.md` 或对应目录 README。

## 测试映射

- 文档、功能状态或架构边界改动：运行 `make docs-check`，必要时再运行 `make check`。
- `frontend/` 改动：至少运行 `npm --prefix frontend run build`。
- `backend/` 路由/service 改动：运行 `uv run pytest tests/unit_tests`，必要时补 TestClient 测试。
- `src/agent/` 改动：使用模拟模型测试节点行为；不要在单元测试中调用真实模型。
- `src/shaderforge/intent`、`dsl`、`generation`、`rendering`、`evaluation`、`search`、`review`、`store` 改动：补聚焦单元测试；跨模块流程放 `tests/integration_tests/`。
- 跨前端、后端、agent、领域核心的用户流程，必须在 `docs/FEATURES.md` 中写清验证命令和证据。

## 代码边界

- `src/agent/`：LangGraph 编排、模型配置、Prompt 加载、Agent 节点，内部入口为 `agent.app.*`。
- `src/shaderforge/`：后续领域核心流水线，承载 IR、DSL、渲染、评分、搜索、评审、存储等确定性能力。
- `backend/`：HTTP API、请求校验、服务层编排、应用生命周期和 SQL schema 记录。
- `frontend/`：用户输入、图片预览、WebGL 预览、生成的 GLSL 展示。
- `tests/`：Python 单元测试和集成测试。依赖模型的测试必须使用模拟对象，除非明确标记为集成测试。
- `human_doc/`：用户提供的源材料。不要静默改写架构来源。

## 架构规则

- Prompt 放在 `src/agent/app/prompts/*.yaml`，不要硬编码在路由或后端 service 中。
- 后端只能通过 `agent.app.services.*` 明确暴露的公共用例服务调用 Agent，不直接 import `agent.app.contracts`、`agent.app.llms`、`agent.app.prompts` 或 LangChain 消息类型。
- Agent 过程数据通过公共接口返回结构化摘要，例如 `model_calls`、`events` 和 `logs`；后端统一写入 `agent_runs`、`agent_events` 和 `agent_logs`。
- HTTP 校验放在路由中；可复用确定性流程逻辑放在服务层或 `src/shaderforge/`，涉及模型编排时才进入 `src/agent/`。
- `/api/shader/generate` 的项目锁、模式分流、Legacy timeout、过程总账、错误分类和公开响应契约由 `backend.app.services.shader_generation` 统一编排；Route 只保留上传校验、依赖装配和 HTTP envelope 映射。
- 一次只新增一个已验证功能；状态变化时更新 `docs/FEATURES.md` 和 `PROGRESS.md`。
- 在具体功能需要前，不实现 SVG 中的目标层模块。
- 不按 SVG 节点提前拆微服务；只有出现独立部署、资源隔离或性能瓶颈时，才评估服务拆分。
