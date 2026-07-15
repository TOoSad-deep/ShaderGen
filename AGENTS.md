# AGENTS.md

## 项目概览

ShaderGen 是一个“图片生成视效 Shader”工程：前端接收用户输入，后端提供 API，LangGraph agent 和后续 ShaderForge 核心流水线把需求转成可评估的 GLSL/渲染结果。最终技术架构以 `human_doc/shaderforge-technical-architecture-aligned(1).svg` 为准。

## 常用命令

- 安装：`make setup`（包括 Playwright Chromium）
- 初始化 Memory PostgreSQL：`make setup-memory-postgres`
- LangGraph：`make dev-agent`
- 后端：`make dev-backend`
- Node Lab 后端：`make dev-node-lab`（仅本地调试，显式开放 `/api/lab/v1/*`）
- 前端：`make dev-frontend`
- 单元测试：`make test`
- Memory PostgreSQL 验收：`make test-memory-postgres`
- 文档边界检查：`make docs-check`
- 默认主干验证（单元测试、docs-check、LangGraph validate、前端构建）：`make check`
- M5 无模型 smoke：`make benchmark-ai-off`
- Node Lab AI-off：`make benchmark-node-lab-ai-off`
- Node Lab 五模型角色离线 fixture：`make benchmark-node-lab-model`
- Node Lab 真实模型角色：`SHADERGEN_NODE_LAB_REAL_MODEL_ENABLED=true uv run python scripts/run_node_lab_model_benchmark.py --execution-mode real --allow-model-calls`（显式按量调用）
- Node Lab 页面验收：`make test-node-lab-ui`
- M5 真实模型 benchmark：`make benchmark-png-to-shader QUALITY_PRESET=balanced MODEL_CALL_BUDGET=80`（显式按量调用）
- M5 人工盲评 gate：`make benchmark-gate BENCHMARK_OUTPUT=<run-dir> HUMAN_REVIEW=<review.json>`

## 硬约束

- 每次只处理一个 `active` 功能。
- 未通过验证不得在 `docs/FEATURES.md` 标记为 `passing`。
- 涉及跨组件行为必须跑对应端到端或集成检查；没有自动化检查时，在 `PROGRESS.md` 写明缺口。
- 会话结束前更新 `PROGRESS.md`，重要取舍写入 `docs/DECISIONS.md`。
- 文档、计划、代码注释和 SQL 注释尽量使用中文；保留必要的英文技术名词、代码标识符和外部 API 名称。
- 架构、目录边界、命令、环境变量、功能状态或前后端契约变化时，必须同步更新对应 Markdown。
- Graph 可视化属于 Graph 实现的一部分：凡是新增、删除、重命名节点，修改直接边、条件边、路由结果、循环、终止路径、`current_best` 安全边界或 `langgraph.json` 注册，必须在同一次改动中同步对应 `*_graph.py` Builder 上方的 ASCII 图、`src/agent/app/graphs/ARCHITECTURE.md` 的 Mermaid 区块及相关路由表/安全说明；未通过 `make docs-check` 和 `uv run langgraph validate` 不得视为完成。
- 对仓库事实无法确定且会影响架构、契约、数据、安全或验收的问题，先向用户确认，不要自行猜测。
- 密钥只放 `.env`，不要提交真实 API key。
- 真实模型 benchmark 不进入普通测试；必须使用固定 manifest、显式调用开关和整套硬预算，失败产物不得覆盖或删除。

## 按需阅读

- 架构边界：`docs/ARCHITECTURE.md`
- 功能状态机：`docs/FEATURES.md`
- 决策记录：`docs/DECISIONS.md`
- 当前进度：`PROGRESS.md`
- 前端细则：`frontend/README.md`
- 后端细则：`backend/README.md`
- Graph 与路由开发：`src/agent/app/graphs/ARCHITECTURE.md`（涉及 Graph、routing 或节点跳转语义时必须先读）

## 仓库边界

- `src/agent/`：LangGraph 智能体核心，内部采用 `src/agent/app/` 规范结构。Prompt 只放 `src/agent/app/prompts/*.yaml`。
- `src/shaderforge/`：后续领域核心流水线。只有功能需要真实代码时才创建对应子包。
- `backend/`：FastAPI 后端。Route 放 `backend/app/api/routes/`，编排逻辑放 `backend/app/services/`，手写 SQL 放 `backend/sql/`。
- `frontend/`：Vite/React 前端。源码在 `frontend/src/`。
- `tests/`：Python 测试。单元测试放 `tests/unit_tests/`，集成测试放 `tests/integration_tests/`。
