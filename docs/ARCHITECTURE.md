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

当前仓库状态：`frontend/src/App.tsx` 支持图片上传和 GLSL 预览；显式的风格、场景、约束、测试规划输入尚未实现。

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

当前仓库状态：`src/agent/app/graphs/main_graph.py` 保留基础对话图；`src/agent/app/graphs/shader_generation_graph.py` 使用显式 `operation` 路由串联 `prepare_context -> generate_glsl`，或 `prepare_context -> review_render -> promote_memory`。任务内轻量状态由 LangGraph Checkpointer 按 `project_id == thread_id` 保存，项目长期 Review Memory 写入版本化 Store namespace；图片、完整 GLSL、ContextPack、reasoning 和过程摘要不进入 checkpoint/Store。当前没有服务端 Renderer 时，前端先用 WebGL canvas 渲染第一帧，再把原图、当前渲染图、GLSL 和 `project_id` 发给 `POST /api/shader/review`。`backend/app/services/shader.py` 只调用 `agent.app.services.shader_generation` 公共服务；Backend 生命周期持有 LangGraph psycopg persistence pool，Agent Node 只通过 Runtime Store 接口访问 Memory。`backend/sql/001_agent_process.sql` 和 `backend/app/services/agent_process_store.py` 提供带 `project_id` 的过程账本。Intent IR、DSL、Renderer、Oracle、Search Engine、完整 VLM/HITL、ShaderForge Store 都是后续工作。

### 3. 工具知识层

目标支撑能力：

- AI / Agent 知识：Prompt 策略、ReAct/LangGraph、停止条件、预算。
- 图像与颜色指标：Lab Delta E、CIEDE2000、SSIM、边缘、Mask、局部损失。
- 搜索优化工具：CMA-ES、MAP-Elites、参数归一化、分块优化。
- Shader / 渲染知识：GLSL、WebGL、SDF、Noise、Blend、渲染一致性测试。
- 数据与评测：SQLite/文件缓存、版本、谱系、评分、VLM pairwise、人工标签。

当前仓库状态：已有 LangGraph、FastAPI、React、WebGL 预览、Prompt YAML、Agent 过程数据表和单元测试。F09 M0 已创建 `src/shaderforge/contracts/`，冻结 WebGL1 无贴图运行契约、问题域、停止原因、预算和候选接受策略；Renderer、Oracle、搜索和 ShaderForge 持久化层尚未实现。

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
│       ├── rendering/        # DSL 到 GLSL、渲染适配、渲染一致性检查
│       ├── evaluation/       # Oracle、全局评分、局部损失、图像指标
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
  -> backend service 编排
  -> src/agent LangGraph 节点进行模型分析和策略选择
  -> src/shaderforge 生成 Intent IR / DSL / GLSL
  -> src/shaderforge evaluation 与 search 迭代优化
  -> src/shaderforge review/store 记录评分、谱系和人工/模型评审
  -> backend 返回结果
  -> frontend 展示 GLSL 与 WebGL 预览
```

核心原则：`backend` 是入口，不是领域层；`agent` 是智能编排，不是算法仓库；`shaderforge` 是可测试、可复用、可脱离 HTTP 和 UI 运行的领域核心。

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
- `src/shaderforge/intent`、`dsl`、`rendering`、`evaluation`、`search`、`review`、`store` 改动：补聚焦单元测试；跨模块流程放 `tests/integration_tests/`。
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
- 一次只新增一个已验证功能；状态变化时更新 `docs/FEATURES.md` 和 `PROGRESS.md`。
- 在具体功能需要前，不实现 SVG 中的目标层模块。
- 不按 SVG 节点提前拆微服务；只有出现独立部署、资源隔离或性能瓶颈时，才评估服务拆分。
