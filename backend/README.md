# Backend

后端负责 FastAPI 应用入口、HTTP 路由、请求校验、错误响应、应用生命周期、数据库连接和服务端密钥保护。后端是系统入口和编排层，不是领域算法仓库。

## 目录规范

- `app/main.py`：创建 FastAPI 应用、注册中间件、生命周期和路由。
- `app/api/router.py`：聚合后端 HTTP 路由。
- `app/api/routes/`：HTTP 路由。只做请求解析、边界校验、调用 service、返回响应。
- `app/services/`：后端编排逻辑。可以调用 `src/agent/` 或后续 `src/shaderforge/`，但不要把大型领域算法写在这里。
- `app/schemas/`：请求和响应数据结构。API 契约稳定后放这里，避免散落在 route 文件。
- `app/database/session.py`：数据库连接生命周期、schema 初始化和健康检查查询。
- `app/database/agent_memory.py`：LangGraph psycopg saver/store 生命周期、健康检查和独立 setup。
- `app/middleware/`：FastAPI 中间件，例如请求日志。
- `app/core/`：应用级基础配置，例如日志配置。
- `sql/`：手写 SQL schema 记录目录。当前只存建表脚本，不放 Python 迁移框架。

## 数据库 SQL 规则

- SQL 文件按顺序编号，例如 `001_agent_process.sql`。
- 当前使用 Postgres 语法，配合现有 `asyncpg` 连接池。
- 配置 `DATABASE_URL` 时，后端启动会按文件名顺序执行 `sql/*.sql`，确保表结构存在。
- Agent 过程数据落业务表；运行内需要查询的安全诊断摘要可写入 `agent_logs`。
- `POST /api/shader/generate` 在数据库连接池可用时，会写入 `agent_runs`、`agent_events` 和 `agent_logs`。
- `POST /api/shader/review` 在数据库连接池可用时，也会写入 `agent_runs`、`agent_events` 和 `agent_logs`。
- `agent_events.reasoning_content` 保存模型返回的思维链，仅用于受控调试和评估，不进入对外 API 响应。
- `agent_runs.project_id` 关联一次运行与 Shader 项目；清除 Memory 不删除过程账本。
- 过程数据写库成功时记录 `agent.process.database.write.succeeded`；写库失败时记录 `agent.process.database.write.failed`。
- 过程数据写入编排放在 `app/services/agent_process_store.py`；route 不直接写数据库过程表。
- `agent_logs` 允许 `debug` 级别，但只用于运行内关键调试摘要，不接普通 debug logging。
- 普通请求日志、普通 debug 日志、完整堆栈和基础设施日志继续走 Python logging。
- 数据库不要保存密钥、API key、完整模型供应商原始响应或 base64 图片；思维链只允许写入 `agent_events.reasoning_content` 单列。

## 路由规则

- route 负责 HTTP 边界：状态码、上传大小、content type、请求字段校验。
- route 不写 Prompt、不直接调用模型、不实现搜索/评分/渲染算法。
- route 捕获外部调用异常时，应返回明确的 HTTP 错误；日志记录内部细节，响应给用户的信息保持可理解。
- `POST /api/shader/review` 接收 `original_file`、`rendered_file` 和 `glsl`，由 Agent 根据原图、当前渲染图和 GLSL 返回评估与修改建议。
- `POST /api/shader/generate` 接收可选 `project_id`；未提供时创建 UUID。Generate/Review 都返回 `memory_status`。
- `POST /api/shader/review` 额外要求 `project_id`。
- `DELETE /api/shader/projects/{project_id}/memory` 删除 checkpoint thread 和 Store Memory，不删除审计账本。
- 单进程内同一 `project_id` 并发请求立即返回 `409 project_busy`。
- 新增路由必须注册到 `app/api/router.py`。
- 新增业务 route 文件按领域命名，例如 `shader.py`、`health.py`；不要创建没有真实 endpoint 的空 route。

## Service 规则

- service 负责一次后端用例的编排，例如“接收图片并生成 GLSL”。
- 涉及模型编排时，只调用 `agent.app.services.*` 明确暴露的公共用例服务，不直接 import Agent 内部模型、Prompt 加载器或 LangChain 消息类型。
- Agent 返回的 `model_calls`、`events` 和 `logs` 由 service/route 编排后统一交给 `agent_process_store.py` 写库；Agent 不直接拿数据库连接池。
- 涉及确定性领域逻辑时调用后续 `src/shaderforge/`。
- service 中可做轻量数据转换；复杂 IR、DSL、评分、搜索逻辑不要留在 `backend/`。
- 不为未来功能提前创建 `user_service.py`、`file_service.py`、`auth/` 等空模块；有真实 endpoint、数据或鉴权需求时再加。

## Schema 规则

- 对外 API 请求/响应优先放在 `app/schemas/`；只有一次性内部临时结构才留在局部文件。
- schema 字段名应和前端 `src/api/` 类型保持一致。
- 对外响应不要暴露内部异常类型、模型供应商字段或密钥相关信息。

## 测试规则

- route 和 service 改动至少运行：

```bash
uv run pytest tests/unit_tests
make test-memory-postgres
```

首次部署先运行 `make setup-memory-postgres`。应用运行时不执行 LangGraph DDL，只验证 saver/store schema；无 `DATABASE_URL` 时使用明确标记的进程内临时记忆。

- HTTP 行为优先用 `fastapi.testclient.TestClient` 测试。
- 依赖模型、数据库或外部服务时使用模拟对象；除非明确写成集成测试，不调用真实服务。
- 跨 backend、agent、shaderforge 的流程放到 `tests/integration_tests/`。
- 后端路由、service、schema、错误处理或测试规则变化时，同步更新本文档。
