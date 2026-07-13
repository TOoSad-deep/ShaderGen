# Agent App Structure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `src/agent/` 彻底改造成类似独立 agent 项目的规范包结构，同时继续放在当前 monorepo 内运行。

**Architecture:** Agent 内部采用 `agent.app.*` 分层：`graphs` 负责 LangGraph 入口，`nodes` 负责节点，`states` 负责状态，`models` 负责模型工厂，`prompts` 负责 Prompt 文件和加载，`services` 负责对后端暴露的生成用例。后端只调用 `agent.app.services.shader_generation`，不依赖 Agent 内部模型或 Prompt。

**Tech Stack:** Python 3.10+、LangGraph、LangChain、FastAPI、pytest、ruff、importlib.resources。

## Global Constraints

- 不保留 `agent.graph`、`agent.models`、`agent.prompt_loader`、`agent.shader_generation`、`agent.utils.*` 旧导入兼容。
- 不创建空的 `api`、`storage`、`workers`，因为当前 agent 不是独立 HTTP 服务。
- 后端不得 import Agent 模型工厂、Prompt 加载器或 LangChain 消息类型。
- Prompt 仍由 Agent 包持有，放在 `src/agent/app/prompts/*.yaml`。
- 架构、目录边界和契约变化必须同步更新 Markdown。

---

### Task 1: 新结构测试红灯

**Files:**
- Modify: `tests/unit_tests/test_configuration.py`
- Modify: `tests/integration_tests/test_graph.py`

**Interfaces:**
- Consumes: planned `agent.app.graphs.main_graph.graph`
- Consumes: planned `agent.app.models.llm_factory.SHADER_GEN_MODEL_NAME`
- Consumes: planned `agent.app.prompts.prompt_loader.load_prompt`
- Consumes: planned `agent.app.services.shader_generation.ShaderGenerationResult`
- Produces: failing tests proving new structure is required.

- [x] **Step 1: Write the failing test**

Update imports in `tests/unit_tests/test_configuration.py` and `tests/integration_tests/test_graph.py` to use `agent.app.*` paths only.

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit_tests/test_configuration.py::test_placeholder -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.app'`.

### Task 2: 迁移 Agent 包结构

**Files:**
- Create: `src/agent/app/__init__.py`
- Create: `src/agent/app/config/__init__.py`
- Create: `src/agent/app/config/model_config.py`
- Create: `src/agent/app/graphs/__init__.py`
- Create: `src/agent/app/graphs/main_graph.py`
- Create: `src/agent/app/states/__init__.py`
- Create: `src/agent/app/states/agent_state.py`
- Create: `src/agent/app/nodes/__init__.py`
- Create: `src/agent/app/nodes/model_node.py`
- Create: `src/agent/app/prompts/__init__.py`
- Create: `src/agent/app/prompts/prompt_loader.py`
- Move: `src/agent/prompts/image_to_glsl.yaml` -> `src/agent/app/prompts/image_to_glsl.yaml`
- Create: `src/agent/app/models/__init__.py`
- Create: `src/agent/app/models/llm_factory.py`
- Create: `src/agent/app/services/__init__.py`
- Create: `src/agent/app/services/shader_generation.py`
- Create: `src/agent/app/tools/__init__.py`
- Create: `src/agent/app/observability/__init__.py`
- Delete: old flat Agent modules.

**Interfaces:**
- Produces: `graph` in `agent.app.graphs.main_graph`
- Produces: `generate_glsl_from_image(image: bytes, content_type: str) -> ShaderGenerationResult`
- Produces: `shader_generation_models() -> tuple[str, str]`
- Produces: `extract_glsl(text: str) -> str`

- [x] **Step 1: Write minimal implementation**

Move existing code into the new package names without changing runtime behavior.

- [x] **Step 2: Run tests**

Run: `uv run pytest tests/unit_tests/test_configuration.py -q`
Expected: PASS.

### Task 3: 改后端入口和工程配置

**Files:**
- Modify: `backend/app/services/shader.py`
- Modify: `langgraph.json`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `agent.app.services.shader_generation`
- Produces: backend still calls only the Agent service boundary.

- [x] **Step 1: Update backend and config**

Point backend service and LangGraph config to `agent.app.*`; package new subpackages and prompt YAML.

- [x] **Step 2: Run boundary scan**

Run: `rg -n "agent\\.(graph|models|prompt_loader|shader_generation|utils)|langchain_core|shader_gen_model|load_prompt|HumanMessage" backend`
Expected: no output.

### Task 4: 更新文档并全量验证

**Files:**
- Create: `src/agent/README.md`
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/DECISIONS.md`
- Modify: `PROGRESS.md`

**Interfaces:**
- Produces: repo docs describe the new Agent package boundary.

- [x] **Step 1: Update Markdown**

Document `agent.app.*` structure, no compatibility promise, and backend boundary.

- [x] **Step 2: Run verification**

Run:

```bash
uv run pytest tests/unit_tests
uv run pytest tests/integration_tests
uv run ruff check src/agent/app backend/app/services/shader.py tests/unit_tests/test_configuration.py tests/integration_tests/test_graph.py
uv run langgraph validate
```

Expected: all commands exit 0. `langgraph validate` may keep the existing `$schema` warning.
