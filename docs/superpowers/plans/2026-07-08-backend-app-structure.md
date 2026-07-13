# Backend App Structure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将后端重构为 `api/routes`、`database/session`、`middleware`、`core` 的规范结构，并保留独立 SQL 记录目录。

**Architecture:** FastAPI 应用入口仍是 `backend.app.main:app`。路由统一由 `backend.app.api.router.api_router` 聚合，健康检查和 Shader 生成分别放进 `backend.app.api.routes.health` 与 `backend.app.api.routes.shader`。数据库连接代码迁移到 `backend.app.database.session`，手写 SQL schema 移到 `backend/sql/`。

**Tech Stack:** Python 3.10+、FastAPI、asyncpg、pytest、ruff、现有根目录 `pyproject.toml`。

## Global Constraints

- 暂不创建 `auth`、`user`、`file` 空包；等有真实功能再加。
- 暂不拆 `backend/pyproject.toml`；继续使用根目录 `pyproject.toml` 管理 monorepo。
- 不改变外部 API 契约：`GET /health`、`GET /health/db`、`POST /api/shader/generate` 保持不变。
- SQL 记录目录使用 `backend/sql/`，数据库初始化按文件名顺序执行其中的 `*.sql`。
- 后端仍只能通过 `agent.app.services.*` 调用 Agent。

---

### Task 1: 新后端结构测试红灯

**Files:**
- Modify: `tests/unit_tests/test_configuration.py`
- Modify: `tests/unit_tests/test_agent_process_store.py`

**Interfaces:**
- Consumes: planned `backend.app.api.routes.shader`
- Consumes: planned `backend.app.database.session.initialize_database_schema`
- Produces: failing tests proving new structure is required.

- [x] **Step 1: Write the failing test**

Update tests to import `shader_route` from `backend.app.api.routes.shader` and `initialize_database_schema` from `backend.app.database.session`.

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit_tests/test_agent_process_store.py::test_initialize_database_schema_executes_sql_files -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.app.database.session'`.

### Task 2: 迁移后端代码结构

**Files:**
- Create: `backend/app/api/__init__.py`
- Create: `backend/app/api/router.py`
- Create: `backend/app/api/routes/__init__.py`
- Create: `backend/app/api/routes/health.py`
- Create: `backend/app/api/routes/shader.py`
- Create: `backend/app/core/__init__.py`
- Create: `backend/app/core/logging.py`
- Create: `backend/app/database/__init__.py`
- Create: `backend/app/database/session.py`
- Create: `backend/app/middleware/__init__.py`
- Create: `backend/app/middleware/request_logging.py`
- Create: `backend/app/schemas/shader.py`
- Create: `backend/sql/001_agent_process.sql`
- Create: `backend/sql/README.md`
- Modify: `backend/app/main.py`
- Modify: `pyproject.toml`
- Delete: `backend/app/database.py`
- Delete: `backend/app/logging_config.py`
- Delete: `backend/app/routes/__init__.py`
- Delete: `backend/app/routes/shader.py`
- Delete: `backend/db/001_agent_process.sql`

**Interfaces:**
- Produces: `api_router`
- Produces: `build_request_logging_middleware(logger)`
- Produces: `open_database_pool(app)`, `close_database_pool(app)`, `initialize_database_schema(pool)`, `ping_database(pool)`
- Produces: `ShaderResponse`

- [x] **Step 1: Move implementation**

Move existing behavior into the new modules without changing API paths or response schemas.

- [x] **Step 2: Run tests**

Run: `uv run pytest tests/unit_tests -q`
Expected: PASS.

### Task 3: 更新文档和边界扫描

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `backend/README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/DECISIONS.md`
- Modify: `PROGRESS.md`

**Interfaces:**
- Produces: docs matching the new backend structure and SQL directory.

- [x] **Step 1: Update Markdown**

Document `backend/app/api`, `backend/app/database`, `backend/app/middleware`, `backend/app/core`, and `backend/sql`.

- [x] **Step 2: Run final verification**

Run:

```bash
uv run pytest tests/unit_tests
uv run pytest tests/integration_tests
uv run ruff check backend tests/unit_tests/test_configuration.py tests/unit_tests/test_agent_process_store.py
uv run langgraph validate
rg -n "backend\\.app\\.(database|logging_config|routes)|backend/app/(database\\.py|logging_config\\.py|routes)|backend/db" backend tests AGENTS.md README.md docs pyproject.toml
```

Expected: tests and ruff pass, LangGraph validate exits 0 with the existing `$schema` warning, and the boundary scan has no stale code/docs references except historical plans.
