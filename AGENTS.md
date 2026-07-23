# AGENTS.md

## 项目概览

ShaderGen 是一个“图片生成视效 Shader”工程：前端接收用户输入，后端提供 API，LangGraph agent 和后续 ShaderForge 核心流水线把需求转成可评估的 GLSL/渲染结果。`human_doc/shaderforge-technical-architecture-aligned(1).svg` 是项目最初设计参考，不覆盖当前架构文档、决策、实现事实或已确认的新方案。

## 常用命令

- 安装：`make setup`（包括 Playwright Chromium）
- 初始化 Memory PostgreSQL：`make setup-memory-postgres`
- LangGraph：`make dev-agent`
- 后端：`make dev-backend`
- 前端：`make dev-frontend`
- 单元测试：`make test`
- Memory PostgreSQL 验收：`make test-memory-postgres`
- 文档边界检查：`make docs-check`
- 默认主干验证（单元测试、docs-check、LangGraph validate、前端构建）：`make check`
- scene_mvp 页面验收：`make test-scene-mvp-ui`

## 硬约束

- 每次只处理一个 `active` 功能。
- 未通过验证不得在 `docs/FEATURES.md` 标记为 `passing`。
- 涉及跨组件行为必须跑对应端到端或集成检查；没有自动化检查时，在 `PROGRESS.md` 写明缺口。
- 会话结束前原地刷新 `PROGRESS.md` 的当前状态、下一步、未解决缺口和验证基线；只有功能状态、架构/契约、质量门禁、阶段里程碑或重要缺口变化时才新增“最近重要变更”，例行重复验证不得形成逐会话流水账。重要取舍写入 `docs/DECISIONS.md`。
- 文档、计划、代码注释和 SQL 注释尽量使用中文；保留必要的英文技术名词、代码标识符和外部 API 名称。
- 架构、目录边界、命令、环境变量、功能状态或前后端契约变化时，必须同步更新对应 Markdown。
- Graph 可视化属于 Graph 实现的一部分：凡是新增、删除、重命名节点，修改直接边、条件边、路由结果、循环、终止路径、`current_best` 安全边界或 `langgraph.json` 注册，必须在同一次改动中同步对应 `*_graph.py` Builder 上方的 ASCII 图、`src/agent/app/graphs/ARCHITECTURE.md` 的 Mermaid 区块及相关路由表/安全说明；未通过 `make docs-check` 和 `uv run langgraph validate` 不得视为完成。
- 对仓库事实无法确定且会影响架构、契约、数据、安全或验收的问题，先向用户确认，不要自行猜测。
- 本地密钥只放根目录 `.env`，部署使用环境变量或 Secret Manager；任何密钥都不得进入 `VITE_*`、示例文件或 Git。
- 历史真实模型 benchmark 与失败证据默认不得覆盖或删除；只有用户针对精确范围明确授权一次性退役清理时可以删除，并必须同步 evidence registry、`PROGRESS.md` 和决策记录。当前仓库不再提供旧 V1 benchmark 运行入口。

## 按需阅读

- 架构边界：`docs/ARCHITECTURE.md`
- 功能状态机：`docs/FEATURES.md`
- 决策记录：`docs/DECISIONS.md`
- 当前进度：`PROGRESS.md`
- 验收证据：`docs/evidence/registry.json`（先看 `durability_status`，`partial` 不等于跨环境可复验）
- 历史审计：`docs/progress/archive/`（只在追溯时读取，不作为当前事实来源）
- 前端细则：`frontend/README.md`
- 后端细则：`backend/README.md`
- Graph 与路由开发：`src/agent/app/graphs/ARCHITECTURE.md`（涉及 Graph、routing 或节点跳转语义时必须先读）

## 仓库边界

- `src/agent/`：LangGraph 智能体核心，内部采用 `src/agent/app/` 规范结构。Prompt 只放 `src/agent/app/prompts/*.yaml`。
- `src/shaderforge/`：后续领域核心流水线。只有功能需要真实代码时才创建对应子包。
- `backend/`：FastAPI 后端。Route 放 `backend/app/api/routes/`，编排逻辑放 `backend/app/services/`，手写 SQL 放 `backend/sql/`。
- `frontend/`：Vite/React 前端。源码在 `frontend/src/`。
- `tests/`：Python 测试。单元测试放 `tests/unit_tests/`，集成测试放 `tests/integration_tests/`。
