# Agent Public Interface Implementation Plan

> 历史计划：该方案已被 `docs/superpowers/plans/2026-07-08-agent-app-structure.md` 取代。当前 Agent 公共入口是 `agent.app.services.shader_generation`。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 后端不再直接依赖 Agent 内部模型、Prompt 或 LangChain 消息组装，只通过 Agent 端公共接口执行图片到 GLSL 生成。

**Architecture:** `src/agent/shader_generation.py` 暴露公共接口和结果类型。`backend/app/services/shader.py` 只做薄编排代理，`backend/app/routes/shader.py` 只调用后端 service 并写过程账本。

**Tech Stack:** Python、FastAPI、LangChain、pytest、现有 `agent_runs`/`agent_events` 过程账本。

## Global Constraints

- 不新增依赖。
- 不引入注册中心、接口基类或多实现工厂。
- 后端不得 import `agent.models`、`agent.prompt_loader` 或 `langchain_core`。
- 文档和注释使用中文，保留必要英文技术名词。
- 修改后运行 `uv run pytest tests/unit_tests`。

---

### Task 1: Agent 公共接口

**Files:**
- Create: `src/agent/shader_generation.py`
- Modify: `backend/app/services/shader.py`
- Modify: `backend/app/routes/shader.py`
- Modify: `tests/unit_tests/test_configuration.py`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `backend/README.md`
- Modify: `docs/DECISIONS.md`
- Modify: `PROGRESS.md`

**Interfaces:**
- Produces: `ShaderGenerationResult(glsl: str, glsl_model_name: str, vision_model_name: str)`
- Produces: `generate_glsl_from_image(image: bytes, content_type: str) -> ShaderGenerationResult`
- Produces: `shader_generation_models() -> tuple[str, str]`
- Consumes: existing `agent.models.shader_gen_model`, `agent.models.SHADER_GEN_MODEL_NAME`, `agent.prompt_loader.load_prompt`

- [x] **Step 1: Write the failing test**

```python
async def test_backend_shader_service_delegates_to_agent_public_interface(monkeypatch) -> None:
    async def fake_agent_generate(image: bytes, content_type: str) -> ShaderGenerationResult:
        assert image == b"image-bytes"
        assert content_type == "image/png"
        return ShaderGenerationResult(
            glsl="void main() {}",
            glsl_model_name="agent-glsl",
            vision_model_name="agent-vision",
        )

    monkeypatch.setattr(shader_service, "run_agent_shader_generation", fake_agent_generate)

    result = await shader_service.generate_shader_from_image(b"image-bytes", "image/png")

    assert result.glsl == "void main() {}"
    assert result.glsl_model_name == "agent-glsl"
    assert result.vision_model_name == "agent-vision"
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit_tests/test_configuration.py::test_backend_shader_service_delegates_to_agent_public_interface -q`

Expected: FAIL because `ShaderGenerationResult` or `generate_shader_from_image` is not available yet.

- [x] **Step 3: Write minimal implementation**

Create `src/agent/shader_generation.py` with the dataclass, model metadata helper, GLSL extraction, and existing image-to-model call. Replace backend service internals with a wrapper around the public Agent function. Update route to use the result metadata instead of importing Agent model names.

- [x] **Step 4: Run tests**

Run: `uv run pytest tests/unit_tests`

Expected: PASS.

- [x] **Step 5: Update docs**

Record the boundary rule in architecture docs, backend README, decision log, and progress.
