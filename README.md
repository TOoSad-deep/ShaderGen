# ShaderGen

ShaderGen 是一个“图片生成视效 Shader”工程。最终技术架构以 `human_doc/shaderforge-technical-architecture-aligned(1).svg` 为准。

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
│   ├── nodelab/     # Pipeline 无关的调试、证据与 benchmark Harness 内核
│   ├── nodelab_service/ # 可独立启动的 Node Lab FastAPI 服务与插件组合根
│   └── shaderforge/ # 确定性领域核心：契约、测量、校验、渲染、评分、制品
├── tests/           # Python 单元测试和集成测试
├── docs/            # 架构、决策、功能状态
└── human_doc/       # 用户提供的权威材料
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
- Node Lab Service：`http://127.0.0.1:8090`
- Node Lab Swagger：`http://127.0.0.1:8090/docs`
- Vite 前端：`http://127.0.0.1:5173`
- Node Lab 工作台：`http://127.0.0.1:5173/lab`（需另行运行 `make dev-node-lab`）

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
NODELAB_ROOT=output/node-lab/service
NODELAB_BATCH_ROOT=output/benchmarks/node-lab-service
NODELAB_PIPELINE_ID=node_lab
NODELAB_APPLICATION_FACTORY=
NODELAB_REAL_MODEL_ENABLED=false
NODELAB_CORS_ORIGINS=http://127.0.0.1:5173,http://localhost:5173
NODELAB_LOG_LEVEL=INFO
SHADERGEN_NODE_LAB_REAL_MODEL_ENABLED=false
```

`DATABASE_URL` 配置后，Backend 使用独立 psycopg pool 运行 LangGraph Checkpointer/Store，并使用现有 asyncpg pool 写 Agent 过程账本。首次部署或 persistence 包升级后先执行 `make setup-memory-postgres`。`make test-memory-postgres` 优先使用 `TEST_DATABASE_URL`；未配置时会基于 `DATABASE_URL` 创建随机临时数据库，测试结束后自动删除。

当前发布状态、阻塞项和 gate 证据只以 `docs/FEATURES.md` 与 `PROGRESS.md` 为准。当前实现只保留 `png_to_shader_v1` Graph 和 `procedural_v1` 产品路径；旧基础对话 Graph、legacy 生成、独立 `/review` API 及其专属 Node 已删除。V1 服务端完成 WebGL1 render/evaluate/review/refine；正常结果返回 `current_best`、评分和 final Artifact，Evaluator 不可用时返回明确的 WebGL-valid `unscored_fallback`，不伪造评分。页面是否显示实验/no-go 必须跟随 F09 状态，唯一实现路径本身不代表已获准灰度。公开 Artifact API 只允许 final-render、metrics 和 manifest。

`SHADER_GEN_MODEL_NAME` 支持 `provider:model` 形式，例如 `dashscope:qwen3.7-plus`。`dashscope`、`openai`、`deepseek`、`glm` 表示凭据和 base URL 来源；真实模型名再决定使用 Qwen、GLM、DeepSeek 或 OpenAI 系列配置。

`SHADERGEN_NODE_LAB_REAL_MODEL_ENABLED` 只控制现有 PNG-to-Shader V1 real-model benchmark，不控制独立服务；独立服务使用 `NODELAB_REAL_MODEL_ENABLED`，且仍要求 factory/Provider 自身授权。

F09 M5 的确定性 AI-off smoke 可直接运行；真实 benchmark 会产生按量模型调用，必须使用显式命令和硬预算。报告、逐例失败证据和盲评页面写入 `output/benchmarks/png-to-shader-v1/`。最新 gate、run、比例、证据 hash 和验证基线只在 `PROGRESS.md`、`docs/FEATURES.md` 与 `docs/evidence/registry.json` 维护。

Node Lab 由通用 `nodelab` Harness 和可独立启动的 `nodelab_service` 组成。独立服务默认使用空安全 Application，不再静默装配 PNG-to-Shader V1；重构后的项目通过进程启动配置 `NODELAB_APPLICATION_FACTORY=module:callable` 注入自己的 Provider、Registry 和资源。产品 Backend 不再注册 `/api/lab/v1/*`。模块仍使用三项独立门禁：`make benchmark-node-lab-ai-off`、`make benchmark-node-lab-model` 和 `make test-node-lab-ui`；V1 benchmark 只是显式测试插件，不是服务默认语义。所有 Node Lab 报告都不覆盖 M5 证据。

人工学习可使用 `/lab` 页面、独立 Swagger 或 `scripts/run_node_lab_cli.py`。运行 `make dev-node-lab` 后，空服务位于 `127.0.0.1:8090`；要执行项目 Node，先安装包含 factory/Provider 的项目包，再设置 `NODELAB_APPLICATION_FACTORY`。私有 LabRun/Artifact 默认写入 `output/node-lab/service`，HTTP batch 报告写入 `output/benchmarks/node-lab-service`。完整教程见 `docs/NODE_LAB_GUIDE.md`，服务边界见 `src/nodelab_service/ARCHITECTURE.md`。

## 开发规则

- 一次只处理 `docs/FEATURES.md` 中一个 `active` 功能。
- 未通过验证命令，不得把功能标记为 `passing`。
- 会话结束前原地刷新 `PROGRESS.md` 的当前交接信息；例行验证更新现有基线，只有状态、契约、门禁、里程碑或重要缺口变化时才新增最近变更。
- 架构、目录边界、命令、环境变量、功能状态或前后端契约变化时，同步更新对应 Markdown。
- 对仓库事实无法确定且会影响架构、契约、数据、安全或验收的问题，先向用户确认。
- 产品 HTTP route 放 `backend/app/api/routes/`；Node Lab HTTP 只放 `src/nodelab_service/`。
- 后端请求/响应 schema 放 `backend/app/schemas/`，后端编排放 `backend/app/services/`。
- 手写 SQL schema 放 `backend/sql/`，文件名按顺序编号并保持幂等。
- Prompt 放在 `src/agent/app/prompts/*.yaml`，后端只调用 `agent.app.services.*`。
- 产品 HTTP 边界放 `backend/`，Node Lab HTTP 边界放 `src/nodelab_service/`，前端交互放 `frontend/`，确定性领域核心后续放 `src/shaderforge/`。
