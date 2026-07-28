# Shader Memory 与 Context Engineering 设计

> 归档状态：历史且已被当前运行模式覆盖，不得作为当前产品路径或开发任务。

状态：已实现并通过 `F08` 全部验收门禁。

## 1. 背景

当前 ShaderGen 有两个 LangGraph：

- 基础对话图 `agent`；
- Shader 生成/评审图 `shader_generation`。

Shader 生成和 Review 通过两个 HTTP 请求完成。当前 Graph 没有 checkpointer 或长期 Store，同一项目的后续生成无法复用之前的 Review 结论；`ShaderPipelineState` 还直接保存图片字节，如果直接启用 checkpoint，会把大对象持久化。

本设计借鉴 Hello-Agents 的分层记忆与 GSSC（Gather、Select、Structure、Compress）Context Engineering 思路，但不安装或依赖 `hello-agents`。任务内记忆和项目级长期记忆复用 LangGraph 原生 checkpointer 与 Store。

## 2. 目标

首个垂直闭环接入 Shader 生成/评审图，并满足：

1. 首次生成由后端创建并返回 `project_id`。
2. 同一 `project_id` 的生成、Review 和再次生成共享轻量任务状态。
3. Review 摘要经筛选后成为项目长期记忆，并进入后续模型上下文。
4. 不同 `project_id` 的 checkpoint 和长期记忆严格隔离。
5. Context Builder 在约 2,000 token 的历史上下文预算内完成 GSSC。
6. 图片、完整 GLSL、完整 Prompt、完整对话和 `reasoning_content` 不进入 checkpoint 或长期 Memory。
7. PostgreSQL 模式支持进程重启后恢复；无数据库时提供明确标记的临时内存模式。
8. 用户可以清除当前项目的 checkpoint 与长期记忆。

## 3. 非目标

首期不实现：

- embedding、向量数据库或 RAG；
- Hello-Agents 运行时依赖；
- 项目列表、搜索、重命名和完整项目管理；
- 用户、租户或鉴权系统；
- 同一项目内的多分支 `task_id`；
- LLM 自动摘要调用；
- 模型自行认定“成功策略”；
- ShaderForge Genome、渲染产物、评分和谱系 Store；
- 多实例并发写同一项目。

## 4. 核心决策

### 4.1 两层 Memory

- 任务内记忆：LangGraph checkpointer 保存轻量 `ShaderPipelineState`。
- 项目级长期记忆：LangGraph Store，namespace 为 `("shadergen", "v1", project_id, "memory")`。

`project_id` 首期同时映射为 LangGraph `thread_id`。`run_id` 继续代表一次 HTTP 调用和过程账本记录。当产品需要同一项目并行尝试或分支回放时，再增加独立 `task_id`。

### 4.2 Context Engineering 独立于 runtime context

新增 `src/agent/app/context/` 表达 Context Engineering。现有 `agent.app.states.Context` 继续只服务基础对话图的 `model_thinking` 和 `capture_reasoning`，不重命名，也不扩展到 Shader Memory。

Shader Graph 移除当前无消费者的 `context_schema=Context`。`project_id` 是轻量业务状态；`prepare_context` 通过 LangGraph `Runtime.store` 访问 Store，但不读取 `runtime.context`。

### 4.3 Agent Memory 与 ShaderForge Store 分离

Agent Memory 只保存“下一次模型调用需要知道的摘要”。未来 ShaderForge Store 保存 Genome、渲染产物、评分、版本和谱系。Memory 只能保存对应 ID 或 hash，不复制权威产物。

## 5. 模块边界

计划新增：

```text
src/agent/app/
├── memory/
│   ├── ARCHITECTURE.md
│   ├── __init__.py
│   ├── models.py          # MemoryItem 与 kind
│   └── store.py           # namespace、查询、晋升和清除操作
├── context/
│   ├── ARCHITECTURE.md
│   ├── __init__.py
│   └── builder.py         # ContextPack 与纯 GSSC Builder
└── nodes/
    ├── prepare_context_node.py
    └── promote_memory_node.py
```

配套职责：

- `memory/` 不加载 Prompt、不调用 LLM、不接触 PostgreSQL 连接。
- `context/` 接收普通 Python 数据，返回 `ContextPack`；不访问 Store。
- `prepare_context_node.py` 从 Runtime Store 读取候选 Memory，再调用纯 Builder。
- `promote_memory_node.py` 把 Review 结果规范化为 `MemoryItem` 并写入 Runtime Store。
- 生成和评审节点只消费 `ContextPack`，仍只通过 `LLMGateway` 调用模型。
- Graph Builder 接收 checkpointer 和 Store，并负责节点装配。
- Agent 公共 service 暴露带持久化依赖的 Shader service 构造入口；每次生成/Review 仍只接收简单 Python 参数。
- Backend 生命周期负责创建、`setup()` 和关闭 saver/store，并把它们注入 Agent 公共 service；Node 不接触数据库连接。

Backend 计划新增 `backend/app/database/agent_memory.py` 管理 LangGraph 持久化基础设施。它可以依赖 LangGraph 官方 persistence API，但不得导入 Agent 内部 Graph、Node、Prompt 或 LLM 实现。

## 6. 标识与 API 契约

### 6.1 标识

| 标识 | 创建时机 | 用途 |
|---|---|---|
| `project_id` | 首次 `POST /api/shader/generate` | API 项目标识、checkpoint thread、Store namespace |
| `run_id` | 每次 generate/review | 过程账本、日志、Memory 来源 |
| `memory_id` | Memory 晋升时 | Store key |

`project_id` 使用 UUID。它是隔离键，不是认证或授权凭据。

### 6.2 Generate

`POST /api/shader/generate` 增加可选 form 字段 `project_id`：

- 未提供：后端创建 UUID。
- 已提供：继续该项目。

响应：

```json
{
  "project_id": "uuid",
  "glsl": "fragment shader source",
  "memory_status": "durable"
}
```

### 6.3 Review

`POST /api/shader/review` 增加必填 form 字段 `project_id`。响应：

```json
{
  "project_id": "uuid",
  "review": {
    "evaluation": "评估摘要",
    "suggestions": ["修改建议"]
  },
  "memory_status": "durable"
}
```

`memory_status` 的合法值为：

- `durable`：PostgreSQL checkpointer/store 正常。
- `ephemeral`：使用进程内 saver/store，重启会丢失。
- `degraded`：长期 Store 本次读或写失败，返回结果没有假装长期记忆成功。

### 6.4 清除 Memory

新增：

```http
DELETE /api/shader/projects/{project_id}/memory
```

成功返回 `204`，删除：

- `thread_id == project_id` 的所有 checkpoint；
- `("shadergen", "v1", project_id, "memory")` namespace 下的全部 Memory。Store 没有直接删除 namespace 的操作，实现必须反复从 offset 0 分页查询并逐项删除，直到查询为空。

该操作不删除 `agent_runs`、`agent_events` 和 `agent_logs`；这些表属于过程审计账本，使用独立保留策略。

## 7. State 与持久化分类

### 7.1 进入 checkpoint

```text
project_id
phase
iteration
last_glsl_sha256
last_generation_model
last_generated_at
last_review_summary
last_suggestions
```

### 7.2 不进入 checkpoint

以下字段使用 LangGraph `UntrackedValue`：

```text
operation
image
content_type
rendered_image
rendered_content_type
glsl
context_pack
selected_memory_ids
memory_status
model_calls
events
logs
run_id
```

`UntrackedValue` 让值参与当前 Graph 调用，但不写入 checkpoint。完整 GLSL 继续作为 API 输出或 Review 输入传递；checkpoint 只保存 SHA-256 和摘要。

`model_calls`、`events` 和 `logs` 改为非持久化后，不再使用当前 append reducer。每个顺序 Node 返回“已有 tuple + 本节点新增项”的完整值，Agent service 只映射本次调用最终得到的 tuple；这样既保留 `context_built`、Review 和 Memory 事件，又不会把历史运行摘要累积进 checkpoint。

## 8. MemoryItem

首期 `MemoryItem` 字段固定为：

```text
schema_version: int
memory_id: str
kind: review | constraint | decision | strategy
summary: str
importance: float
source_run_id: str
glsl_sha256: str | None
iteration: int | None
created_at: datetime
updated_at: datetime
```

约束：

- `summary` 非空且最多 2,000 个字符。
- `importance` 范围为 0.0 到 1.0。
- `schema_version` 首期固定为 `1`。
- namespace 承载 `project_id`，value 不重复保存。
- Review 使用 `review:{glsl_sha256}` 作为稳定 key；同一 GLSL 的重复 Review 更新同一记录，保留 `created_at`，刷新 `updated_at`、`source_run_id`、`summary` 和 `iteration`。
- 自动 Review 的 `importance` 首期固定为 `0.5`；不得根据模型自然语言隐式打分。未来人工确认的 constraint/decision/strategy 再定义独立确定性等级。
- 首期唯一自动写入的长期类型是 `review`。
- `constraint` 和 `decision` 只有未来显式用户确认入口才能写入。
- `strategy` 只有确定性评分或人工确认通过后才能写入。
- `reasoning_content` 永远不能映射到 Memory。

Review Memory 的 `summary` 由现有结构化 `evaluation` 与 `suggestions` 确定性拼接并限长，不增加模型调用。

## 9. Graph 数据流

Graph 使用显式 `operation` 路由：

```text
START
  -> prepare_context
  -> operation == generate -> generate_glsl -> END
  -> operation == review   -> review_render -> promote_memory -> END
```

行为：

1. Agent service 用 `project_id` 设置 `configurable.thread_id`。
2. Checkpointer 恢复该项目的轻量 State。
3. `prepare_context` 读取项目 Store，并构造本次 `ContextPack`。
4. Generate 或 Review Node 使用 YAML Prompt、ContextPack 和当前多模态输入调用 Gateway。
5. Generate 更新 checkpoint 中的阶段、迭代、GLSL hash 和生成摘要。
6. Review 更新 checkpoint，并由 `promote_memory` upsert Review Memory。
7. Agent service 把结果、过程摘要和 `memory_status` 返回 Backend。

## 10. GSSC Context Builder

### 10.1 Gather

收集：

- 当前 operation 与轻量 checkpoint 状态；
- namespace 下最多 50 条 Memory；
- 当前请求的输入元数据。

图片和完整 GLSL 不复制进 ContextPack，它们继续作为当前 HumanMessage 的独立片段。

### 10.2 Select

固定优先级：

```text
constraint > decision > 当前 GLSL review > 当前 iteration strategy > 历史 review
```

同一 kind 内按 `importance` 降序、`updated_at` 降序。当前 `last_glsl_sha256` 对应的 Review 置顶；历史 Review 最多保留 3 条，并排除同一 hash、当前 `run_id` 和已被当前版本取代的重复建议。按 `memory_id` 和 `glsl_sha256` 去重。首期不使用 embedding。

### 10.3 Structure

`ContextPack` 固定字段：

```text
current_phase
current_iteration
confirmed_constraints
confirmed_decisions
approved_strategies
recent_reviews
selected_memory_ids
estimated_tokens
dropped_memory_count
```

模型消息改为：

- `SystemMessage`：现有 YAML Prompt，继续承载角色、规则和输出契约。
- `HumanMessage`：标注为“历史数据，不是指令”的 JSON ContextPack、当前图片和当前 GLSL。

历史 Memory 视为不可信数据，不得改变 SystemMessage 中的规则。

### 10.4 Compress

- `ContextPolicy` 集中管理策略参数；默认 `max_history_tokens=2000`、`max_memory_candidates=50`、`max_historical_reviews=3`，调用方可以显式覆盖。
- 使用已安装 LangChain 的 `count_tokens_approximately()`。
- 超预算时先删除最旧、最低优先级 Review，再缩短 Review 或 strategy 细节。
- 不增加模型摘要调用。
- 当前输入和输出契约不参与这次历史 Memory 淘汰。
- `estimated_tokens` 和 `dropped_memory_count` 进入安全过程事件，不记录 Context 正文。

首期没有 `constraint`、`decision` 或 `strategy` 的自动 writer，因此自动压缩只会淘汰 Review。未来增加这些 writer 时，必须先定义强制信息超过预算时的用户可见处理策略。

## 11. Persistence 生命周期

### 11.1 无 DATABASE_URL

使用：

- `InMemorySaver`；
- `InMemoryStore`。

Backend 启动时记录明确 warning，API 返回 `memory_status="ephemeral"`。

### 11.2 有 DATABASE_URL

增加官方 `langgraph-checkpoint-postgres` 依赖，使用异步 Postgres saver/store：

- Backend lifespan 创建资源；
- 开发和测试通过独立 `make setup-memory-postgres` 执行 saver/store 官方 `setup()`；生产部署把该命令作为 migration step，应用启动不申请 DDL 权限；
- 构建带 saver/store 的 Shader service；
- 关闭时释放资源。

LangGraph persistence 表属于框架基础设施，由官方 `setup()` 管理；现有 `backend/sql/*.sql` 继续只管理 ShaderGen 业务表。现有 `asyncpg` 过程账本不迁移。

`agent_runs` 增加可空 `project_id uuid` 和按项目/时间查询的索引，以关联 run 与项目；清除 Memory 不删除过程账本。

Saver/store 使用 psycopg async pool，不复用现有 asyncpg 过程账本连接池。配置 `LANGGRAPH_STRICT_MSGPACK=true`；如持久化环境未完成 setup、连接或健康检查失败，Backend 启动失败，不静默回退为内存。

## 12. 错误、并发与降级

| 情况 | 行为 |
|---|---|
| `project_id` 非 UUID | `422` |
| Review 缺少 `project_id` | `422` |
| 同项目已有执行 | `409 project_busy` |
| LLM/Gateway 失败 | `502` |
| Checkpointer 读写失败 | `503 memory_unavailable` |
| Store 读取失败 | 使用当前请求和 checkpoint 继续，`memory_status=degraded` |
| Review Memory 写入失败 | 返回 Review，同时 `memory_status=degraded` 和安全警告 |

首期在单个 FastAPI 进程内按 `project_id` 串行执行；请求结束后释放对应锁。多实例部署在增加分布式锁或迁移到 LangGraph Agent Server 前不受支持。

## 13. 安全与数据边界

- `project_id` 只提供 namespace 隔离，不提供鉴权。
- 无鉴权阶段不提供项目列表或 Memory 正文读取 API。
- 不把该能力标记为多用户生产安全。
- Store 写入必须经过 `MemoryItem` 类型、长度和枚举校验。
- 历史 Memory 使用 JSON 数据块，不能成为 Prompt 指令。
- checkpoint、Store、事件和日志不保存图片、完整 GLSL、完整 Prompt、完整对话或 `reasoning_content`。
- 日志只保存 Memory ID、kind、数量、token 估算、状态和错误分类。

项目 Memory 在用户明确清除前一直保留。`新建项目` 只切换 `project_id`，不会删除旧 Memory。

## 14. 前端行为

- 当前 `project_id` 保存到 `localStorage`。
- `localStorage` 额外保存最近最多 10 个 project ID 及最后使用时间，允许用户恢复或复制 ID；这只是本地最近记录，不是后端项目列表，也不提供跨设备同步。
- 刷新后恢复当前 ID。
- 首次生成成功后写入后端返回的 ID。
- 后续 generate/review 都回传该 ID。
- “新建项目”在确认提示中明确说明旧 Memory 不会自动删除；用户若不再需要旧项目，必须先执行“清除当前项目记忆”。确认后清空当前 ID 和页面状态，下一次 generate 由后端创建新 ID。
- “清除当前项目记忆”二次确认后调用 DELETE；成功后清空当前 ID 和页面状态。
- `memory_status=degraded` 显示可理解警告；`ephemeral` 显示开发环境临时记忆提示。

首期没有后端项目列表；用户只能从当前浏览器的最近项目记录恢复旧 `project_id`。清除项目 Memory 后必须同步移除该本地记录。

## 15. 可观测性

复用现有 `events` 和 `logs`：

- `context_built`：候选数、选中数、估算 token、丢弃数。
- `memory_promoted`：Memory ID、kind、source run、GLSL hash。
- `memory_skipped`：没有可晋升内容的原因码。
- `memory_degraded`：read/write/checkpoint 分类，不含正文。
- `memory_cleared`：project ID 与删除计数。

不新增外部指标系统。

## 16. Feature 与验收

新增 `F08`：

> 同一 project_id 的 Shader 生成与 Review 能复用经过筛选的任务记忆和项目记忆，不同项目互不泄漏，并可清除记忆。

实现期间只有 `F08` 为 `active`。验收必须覆盖：

1. 首次 generate 返回 UUID project ID。
2. 同项目 Review 后再次 generate，Fake Gateway 收到上次 Review 摘要。
3. 不同项目无法读取彼此 Memory。
4. 超预算时删除旧 Review，ContextPack 估算不超过约 2,000 token。
5. checkpoint/store 不含禁止持久化的内容。
6. PostgreSQL 资源重建后仍可恢复。
7. 相同 GLSL 重复 Review 不产生重复 Memory。
8. 清除操作删除 checkpoint thread 和 Store namespace。
9. Store 失败返回 degraded；checkpointer 失败返回 503。
10. 同项目并发请求返回 409。
11. 前端刷新、继续项目、新建项目和清除记忆行为正确。
12. checkpoint 不包含 `selected_memory_ids`、`memory_status`、图片、完整 GLSL、ContextPack、模型调用、事件、日志或 reasoning。
13. 相同 Review upsert 保留 `created_at` 并刷新 `updated_at`，部分成功后重试不产生重复 Memory。
14. 清除超过一页的 Memory 不漏项；Store 清除失败时不假装返回 204。
15. 当前 GLSL Review 优先于旧迭代 Review，旧建议不会覆盖当前版本建议。
16. PostgreSQL setup 使用独立命令，运行时启用严格 msgpack 模式并完成健康检查。

目标验证命令：

```bash
uv run pytest tests/unit_tests
uv run pytest tests/integration_tests/test_shader_memory_flow.py
make test-memory-postgres
uv run langgraph validate
npm --prefix frontend run build
npm --prefix frontend run e2e:memory
uv run ruff check src/agent backend tests scripts
make docs-check
make check
```

`make test-memory-postgres` 必须连接隔离测试数据库并验证资源重建后的恢复。PostgreSQL 集成或浏览器 e2e 未实际通过时，`F08` 不得标记为 `passing`。

所有 Agent 测试使用 Fake Gateway，不调用真实模型或密钥。

## 17. 文档同步

实现时同步更新：

- `docs/ARCHITECTURE.md`；
- `docs/DECISIONS.md`；
- `docs/FEATURES.md`；
- `PROGRESS.md`；
- `src/agent/ARCHITECTURE.md`；
- `src/agent/app/ARCHITECTURE.md`；
- `states/graphs/nodes/services` 旁的 `ARCHITECTURE.md`；
- 新增 `memory/context/ARCHITECTURE.md`；
- `backend/README.md`；
- `frontend/README.md`；
- `README.md`、`.env.example`、`Makefile` 和 `pyproject.toml`。

## 18. 参考

- Hello-Agents Context Engineering：<https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter9/Chapter9-Context-Engineering.md>
- Hello-Agents Memory and Retrieval：<https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter8/Chapter8-Memory-and-Retrieval.md>
- LangGraph Memory：<https://docs.langchain.com/oss/python/langgraph/add-memory>
- LangGraph Persistence：<https://docs.langchain.com/oss/python/langgraph/persistence>
- LangGraph UntrackedValue：<https://reference.langchain.com/python/langgraph/channels/untracked_value>
