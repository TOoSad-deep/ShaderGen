# ShaderGen

ShaderGen 是一个“图片生成视效 Shader”工程。`human_doc/shaderforge-technical-architecture-aligned(1).svg` 保留为最初设计参考，不作为当前架构的覆盖性权威。

## 事实来源

- Agent 入口：`AGENTS.md`
- 架构规范：`docs/ARCHITECTURE.md`
- 功能状态：`docs/FEATURES.md`
- 决策记录：`docs/DECISIONS.md`
- 当前进度与下一步：`PROGRESS.md`
- 验收证据与耐久性：`docs/evidence/registry.json`
- 历史进度快照：`docs/progress/archive/`（仅供审计，不代表当前事实）
- 前端细则：`frontend/README.md`
- 后端细则：`backend/README.md`

## 目录结构

```text
ShaderGen/
├── frontend/        # 用户输入、WebGL 预览、结果展示
├── backend/         # FastAPI HTTP 边界和后端编排
│   └── sql/         # 后端启动时按文件名顺序执行的手写 SQL
├── src/
│   ├── agent/       # LangGraph Agent 包，内部入口为 agent.app.*
│   └── shaderforge/ # 确定性领域核心：契约、测量、校验、渲染、评分、制品
├── tests/           # Python 单元测试和集成测试
├── docs/            # 架构、决策、功能状态
└── human_doc/       # 用户提供的源材料；具体权威关系见 docs/ARCHITECTURE.md
```

`src/shaderforge/` 只创建已经进入 active 功能且有真实实现与测试的子包，不预建空目录。

`make setup` 会同步 Python/前端依赖，并安装 M1 WebGL1 Renderer 使用的 Playwright Chromium。只需补装浏览器时可运行 `uv run playwright install chromium`。

## 常用命令

```bash
make setup
make setup-memory-postgres
make dev-agent
make dev-backend
make dev-node-lab
make dev-frontend
make test
make docs-check
make test-memory-postgres
make check
make benchmark-ai-off
make benchmark-node-lab-ai-off
make benchmark-node-lab-model
make test-node-lab-ui
make test-scene-mvp-ui
make benchmark-png-to-shader QUALITY_PRESET=balanced MODEL_CALL_BUDGET=80
npm --prefix frontend run e2e:procedural-v1
```

`make check` 是默认主干验证，只覆盖单元测试、docs-check、LangGraph validate 和前端构建；跨组件改动仍需按范围追加集成测试、浏览器 E2E、PostgreSQL 或 benchmark，真实模型 benchmark 继续要求显式按量调用。

GitHub 主 CI 使用 Python 3.12、Node 22、`uv sync --locked` 和 `npm ci --prefix frontend` 后执行完整 `make check`、全仓 Ruff 与 `mypy --strict src backend`，另以 Python 3.10/3.11 运行兼容性单测。定时集成测试和 PNG-to-Shader benchmark 才安装 Playwright Chromium；集成测试不注入模型密钥，真实模型 benchmark 仍只在仓库变量或手动输入显式开启时运行。

服务默认地址：

- LangGraph API：`http://127.0.0.1:2024`
- LangGraph Studio：`https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`
- FastAPI 后端：`http://127.0.0.1:8088`
- FastAPI 文档：`http://127.0.0.1:8088/docs`
- Vite 前端：`http://127.0.0.1:5173`
- Node Lab 工作台：`http://127.0.0.1:5173/lab`（Backend 需用 `make dev-node-lab` 显式开启）

## 配置

环境变量按运行时分层：

- Agent、Backend 和仓库脚本读取根目录 `.env`；从 `.env.example` 复制，不要提交真实密钥。
- Vite 只读取 `frontend/.env.local` 或启动它的 shell；从 `frontend/.env.example` 复制。所有 `VITE_*` 值都会进入浏览器产物，禁止放置任何秘密。
- 服务端关键变量如下，名称与根目录 `.env.example` 保持一致：

```text
LANGSMITH_TRACING=false
LANGSMITH_PROJECT=ShaderGen
LANGSMITH_API_KEY=
SHADER_GEN_MODEL_NAME=dashscope:qwen3.7-plus
SHADER_GEN_QWEN_ENABLE_THINKING=
SHADER_GEN_QWEN_OUTPUT_THINKING=false
DASHSCOPE_API_KEY=
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
GLM_API_KEY=
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
OPENAI_API_KEY=
OPENAI_BASE_URL=
LOG_LEVEL=INFO
SHADERGEN_CORS_ORIGINS=http://127.0.0.1:5173,http://localhost:5173
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DATABASE
TEST_DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/SHADERGEN_TEST
LANGGRAPH_STRICT_MSGPACK=true
SHADERGEN_NODE_LAB_ENABLED=false
SHADERGEN_NODE_LAB_ROOT=
SHADERGEN_NODE_LAB_BATCH_ROOT=
SHADERGEN_NODE_LAB_REAL_MODEL_ENABLED=false
```

`DATABASE_URL` 配置后，Backend 使用独立 psycopg pool 运行 LangGraph Checkpointer/Store，并使用现有 asyncpg pool 写 Agent 过程账本。首次部署或 persistence 包升级后先执行 `make setup-memory-postgres`。`make test-memory-postgres` 优先使用 `TEST_DATABASE_URL`；未配置时会基于 `DATABASE_URL` 创建随机临时数据库，测试结束后自动删除。

当前发布状态、阻塞项和 gate 证据只以 `docs/FEATURES.md` 与 `PROGRESS.md` 为准。当前注册默认 `png_to_shader_v1` 和实验性 `png_to_shader_min` 两个 Graph；产品表单默认 `procedural_v1`，也可显式选择 `scene_mvp` 快速贯通路径。旧基础对话 Graph、legacy 生成、独立 `/review` API 及其专属 Node 已删除。V1 服务端完成 WebGL1 render/evaluate/review/refine；`scene_mvp` 当前完成确定性感知、严格 Model Author、模型/fallback 真实渲染仲裁、多 feature 固定模板、prepared uniform 热渲染、累计式确定性参数搜索、前景/高光/阴影复合评分、Artifact 和 trace，尚未引入 CMA-ES/2000 draw。`fast|balanced|high` 会分别限制 render/LLM/Refine 为 `48/2/1`、`96/4/2`、`160/6/3`，模型失败会安全回退；密钥仍只配置在根目录 `.env`。两条路径均只公开 final-render、metrics 和 manifest，实验入口不代表已获准灰度。

`SHADER_GEN_MODEL_NAME` 支持 `provider:model` 形式，例如 `dashscope:qwen3.7-plus`。`dashscope`、`openai`、`deepseek`、`glm` 表示凭据和 base URL 来源；真实模型名再决定使用 Qwen、GLM、DeepSeek 或 OpenAI 系列配置。

F09 M5 的确定性 AI-off smoke 可直接运行；真实 benchmark 会产生按量模型调用，必须使用显式命令和硬预算。报告、逐例失败证据和盲评页面写入 `output/benchmarks/png-to-shader-v1/`。最新 gate、run、比例、证据 hash 和验证基线只在 `PROGRESS.md`、`docs/FEATURES.md` 与 `docs/evidence/registry.json` 维护。

Node Lab 模块使用三项独立门禁：`make benchmark-node-lab-ai-off` 覆盖 capability、真实 node target、scenario/pipeline、Renderer cold/warm 和 direct-vs-HTTP transport；`make benchmark-node-lab-model` 用固定 fixture 离线检查五个模型角色；`make test-node-lab-ui` 用假 API 验收工作台。H02 的当前状态与证据见 `docs/FEATURES.md`。模型报告按角色聚合 Parser/Schema/binding/timeout、latency、token、费用和 requested/actual model；中断可恢复但仍留在分母，样本不足 20 时 p95 为 `null`。真实模型诊断必须运行 `SHADERGEN_NODE_LAB_REAL_MODEL_ENABLED=true uv run python scripts/run_node_lab_model_benchmark.py --execution-mode real --allow-model-calls`，并受 manifest 的调用、provider 输出 token、总 token、时间和费用硬预算限制。三类 CLI 只向 stdout 输出 suite/status/report path 单行 JSON，case 失败返回非零。所有 Node Lab 报告都不覆盖 M5 证据。

人工学习可使用 `/lab` 页面、Swagger 或 `scripts/run_node_lab_cli.py`；生产 Provider 提供机器可读 descriptor 和输入示例，步骤列表可重建 `base_step_id` DAG，Artifact 列表只返回同一 LabRun 的私有 descriptor。启动 Backend 必须使用 `make dev-node-lab` 或在进程导入前设置 `SHADERGEN_NODE_LAB_ENABLED=true`；默认关闭。私有 LabRun/Artifact 默认写入 `output/node-lab/http`，HTTP batch 报告默认写入 `output/benchmarks/node-lab-http`，分别可用 `SHADERGEN_NODE_LAB_ROOT` 与 `SHADERGEN_NODE_LAB_BATCH_ROOT` 覆盖。完整教程见 `docs/NODE_LAB_GUIDE.md`。

## 开发规则

- 一次只处理 `docs/FEATURES.md` 中一个 `active` 功能。
- 未通过验证命令，不得把功能标记为 `passing`。
- 会话结束前原地刷新 `PROGRESS.md` 的当前交接信息；例行验证更新现有基线，只有状态、契约、门禁、里程碑或重要缺口变化时才新增最近变更。
- 架构、目录边界、命令、环境变量、功能状态或前后端契约变化时，同步更新对应 Markdown。
- 对仓库事实无法确定且会影响架构、契约、数据、安全或验收的问题，先向用户确认。
- HTTP route 放 `backend/app/api/routes/`，并在 `backend/app/api/router.py` 注册。
- 后端请求/响应 schema 放 `backend/app/schemas/`，后端编排放 `backend/app/services/`。
- 手写 SQL schema 放 `backend/sql/`，文件名按顺序编号并保持幂等。
- Prompt 放在 `src/agent/app/prompts/*.yaml`，后端只调用 `agent.app.services.*`。
- HTTP 边界放 `backend/`，前端交互放 `frontend/`，确定性领域核心后续放 `src/shaderforge/`。
