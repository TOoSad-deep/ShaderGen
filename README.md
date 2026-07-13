# ShaderGen

ShaderGen 是一个“图片生成视效 Shader”工程。最终技术架构以 `human_doc/shaderforge-technical-architecture-aligned(1).svg` 为准。

## 事实来源

- Agent 入口：`AGENTS.md`
- 架构规范：`docs/ARCHITECTURE.md`
- 功能状态：`docs/FEATURES.md`
- 决策记录：`docs/DECISIONS.md`
- 当前进度：`PROGRESS.md`
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
│   └── shaderforge/ # 后续领域核心流水线，按功能需要创建
├── tests/           # Python 单元测试和集成测试
├── docs/            # 架构、决策、功能状态
└── human_doc/       # 用户提供的权威材料
```

`src/shaderforge/` 不是当前必须存在的空目录；只有功能需要真实实现时才创建对应子包。

## 常用命令

```bash
make setup
make setup-memory-postgres
make dev-agent
make dev-backend
make dev-frontend
make test
make docs-check
make test-memory-postgres
make check
```

服务默认地址：

- LangGraph API：`http://127.0.0.1:2024`
- LangGraph Studio：`https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`
- FastAPI 后端：`http://127.0.0.1:8088`
- FastAPI 文档：`http://127.0.0.1:8088/docs`
- Vite 前端：`http://127.0.0.1:5173`

## 配置

环境变量放在 `.env`，不要提交真实密钥。常用变量：

```text
LANGSMITH_API_KEY=
SHADER_GEN_MODEL_NAME=
DASHSCOPE_API_KEY=
DASHSCOPE_BASE_URL=
SHADER_GEN_QWEN_ENABLE_THINKING=
SHADER_GEN_QWEN_OUTPUT_THINKING=
GLM_API_KEY=
GLM_BASE_URL=
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=
OPENAI_API_KEY=
OPENAI_BASE_URL=
DATABASE_URL=
TEST_DATABASE_URL=
LANGGRAPH_STRICT_MSGPACK=true
LOG_LEVEL=
```

`DATABASE_URL` 配置后，Backend 使用独立 psycopg pool 运行 LangGraph Checkpointer/Store，并使用现有 asyncpg pool 写 Agent 过程账本。首次部署或 persistence 包升级后先执行 `make setup-memory-postgres`。`make test-memory-postgres` 优先使用 `TEST_DATABASE_URL`；未配置时会基于 `DATABASE_URL` 创建随机临时数据库，测试结束后自动删除。

`SHADER_GEN_MODEL_NAME` 支持 `provider:model` 形式，例如 `dashscope:qwen3.7-plus`。`dashscope`、`openai`、`deepseek`、`glm` 表示凭据和 base URL 来源；真实模型名再决定使用 Qwen、GLM、DeepSeek 或 OpenAI 系列配置。

## 开发规则

- 一次只处理 `docs/FEATURES.md` 中一个 `active` 功能。
- 未通过验证命令，不得把功能标记为 `passing`。
- 会话结束前更新 `PROGRESS.md`。
- 架构、目录边界、命令、环境变量、功能状态或前后端契约变化时，同步更新对应 Markdown。
- 对仓库事实无法确定且会影响架构、契约、数据、安全或验收的问题，先向用户确认。
- HTTP route 放 `backend/app/api/routes/`，并在 `backend/app/api/router.py` 注册。
- 后端请求/响应 schema 放 `backend/app/schemas/`，后端编排放 `backend/app/services/`。
- 手写 SQL schema 放 `backend/sql/`，文件名按顺序编号并保持幂等。
- Prompt 放在 `src/agent/app/prompts/*.yaml`，后端只调用 `agent.app.services.*`。
- HTTP 边界放 `backend/`，前端交互放 `frontend/`，确定性领域核心后续放 `src/shaderforge/`。
