# Backend

后端负责 FastAPI 应用入口、HTTP 路由、请求校验、错误响应、应用生命周期、数据库连接和服务端密钥保护。后端是系统入口和编排层，不是领域算法仓库。

## 目录规范

- `app/main.py`：应用组合根；通过 `create_app(settings)` 创建 FastAPI 应用，并用 `AsyncExitStack` 组装资源生命周期、Agent service、中间件和路由。
- `app/api/router.py`：聚合后端 HTTP 路由。
- `app/api/routes/`：HTTP 路由。只做请求解析、边界校验、调用 service、返回响应。
- `app/services/`：后端编排逻辑。可以调用 `src/agent/` 或后续 `src/shaderforge/`，但不要把大型领域算法写在这里。
- `app/services/shader_generation.py`：`POST /api/shader/generate` 的 V1 用例服务；统一负责项目锁、V1 模型选择、Agent 调用、生成总账、失败分类和公开响应契约。
- `app/schemas/`：请求和响应数据结构。API 契约稳定后放这里，避免散落在 route 文件。
- `app/database/session.py`：数据库连接生命周期、schema 初始化和健康检查查询。
- `app/database/agent_memory.py`：LangGraph psycopg saver/store 生命周期、健康检查和独立 setup；只返回中立的 Memory 资源，不反向创建 Agent service。
- `app/middleware/`：FastAPI 中间件，例如请求日志。
- `app/core/settings.py`：不可变 Backend 配置模型；只在应用组合根读取根目录 `.env` 和环境变量。
- `app/core/`：其余应用级基础设施，例如日志配置；底层模块不得自行重复读取环境变量。
- `sql/`：手写 SQL schema 资源包。`backend.sql` 在 wheel 中显式登记，`__init__.py` 只声明包边界；当前只存建表脚本，不放 Python 迁移或业务编排。

## 数据库 SQL 规则

- SQL 文件按顺序编号，例如 `001_agent_process.sql`。
- 当前使用 Postgres 语法，配合现有 `asyncpg` 连接池。
- 配置 `DATABASE_URL` 时，后端启动会按文件名顺序执行 `sql/*.sql`，确保表结构存在。
- Agent 过程数据落业务表；运行内需要查询的安全诊断摘要可写入 `agent_logs`。
- LangGraph Checkpointer/Store 使用独立 psycopg pool，并在每次借出连接前执行 `AsyncConnectionPool.check_connection`；远端关闭的陈旧连接必须在进入 Graph 前淘汰，不能通过重试整个 Graph 恢复，以免重复模型调用或候选写入。
- FastAPI lifespan 在每项资源初始化前登记对应补偿清理：启动任一步失败也会逆序回滚，关闭 Agent Memory 失败不得跳过 asyncpg 过程账本连接池关闭。关闭函数先从 `app.state` 脱离资源再等待底层 pool，以免关闭异常留下可继续借用的失效对象。
- `BackendSettings` 在应用组合根一次性读取根目录 `.env`，并把数据库、日志与 CORS 作为不可变配置注入 lifespan、Router 和 Service；`SHADERGEN_CORS_ORIGINS` 使用逗号分隔的显式 Origin，禁止通配符 `*`。Node Lab 使用独立进程配置，不进入 Backend settings。
- `POST /api/shader/generate` 在数据库连接池可用时，会写入 `agent_runs`、`agent_events` 和 `agent_logs`。
- `procedural_v1` 把模式、质量档位和补充约束写入 run input，把停止原因、current_best、评分和公开 Artifact URL 写入 run result；Graph 返回的每个阶段事件与 `current_best_updated` 逐项写入 `agent_events`。
- 生成终态的模型调用、阶段事件、Agent 日志和 `agent_runs` 更新使用同一个 asyncpg 显式事务；任一步失败必须整体回滚。事务先 `FOR UPDATE` 锁定 run：相同终态重放直接 no-op，不同终态重放显式报冲突，禁止静默覆盖。
- `agent_events.reasoning_content` 只允许保存节点显式 opt-in 捕获的思维链；V1 默认不捕获、不打印 reasoning，对外 API 永不返回该字段。
- `agent_runs.project_id` 关联一次运行与 Shader 项目；清除 Memory 不删除过程账本。
- 过程数据写库成功时记录 `agent.process.database.write.succeeded`；写库失败时记录 `agent.process.database.write.failed`，并带 `persistence_stage` 定位创建 run 或终态事务阶段。
- Graph 已得到可返回的 Shader 后，终态账本提交故障记录 `shader.generate.success_persistence_failed`，但不再用数据库异常覆盖成功响应；失败账本提交故障同样不得覆盖原始类型化业务错误。
- 生成 run 初始总账创建失败时，生成服务不得继续执行；响应映射为 503 `persistence_unavailable`、`stage=persistence`、`retryable=true`，安全日志使用 `persistence_stage=create_generation_run` 定位且不打印数据库异常原文。
- 过程数据的原子读写放在 `app/services/agent_process_store.py`；生成用例对这些操作的调用时序放在 `app/services/shader_generation.py`，route 不直接写生成过程表。
- `agent_logs` 允许 `debug` 级别，但只用于运行内关键调试摘要，不接普通 debug logging。
- 普通请求日志、普通 debug 日志、完整堆栈和基础设施日志继续走 Python logging。
- `LOG_LEVEL` 同时控制 `backend`、`agent` 和 `shaderforge` logger；默认 `INFO` 时，后端终端可直接看到 `shader.generate.*` 与 `shader.pipeline.*` 的阶段日志。
- FastAPI 在进入 route 前产生的 422 使用 `request.validation_failed` 打印字段路径、校验类型和提示；不打印字段原值，因此可与业务闭环返回的 `shader.generate.no_validated_result` 区分。
- V1 开始日志包含 `run_id`、`project_id`、模式、质量档和图片字节数；模型阶段日志包含剩余 wall-time/调用数和累计模型耗时；Renderer 日志区分静态校验、WebGL compile、Renderer 与 Oracle 评估；成功日志包含 candidate、fallback 状态和 loss。WebGL compiler 原文只保存在私有 compile Artifact，普通事件/数据库只写日志长度和 SHA-256。
- V1 没有有效候选时，终端 `shader.generate.no_validated_result` 和 `agent_logs` 都记录安全诊断：`run_id`、`project_id`、停止原因、失败阶段/事件/异常类型、后端与 Graph 耗时、候选数、模型调用数与耗时和修复次数。`agent_runs.result` 在失败时也保留同一摘要，便于只查询 run 总账定位问题。
- `agent_runs.error` 只持久化异常类型和安全停止原因，不写供应商异常原文；Python 结构化日志也不打印图片、完整 GLSL、用户补充约束或模型原始响应。
- 安全诊断不得包含上传图片、完整 GLSL、reasoning、模型供应商原始响应、用户补充约束正文或密钥；生成失败使用稳定的 FastAPI `detail` envelope，至少包含 `message`、`code`、`run_id`、`stage`、`retryable` 和 `stop_reason`。
- 数据库不要保存密钥、API key、完整模型供应商原始响应或 base64 图片；显式 opt-in 的思维链也只能写入 `agent_events.reasoning_content` 单列，并应配置访问控制与保留策略。

## 路由规则

- route 负责 HTTP 边界：状态码、上传大小、content type、请求字段校验。
- `POST /api/shader/generate` route 只把校验后的输入和应用生命周期依赖组装为 command/dependencies，调用 `execute_shader_generation()`，再把稳定用例错误映射为现有 FastAPI error envelope；不得重新承载锁、Agent 分流、账本或响应契约编排。
- route 不写 Prompt、不直接调用模型、不实现搜索/评分/渲染算法。
- route 捕获外部调用异常时，应返回明确的 HTTP 错误；日志记录内部细节，响应给用户的信息保持可理解。
- `POST /api/shader/generate` 只执行 PNG-to-Shader V1，接收可选 `project_id`、可选且只能为 `procedural_v1` 的 `generation_mode`、`quality_preset=fast|balanced|high` 和最长 2,000 字的 `instruction`；未提供 project 时创建 UUID，未提供 mode 时默认 V1。
- V1 generate 响应包含 `run_id`、质量档位、视觉修订次数、停止原因、候选 id、`unscored_fallback`、规范化 render 尺寸、评分及 final-render/metrics/manifest URL。WebGL 有效但 evaluator 不可用的降级结果仍返回 GLSL 与 final-render，同时明确 `unscored_fallback=true`、`score=null`、`metrics_url=null`，禁止伪造评分。请求边界错误返回类型化 400/413/422；Renderer、模型供应商或运行账本不可用返回 503；全局或模型阶段超时返回 504；模型响应错误返回 502；内部 pipeline 不变量错误返回 500；编译修复耗尽仍返回类型化 422。任何失败响应都不返回 reasoning 或原始异常。
- 成功 run 必须先通过完整 `ShaderResponse` 契约构造，再写入 `status=succeeded`；响应契约失败使用 `shader.generate.response_contract_failed`，并把过程账本标记为 failed，禁止出现“账本成功但 HTTP 500”。
- `GET /api/shader/runs/{run_id}/artifacts/{artifact_name}` 只接受 `final-render`、`metrics`、`manifest` 三个固定名字；未知名字和不存在的 run 统一返回 404，不接受 filesystem path。
- `DELETE /api/shader/projects/{project_id}/memory` 删除 checkpoint thread 和 Store Memory，不删除审计账本。
- 单进程内同一 `project_id` 并发请求立即返回 `409 project_busy`。
- V1 checkpoint 使用稳定的 `png-to-shader-v1:{project_id}` thread 命名；清除项目记忆会清除该 checkpoint、旧 Graph 遗留的裸 `{project_id}` checkpoint 和项目 Store Memory，但不删除过程账本或 Artifact。
- 新增路由必须注册到 `app/api/router.py`。
- 新增业务 route 文件按领域命名，例如 `shader.py`、`health.py`；不要创建没有真实 endpoint 的空 route。

### Node Lab 服务边界

Backend 不再拥有或注册 Node Lab Route、Schema、Service 和环境开关。`make dev-backend` 只开放产品 API；`/api/lab/v1/*` 由默认监听 `8090` 的 `nodelab_service` 独立进程提供。独立服务的配置、Application factory、安全规则与 HTTP 契约见 `src/nodelab_service/ARCHITECTURE.md`。

## Service 规则

- service 负责一次后端用例的编排，例如“接收图片并生成 GLSL”。
- 涉及模型编排时，只调用 `agent.app.services.*` 明确暴露的公共用例服务，不直接 import Agent 内部模型、Prompt 加载器或 LangChain 消息类型。
- Agent 返回的 `model_calls`、`events` 和 `logs` 由 Backend service 编排后统一交给 `agent_process_store.py` 写库；Agent 不直接拿数据库连接池。
- 涉及确定性领域逻辑时调用后续 `src/shaderforge/`。
- service 中可做轻量数据转换；复杂 IR、DSL、评分、搜索逻辑不要留在 `backend/`。
- 不为未来功能提前创建 `user_service.py`、`file_service.py`、`auth/` 等空模块；有真实 endpoint、数据或鉴权需求时再加。

## Schema 规则

- 对外 API 请求/响应优先放在 `app/schemas/`；只有一次性内部临时结构才留在局部文件。
- schema 字段名应和前端 `frontend/src/api/` 类型保持一致。
- 对外响应不要暴露内部异常类型、模型供应商字段或密钥相关信息。

## 测试规则

- route、schema 和 service 改动至少运行受影响的 unit/TestClient 测试；跨模块或无法可靠缩小范围时运行 `uv run pytest tests/unit_tests`。
- 按受影响边界追加门禁：

| 改动范围 | 追加验证 |
|---|---|
| 数据库生命周期、Checkpointer、Store、Memory 隔离或清理 | `make test-memory-postgres` |
| Backend → Agent → ShaderForge 产品流程或 Artifact 契约 | `uv run pytest tests/integration_tests/test_png_to_shader_v1_api.py` |
| 产品浏览器行为 | `npm --prefix frontend run e2e:procedural-v1`；Memory 行为再追加 `npm --prefix frontend run e2e:memory` |
| 独立 Node Lab transport 与前端连接 | `nodelab_service` 相关 unit/TestClient 测试，并追加 `make test-node-lab-ui` |

跨多个范围时运行对应验证的并集；例如只改 health route 不要求 PostgreSQL 验收。

首次部署先运行 `make setup-memory-postgres`。应用运行时不执行 LangGraph DDL，只验证 saver/store schema；无 `DATABASE_URL` 时使用明确标记的进程内临时记忆。

- HTTP 行为优先用 `fastapi.testclient.TestClient` 测试。
- 依赖模型、数据库或外部服务时使用模拟对象；除非明确写成集成测试，不调用真实服务。
- 跨 backend、agent、shaderforge 的流程放到 `tests/integration_tests/`。
- M4 产品路径使用 `tests/integration_tests/test_png_to_shader_v1_api.py` 覆盖 Backend -> Agent Service -> Artifact；浏览器验收运行 `npm --prefix frontend run e2e:procedural-v1`。
- 后端路由、service、schema、错误处理或测试规则变化时，同步更新本文档。
