import importlib
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.pregel import Pregel
from langgraph.store.memory import InMemoryStore

from agent.app.config import model_config
from agent.app.config.model_config import SHADER_GEN_MODEL_NAME
from agent.app.contracts.llm import LLMCallOptions, LLMResponse
from agent.app.graphs.main_graph import graph
from agent.app.graphs.shader_generation_graph import build_shader_generation_graph
from agent.app.llms import client_factory
from agent.app.nodes.generate_glsl_node import make_generate_glsl_node
from agent.app.nodes.model_node import make_model_node
from agent.app.nodes.review_render_node import make_review_render_node
from agent.app.prompts.prompt_loader import load_prompt_definition
from agent.app.services.shader_generation import (
    ShaderGenerationResult,
    ShaderGenerationService,
    extract_glsl,
    parse_shader_review_response,
    review_shader_render,
)
from backend.app.api.routes import shader as shader_route
from backend.app.main import app
from backend.app.services import shader as shader_service


def model_family_module(name: str):
    return importlib.import_module(f"agent.app.llms.families.{name}")


def test_placeholder() -> None:
    # TODO: 后续在这里补充图和业务逻辑的单元测试。
    assert isinstance(graph, Pregel)


def test_llm_client_factory_configured() -> None:
    assert SHADER_GEN_MODEL_NAME == "dashscope:qwen3.7-plus"
    assert callable(client_factory.create_chat_model)


def test_qwen_thinking_env_config_parses_flags(monkeypatch) -> None:
    monkeypatch.setenv("SHADER_GEN_QWEN_ENABLE_THINKING", "false")
    monkeypatch.setenv("SHADER_GEN_QWEN_OUTPUT_THINKING", "true")

    assert model_config.optional_bool_env("SHADER_GEN_QWEN_ENABLE_THINKING") is False
    assert model_config.bool_env("SHADER_GEN_QWEN_OUTPUT_THINKING") is True


def test_provider_model_factories_are_split_from_llm_factory() -> None:
    qwen_model = model_family_module("qwen")
    glm_model = model_family_module("glm")
    deepseek_model = model_family_module("deepseek")
    openai_model = model_family_module("openai")

    assert callable(qwen_model.get_qwen_model)
    assert callable(glm_model.get_glm_model)
    assert callable(deepseek_model.get_deepseek_model)
    assert callable(openai_model.get_openai_model)
    assert callable(client_factory.create_chat_model)
    assert not hasattr(client_factory, "get_model")
    assert not hasattr(client_factory, "get_qwen_model")
    assert not hasattr(client_factory, "QwenChatOpenAI")


def test_get_qwen_model_passes_dashscope_thinking_config(monkeypatch) -> None:
    qwen_model = model_family_module("qwen")

    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-key")
    monkeypatch.setattr(qwen_model, "SHADER_GEN_QWEN_ENABLE_THINKING", False)
    monkeypatch.setattr(qwen_model, "SHADER_GEN_QWEN_OUTPUT_THINKING", True)

    model = qwen_model.get_qwen_model("qwen3.7-plus", provider="dashscope")

    assert model.model_name == "qwen3.7-plus"
    assert model.openai_api_base == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert model.extra_body == {"enable_thinking": False}
    assert model.output_thinking is True


def test_get_qwen_model_accepts_node_level_thinking_options(monkeypatch) -> None:
    qwen_model = model_family_module("qwen")

    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-key")
    monkeypatch.setattr(qwen_model, "SHADER_GEN_QWEN_ENABLE_THINKING", None)
    monkeypatch.setattr(qwen_model, "SHADER_GEN_QWEN_OUTPUT_THINKING", False)

    model = qwen_model.get_qwen_model(
        "qwen3.7-plus",
        provider="dashscope",
        thinking="off",
        capture_reasoning=True,
    )

    assert model.extra_body == {"enable_thinking": False}
    assert model.output_thinking is True


def test_qwen_model_drops_reasoning_content_by_default() -> None:
    qwen_model = model_family_module("qwen")

    model = qwen_model.QwenChatOpenAI(
        model="qwen3.7-plus",
        api_key="fake-key",
        base_url="https://example.test/compatible-mode/v1",
    )

    result = model._create_chat_result(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "最终回复",
                        "reasoning_content": "内部推理",
                    },
                    "finish_reason": "stop",
                }
            ],
            "model": "qwen3.7-plus",
        }
    )

    message = result.generations[0].message
    assert message.content == "最终回复"
    assert "reasoning_content" not in message.additional_kwargs


def test_qwen_model_preserves_reasoning_content_when_enabled() -> None:
    qwen_model = model_family_module("qwen")

    model = qwen_model.QwenChatOpenAI(
        model="qwen3.7-plus",
        api_key="fake-key",
        base_url="https://example.test/compatible-mode/v1",
        output_thinking=True,
    )

    result = model._create_chat_result(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "最终回复",
                        "reasoning_content": "内部推理",
                    },
                    "finish_reason": "stop",
                }
            ],
            "model": "qwen3.7-plus",
        }
    )

    message = result.generations[0].message
    assert message.content == "最终回复"
    assert message.additional_kwargs["reasoning_content"] == "内部推理"


def test_openai_compatible_provider_factories_use_default_provider_env(
    monkeypatch,
) -> None:
    glm_model = model_family_module("glm")
    deepseek_model = model_family_module("deepseek")
    openai_model = model_family_module("openai")

    monkeypatch.setenv("GLM_API_KEY", "glm-key")
    monkeypatch.setenv("GLM_BASE_URL", "https://glm.example.test/v1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://deepseek.example.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai.example.test/v1")

    glm = glm_model.get_glm_model("glm-4.5", temperature=0.2)
    deepseek = deepseek_model.get_deepseek_model(
        "deepseek-chat",
        temperature=0.3,
    )
    openai = openai_model.get_openai_model("gpt-4.1", temperature=0.4)

    assert glm.model_name == "glm-4.5"
    assert glm.openai_api_base == "https://glm.example.test/v1"
    assert glm.temperature == 0.2
    assert deepseek.model_name == "deepseek-chat"
    assert deepseek.openai_api_base == "https://deepseek.example.test/v1"
    assert deepseek.temperature == 0.3
    assert openai.model_name == "gpt-4.1"
    assert openai.openai_api_base == "https://openai.example.test/v1"
    assert openai.temperature == 0.4


def test_model_factories_can_use_dashscope_provider_credentials(monkeypatch) -> None:
    qwen_model = model_family_module("qwen")
    glm_model = model_family_module("glm")

    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://dashscope.example.test/v1")

    qwen = qwen_model.get_qwen_model("qwen3.7-plus", provider="dashscope")
    glm = glm_model.get_glm_model("glm-4.5", provider="dashscope")

    assert qwen.model_name == "qwen3.7-plus"
    assert qwen.openai_api_base == "https://dashscope.example.test/v1"
    assert glm.model_name == "glm-4.5"
    assert glm.openai_api_base == "https://dashscope.example.test/v1"


def test_llm_factory_routes_by_provider_and_model_family(monkeypatch) -> None:
    qwen_model = model_family_module("qwen")
    glm_model = model_family_module("glm")
    deepseek_model = model_family_module("deepseek")
    openai_model = model_family_module("openai")

    monkeypatch.setattr(
        qwen_model,
        "get_qwen_model",
        lambda model,
        provider=None,
        temperature=0,
        thinking="default",
        capture_reasoning=None: (
            "qwen",
            provider,
            model,
            temperature,
        ),
    )
    monkeypatch.setattr(
        glm_model,
        "get_glm_model",
        lambda model, provider=None, temperature=0: (
            "glm",
            provider,
            model,
            temperature,
        ),
    )
    monkeypatch.setattr(
        deepseek_model,
        "get_deepseek_model",
        lambda model, provider=None, temperature=0: (
            "deepseek",
            provider,
            model,
            temperature,
        ),
    )
    monkeypatch.setattr(
        openai_model,
        "get_openai_model",
        lambda model, provider=None, temperature=0: (
            "openai",
            provider,
            model,
            temperature,
        ),
    )

    assert client_factory.create_chat_model(
        LLMCallOptions(model_ref="dashscope:qwen3.7-plus", temperature=0.1)
    ) == (
        "qwen",
        "dashscope",
        "qwen3.7-plus",
        0.1,
    )
    assert client_factory.create_chat_model(
        LLMCallOptions(model_ref="dashscope:glm-4.5", temperature=0.2)
    ) == (
        "glm",
        "dashscope",
        "glm-4.5",
        0.2,
    )
    assert client_factory.create_chat_model(
        LLMCallOptions(model_ref="deepseek:deepseek-chat", temperature=0.3)
    ) == (
        "deepseek",
        "deepseek",
        "deepseek-chat",
        0.3,
    )
    assert client_factory.create_chat_model(
        LLMCallOptions(model_ref="openai:gpt-4.1", temperature=0.4)
    ) == (
        "openai",
        "openai",
        "gpt-4.1",
        0.4,
    )


def test_llm_factory_passes_node_level_options_to_qwen(monkeypatch) -> None:
    qwen_model = model_family_module("qwen")

    monkeypatch.setattr(
        qwen_model,
        "get_qwen_model",
        lambda model,
        provider=None,
        temperature=0,
        thinking="default",
        capture_reasoning=None: (
            provider,
            model,
            temperature,
            thinking,
            capture_reasoning,
        ),
    )

    assert client_factory.create_chat_model(
        LLMCallOptions(
            model_ref="dashscope:qwen3.7-plus",
            temperature=0.1,
            thinking="on",
            capture_reasoning=True,
        )
    ) == (
        "dashscope",
        "qwen3.7-plus",
        0.1,
        "on",
        True,
    )


def test_agent_owns_shader_prompt() -> None:
    definition = load_prompt_definition("image_to_glsl")

    assert "fragment shader" in definition.prompt
    assert definition.version == "image_to_glsl_no_texture_v1"
    assert "禁止使用 texture2D" in definition.prompt
    assert "必须使用 texture2D" not in definition.prompt


def test_extract_glsl_from_markdown() -> None:
    output = """```glsl
void main() {
  gl_FragColor = vec4(1.0);
}
```"""
    assert extract_glsl(output).startswith("void main()")


def test_shader_nodes_are_split_by_file() -> None:
    assert make_generate_glsl_node.__module__ == "agent.app.nodes.generate_glsl_node"
    assert make_review_render_node.__module__ == "agent.app.nodes.review_render_node"


def test_parse_shader_review_response() -> None:
    review = parse_shader_review_response(
        '{"evaluation":"边缘偏软，辉光偏强。","suggestions":["降低 blur 半径","提高边缘对比度"]}'
    )

    assert review.evaluation == "边缘偏软，辉光偏强。"
    assert review.suggestions == ("降低 blur 半径", "提高边缘对比度")


@pytest.mark.anyio
async def test_review_shader_render_uses_original_rendered_images_and_glsl() -> None:
    class FakeGateway:
        async def ainvoke(self, messages, options):
            content = messages[0].content
            text_parts = [part["text"] for part in content if part["type"] == "text"]
            image_urls = [
                part["image_url"]["url"]
                for part in content
                if part["type"] == "image_url"
            ]
            assert "当前 GLSL 代码" in "\n".join(text_parts)
            assert "void main() {}" in "\n".join(text_parts)
            assert image_urls == [
                "data:image/png;base64,b3JpZ2luYWw=",
                "data:image/png;base64,cmVuZGVyZWQ=",
            ]
            text = '{"evaluation":"渲染图颜色偏暗。","suggestions":["提高整体亮度"]}'
            message = AIMessage(content=text)
            return LLMResponse(
                message=message,
                text=text,
                reasoning_content=None,
                model_ref=options.model_ref,
                latency_ms=5,
            )

    checkpointer = InMemorySaver()
    store = InMemoryStore()
    service = ShaderGenerationService(
        build_shader_generation_graph(
            FakeGateway(),
            checkpointer=checkpointer,
            store=store,
        ),
        checkpointer,
        store,
        "ephemeral",
    )

    result = await review_shader_render(
        original_image=b"original",
        original_content_type="image/png",
        rendered_image=b"rendered",
        rendered_content_type="image/png",
        glsl="void main() {}",
        project_id=str(uuid4()),
        run_id=str(uuid4()),
        service=service,
    )

    assert result.evaluation == "渲染图颜色偏暗。"
    assert result.suggestions == ("提高整体亮度",)
    assert result.model_calls[0]["prompt_version"] == "shader_review"


@pytest.mark.anyio
async def test_shader_generation_graph_runs_glsl_then_review_nodes() -> None:
    calls = []

    class FakeGateway:
        async def ainvoke(self, messages, options):
            calls.append(messages[0].content)
            if len(calls) == 1:
                text = "precision mediump float;\nvoid main() {}"
            else:
                text = '{"evaluation":"渲染图接近原图。","suggestions":["微调饱和度"]}'
            message = AIMessage(content=text)
            return LLMResponse(
                message=message,
                text=text,
                reasoning_content=None,
                model_ref=options.model_ref,
                latency_ms=5,
            )

    graph = build_shader_generation_graph(FakeGateway())
    generated = await graph.ainvoke(
        {
            "operation": "generate",
            "project_id": str(uuid4()),
            "image": b"original",
            "content_type": "image/png",
        }
    )
    result = await graph.ainvoke(
        {
            "operation": "review",
            "project_id": str(uuid4()),
            "image": b"original",
            "content_type": "image/png",
            "rendered_image": b"rendered",
            "rendered_content_type": "image/png",
            "glsl": generated["glsl"],
            "last_glsl_sha256": "0" * 64,
            "run_id": str(uuid4()),
        }
    )

    assert len(calls) == 2
    assert generated["glsl"].startswith("precision mediump float;")
    assert result["evaluation"] == "渲染图接近原图。"
    assert result["suggestions"] == ("微调饱和度",)
    assert [call["prompt_version"] for call in generated["model_calls"]] == [
        "image_to_glsl_no_texture_v1"
    ]
    assert [call["prompt_version"] for call in result["model_calls"]] == [
        "shader_review"
    ]


@pytest.mark.anyio
async def test_backend_shader_service_delegates_to_agent_public_interface(monkeypatch) -> None:
    class FakeResult:
        glsl = "void main() {}"
        glsl_model_name = "agent-glsl"
        vision_model_name = "agent-vision"

    async def fake_agent_generate(
        image: bytes,
        content_type: str,
        **kwargs,
    ) -> FakeResult:
        assert image == b"image-bytes"
        assert content_type == "image/png"
        assert kwargs["project_id"] == "project-test"
        assert kwargs["run_id"] == "run-test"
        return FakeResult()

    monkeypatch.setattr(
        shader_service.shader_generation,
        "generate_glsl_from_image",
        fake_agent_generate,
    )

    result = await shader_service.generate_shader_from_image(
        b"image-bytes",
        "image/png",
        project_id="project-test",
        run_id="run-test",
        service=object(),  # type: ignore[arg-type]
    )

    assert result.glsl == "void main() {}"
    assert result.glsl_model_name == "agent-glsl"
    assert result.vision_model_name == "agent-vision"


def test_backend_logs_requests(caplog) -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert "request.completed" in caplog.text
    assert "path=/health" in caplog.text


def test_database_health_requires_pool() -> None:
    client = TestClient(app)

    response = client.get("/health/db")

    assert response.status_code == 503
    assert response.json() == {"detail": "数据库连接池未初始化。"}


def test_database_health_uses_pool() -> None:
    class FakeConnection:
        async def fetchval(self, query: str) -> int:
            assert query == "SELECT 1"
            return 1

    class FakeAcquire:
        async def __aenter__(self) -> FakeConnection:
            return FakeConnection()

        async def __aexit__(self, *args) -> None:
            return None

    class FakePool:
        def acquire(self) -> FakeAcquire:
            return FakeAcquire()

    app.state.db_pool = FakePool()
    client = TestClient(app)

    try:
        response = client.get("/health/db")
    finally:
        del app.state.db_pool

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_generate_shader_records_agent_process(monkeypatch, caplog) -> None:
    class FakeConnection:
        def __init__(self) -> None:
            self.executed = []

        async def execute(self, query: str, *args):
            self.executed.append((query, args))
            return "OK"

    class FakeAcquire:
        def __init__(self, connection: FakeConnection) -> None:
            self.connection = connection

        async def __aenter__(self) -> FakeConnection:
            return self.connection

        async def __aexit__(self, *args) -> None:
            return None

    class FakePool:
        def __init__(self) -> None:
            self.connection = FakeConnection()

        def acquire(self) -> FakeAcquire:
            return FakeAcquire(self.connection)

    async def fake_generate_shader_from_image(
        image: bytes,
        content_type: str,
        **kwargs,
    ) -> ShaderGenerationResult:
        assert image == b"image-bytes"
        assert content_type == "image/png"
        return ShaderGenerationResult(
            project_id=kwargs["project_id"],
            glsl="void main() {}",
            glsl_model_name="fake-glsl",
            vision_model_name="fake-vision",
            memory_status="ephemeral",
            model_calls=(
                {
                    "model": "fake-glsl",
                    "prompt_version": "image_to_glsl",
                    "latency_ms": 7,
                },
            ),
            events=(
                {
                    "stage": "agent",
                    "event_type": "prompt_loaded",
                    "payload": {"prompt": "image_to_glsl"},
                },
            ),
            logs=(
                {
                    "level": "debug",
                    "source": "agent.app.services.shader_generation",
                    "message": "Agent 生成摘要",
                    "context": {"output_chars": 14},
                },
            ),
        )

    pool = FakePool()
    app.state.db_pool = pool
    monkeypatch.setattr(
        shader_route,
        "generate_shader_from_image",
        fake_generate_shader_from_image,
    )
    monkeypatch.setattr(
        shader_route,
        "get_shader_generation_models",
        lambda: ("fake-glsl", "fake-vision"),
    )

    try:
        response = TestClient(app).post(
            "/api/shader/generate",
            files={"file": ("test.png", b"image-bytes", "image/png")},
        )
    finally:
        del app.state.db_pool

    assert response.status_code == 200
    assert response.json()["memory_status"] == "ephemeral"
    assert response.json()["project_id"]
    executed_sql = "\n".join(query for query, _ in pool.connection.executed)
    assert "INSERT INTO agent_runs" in executed_sql
    assert "INSERT INTO agent_events" in executed_sql
    assert "INSERT INTO agent_logs" in executed_sql
    assert "UPDATE agent_runs" in executed_sql
    executed_args = [arg for _, args in pool.connection.executed for arg in args]
    serialized_args = "\n".join(str(arg) for arg in executed_args)
    assert "prompt_version" in serialized_args
    assert "prompt_loaded" in serialized_args
    assert "Agent 生成摘要" in serialized_args
    assert "agent.process.database.write.succeeded" in caplog.text
    assert "backend.agent_process" in caplog.text


def test_generate_shader_logs_agent_process_write_failure(monkeypatch, caplog) -> None:
    class FailingConnection:
        async def execute(self, query: str, *args):
            raise RuntimeError("db down")

    class FakeAcquire:
        async def __aenter__(self) -> FailingConnection:
            return FailingConnection()

        async def __aexit__(self, *args) -> None:
            return None

    class FailingPool:
        def acquire(self) -> FakeAcquire:
            return FakeAcquire()

    async def fake_generate_shader_from_image(
        image: bytes,
        content_type: str,
        **kwargs,
    ) -> ShaderGenerationResult:
        raise RuntimeError("model down")

    app.state.db_pool = FailingPool()
    monkeypatch.setattr(
        shader_route,
        "generate_shader_from_image",
        fake_generate_shader_from_image,
    )
    monkeypatch.setattr(
        shader_route,
        "get_shader_generation_models",
        lambda: ("fake-glsl", "fake-vision"),
    )

    try:
        response = TestClient(app, raise_server_exceptions=False).post(
            "/api/shader/generate",
            files={"file": ("test.png", b"image-bytes", "image/png")},
        )
    finally:
        del app.state.db_pool

    assert response.status_code == 502
    assert "agent.process.database.write.failed" in caplog.text
    assert "backend.agent_process" in caplog.text


def test_review_shader_endpoint_delegates_to_agent_public_interface(monkeypatch) -> None:
    project_id = str(uuid4())

    async def fake_review_shader_render(
        original_image: bytes,
        original_content_type: str,
        rendered_image: bytes,
        rendered_content_type: str,
        glsl: str,
        **kwargs,
    ):
        assert original_image == b"original"
        assert original_content_type == "image/png"
        assert rendered_image == b"rendered"
        assert rendered_content_type == "image/png"
        assert glsl == "void main() {}"
        assert kwargs["project_id"] == project_id

        class FakeReview:
            project_id = kwargs["project_id"]
            evaluation = "渲染图和原图接近。"
            suggestions = ("保留当前颜色结构",)
            memory_status = "ephemeral"

        return FakeReview()

    monkeypatch.setattr(
        shader_route,
        "review_shader_render",
        fake_review_shader_render,
    )

    response = TestClient(app).post(
        "/api/shader/review",
        files={
            "original_file": ("original.png", b"original", "image/png"),
            "rendered_file": ("rendered.png", b"rendered", "image/png"),
        },
        data={"glsl": "void main() {}", "project_id": project_id},
    )

    assert response.status_code == 200
    assert response.json() == {
        "project_id": project_id,
        "review": {
            "evaluation": "渲染图和原图接近。",
            "suggestions": ["保留当前颜色结构"],
        },
        "memory_status": "ephemeral",
    }


def test_review_shader_requires_project_id() -> None:
    response = TestClient(app).post(
        "/api/shader/review",
        files={
            "original_file": ("original.png", b"original", "image/png"),
            "rendered_file": ("rendered.png", b"rendered", "image/png"),
        },
        data={"glsl": "void main() {}"},
    )

    assert response.status_code == 422


def test_review_shader_records_agent_process(monkeypatch, caplog) -> None:
    project_id = str(uuid4())
    class FakeConnection:
        def __init__(self) -> None:
            self.executed = []

        async def execute(self, query: str, *args):
            self.executed.append((query, args))
            return "OK"

    class FakeAcquire:
        def __init__(self, connection: FakeConnection) -> None:
            self.connection = connection

        async def __aenter__(self) -> FakeConnection:
            return self.connection

        async def __aexit__(self, *args) -> None:
            return None

    class FakePool:
        def __init__(self) -> None:
            self.connection = FakeConnection()

        def acquire(self) -> FakeAcquire:
            return FakeAcquire(self.connection)

    async def fake_review_shader_render(
        original_image: bytes,
        original_content_type: str,
        rendered_image: bytes,
        rendered_content_type: str,
        glsl: str,
        **kwargs,
    ):
        class FakeReview:
            project_id = kwargs["project_id"]
            evaluation = "渲染图和原图接近。"
            suggestions = ("保留当前颜色结构",)
            review_model_name = "fake-review"
            memory_status = "ephemeral"
            model_calls = (
                {
                    "model": "fake-review",
                    "prompt_version": "shader_review",
                    "latency_ms": 9,
                    "reasoning_content": "评审思维链",
                },
            )
            events = ()
            logs = ()

        return FakeReview()

    pool = FakePool()
    app.state.db_pool = pool
    monkeypatch.setattr(
        shader_route,
        "review_shader_render",
        fake_review_shader_render,
    )

    try:
        response = TestClient(app).post(
            "/api/shader/review",
            files={
                "original_file": ("original.png", b"original", "image/png"),
                "rendered_file": ("rendered.png", b"rendered", "image/png"),
            },
            data={"glsl": "void main() {}", "project_id": project_id},
        )
    finally:
        del app.state.db_pool

    assert response.status_code == 200
    executed_sql = "\n".join(query for query, _ in pool.connection.executed)
    assert "INSERT INTO agent_runs" in executed_sql
    assert "INSERT INTO agent_events" in executed_sql
    assert "reasoning_content" in executed_sql
    assert "UPDATE agent_runs" in executed_sql
    executed_args = [arg for _, args in pool.connection.executed for arg in args]
    serialized_args = "\n".join(str(arg) for arg in executed_args)
    assert "评审思维链" in serialized_args
    assert "agent.process.database.write.succeeded" in caplog.text


@pytest.mark.anyio
async def test_call_model_uses_gateway() -> None:
    input_messages = [HumanMessage(content="你好")]
    output_message = AIMessage(content="你好，我是 ShaderGen。")

    class FakeGateway:
        def __init__(self) -> None:
            self.calls = []

        async def ainvoke(self, messages, options):
            self.calls.append((messages, options))
            return LLMResponse(
                message=output_message,
                text=output_message.text,
                reasoning_content=None,
                model_ref=options.model_ref,
                latency_ms=1,
            )

    gateway = FakeGateway()
    node = make_model_node(gateway)
    result = await node({"messages": input_messages}, None)

    assert result == {"messages": [output_message]}
    messages, options = gateway.calls[0]
    assert messages == input_messages
    assert options.model_ref == SHADER_GEN_MODEL_NAME
    assert options.thinking == "default"
    assert options.capture_reasoning is None


@pytest.mark.anyio
async def test_call_model_uses_gateway_runtime_options() -> None:
    input_messages = [HumanMessage(content="你好")]
    output_message = AIMessage(content="你好，我是 ShaderGen。")

    class FakeRuntime:
        context = {"model_thinking": "off", "capture_reasoning": True}

    class FakeGateway:
        def __init__(self) -> None:
            self.calls = []

        async def ainvoke(self, messages, options):
            self.calls.append((messages, options))
            return LLMResponse(
                message=output_message,
                text=output_message.text,
                reasoning_content=None,
                model_ref=options.model_ref,
                latency_ms=1,
            )

    gateway = FakeGateway()
    node = make_model_node(gateway)
    result = await node({"messages": input_messages}, FakeRuntime())

    assert result == {"messages": [output_message]}
    messages, options = gateway.calls[0]
    assert messages == input_messages
    assert options.model_ref == SHADER_GEN_MODEL_NAME
    assert options.thinking == "off"
    assert options.capture_reasoning is True
