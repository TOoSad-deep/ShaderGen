# ShaderGen

ShaderGen 把参考图片转换为可评估的无贴图 WebGL1 fragment shader。当前产品只保留 `scene_mvp` 最小骨架：React 上传与展示、FastAPI API、`png_to_shader_min` LangGraph 和 ShaderForge 确定性渲染/评分/优化。

## 事实来源

- Agent 规则：`AGENTS.md`
- 架构：`docs/ARCHITECTURE.md`
- 功能状态：`docs/FEATURES.md`
- 决策：`docs/DECISIONS.md`
- 当前交接：`PROGRESS.md`
- 历史审计摘要：`docs/evidence/registry.json` 与 `docs/progress/archive/`

## 常用命令

```bash
make setup
make dev-agent
make dev-backend
make dev-node-lab
make dev-frontend
make test
make docs-check
make check
make test-scene-mvp-ui
uv run pytest tests/integration_tests
uv run python scripts/run_scene_mvp_run_diagnostics.py --run-dir <run-dir> --output-dir <new-output-dir>
```

`make check` 执行单元测试、文档边界检查、LangGraph validate 和前端构建。跨组件改动还需按范围运行集成测试或页面 E2E。

`run_scene_mvp_acceptance_live_ab.py` 与 `run_scene_mvp_maturity_budget_replay.py` 仅保留旧 MinScene 纯函数、历史实现和既有报告审计。D076 已退役其 benchmark/Oracle 运行依赖，当前分支不得把它们作为可执行入口，也不能用旧结果证明 ShaderGraph 产品质量或授权修改当前预算。

## 当前边界

- `langgraph.json` 只注册 `png_to_shader_min`。
- `src/nodelab/` 与 `src/nodelab_service/` 提供通用 Node Lab 和独立服务；`/lab` 工作台连接端口 8090，产品 Backend 不注册其路由。
- `POST /api/shader/generate` 不再接受生成模式；所有请求执行 `scene_mvp`。
- 前端不再提供 V1 模式、项目 Memory、V1 score/review/current_best 展示。
- 只公开 `final-render`、`metrics`、`manifest` 三种运行 Artifact。
- 旧 V1 benchmark、runner、CI 及本地 `output/` 历史产物已按用户明确授权删除；registry 只保留摘要与原 SHA-256，不能据此复验原报告。
- Memory/checkpoint Python/SQL 实现和现存 PostgreSQL 数据保留，但 Backend 不再启动或调用这套资源。

## 配置

服务端从根目录 `.env` 读取模型、数据库、日志和 CORS 配置；前端只读取 `frontend/.env.local`。任何密钥都不得进入 `VITE_*`。

`scene_mvp` 的目标和 `fast|balanced|high|manual` 预算位于 `src/agent/app/config/png_to_shader_min.yaml`。模型失败会安全回退到确定性感知 Scene；Graph recursion limit 与 ShaderGraph program compile 上限均按合法最坏路径推导。

服务端环境变量清单：

```text
LANGSMITH_TRACING=
LANGSMITH_PROJECT=
LANGSMITH_API_KEY=
SHADER_GEN_MODEL_NAME=
SHADER_GEN_QWEN_ENABLE_THINKING=
SHADER_GEN_QWEN_OUTPUT_THINKING=
DASHSCOPE_API_KEY=
DASHSCOPE_BASE_URL=
GLM_API_KEY=
GLM_BASE_URL=
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=
KIMI_API_KEY=
KIMI_BASE_URL=
SHADER_GEN_KIMI_REASONING_EFFORT=
OPENAI_API_KEY=
OPENAI_BASE_URL=
DATABASE_URL=
TEST_DATABASE_URL=
LOG_LEVEL=
SHADERGEN_CORS_ORIGINS=
SHADERGEN_ENGINE_POLICY_PATH=
SHADERGEN_EVIDENCE_REGISTRY_PATH=
SHADERGEN_DIRECT_GLSL_KILL_SWITCH=
SHADERGEN_ENGINE_ROLLOUT_PRIVATE_ARTIFACT_ROOT=
LANGGRAPH_STRICT_MSGPACK=
NODELAB_ROOT=
NODELAB_BATCH_ROOT=
NODELAB_PIPELINE_ID=
NODELAB_APPLICATION_FACTORY=
NODELAB_CORS_ORIGINS=
NODELAB_LOG_LEVEL=
NODELAB_REAL_MODEL_ENABLED=
```

开发环境只允许显式 origin；生产环境拒绝通配符 CORS。
