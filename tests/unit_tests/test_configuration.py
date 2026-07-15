import importlib

from fastapi.testclient import TestClient
from langgraph.pregel import Pregel

from agent.app.config import model_config
from agent.app.config.model_config import SHADER_GEN_MODEL_NAME
from agent.app.contracts.llm import LLMCallOptions
from agent.app.graphs.png_to_shader_v1_graph import png_to_shader_v1_graph
from agent.app.llms import client_factory
from backend.app.database import agent_memory
from backend.app.main import app


def model_family_module(name: str):
    return importlib.import_module(f"agent.app.llms.families.{name}")


def test_v1_graph_is_compiled() -> None:
    assert isinstance(png_to_shader_v1_graph, Pregel)


def test_request_validation_failure_logs_safe_field_diagnostics(caplog) -> None:
    response = TestClient(app).post(
        "/api/shader/generate",
        data={
            "generation_mode": "unsupported-mode",
            "instruction": "PRIVATE_USER_TEXT",
        },
    )

    assert response.status_code == 422
    assert "request.validation_failed" in caplog.text
    assert "body.file" in caplog.text
    assert "body.generation_mode" in caplog.text
    assert "PRIVATE_USER_TEXT" not in caplog.text


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
        response_format="json_object",
    )

    assert model.extra_body == {"enable_thinking": False}
    assert model.output_thinking is True
    assert model.model_kwargs == {"response_format": {"type": "json_object"}}


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
        lambda model, provider=None, temperature=0, thinking="default", capture_reasoning=None, response_format="text": (
            "qwen",
            provider,
            model,
            temperature,
        ),
    )
    monkeypatch.setattr(
        glm_model,
        "get_glm_model",
        lambda model, provider=None, temperature=0, response_format="text": (
            "glm",
            provider,
            model,
            temperature,
        ),
    )
    monkeypatch.setattr(
        deepseek_model,
        "get_deepseek_model",
        lambda model, provider=None, temperature=0, response_format="text": (
            "deepseek",
            provider,
            model,
            temperature,
        ),
    )
    monkeypatch.setattr(
        openai_model,
        "get_openai_model",
        lambda model, provider=None, temperature=0, response_format="text": (
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
        lambda model, provider=None, temperature=0, thinking="default", capture_reasoning=None, response_format="text": (
            provider,
            model,
            temperature,
            thinking,
            capture_reasoning,
            response_format,
        ),
    )

    assert client_factory.create_chat_model(
        LLMCallOptions(
            model_ref="dashscope:qwen3.7-plus",
            temperature=0.1,
            thinking="on",
            capture_reasoning=True,
            response_format="json_object",
        )
    ) == (
        "dashscope",
        "qwen3.7-plus",
        0.1,
        "on",
        True,
        "json_object",
    )


def test_llm_factory_passes_provider_side_max_output_tokens(monkeypatch) -> None:
    openai_model = model_family_module("openai")

    monkeypatch.setattr(
        openai_model,
        "get_openai_model",
        lambda model, provider=None, temperature=0, response_format="text", max_output_tokens=None: (
            provider,
            model,
            max_output_tokens,
        ),
    )

    assert client_factory.create_chat_model(
        LLMCallOptions(
            model_ref="openai:gpt-4.1",
            max_output_tokens=321,
        )
    ) == ("openai", "gpt-4.1", 321)



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


def test_agent_memory_pool_checks_connection_before_checkout(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakePool:
        check_connection = object()

        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(agent_memory, "AsyncConnectionPool", FakePool)

    agent_memory._pool("postgresql://user:password@127.0.0.1:5432/shadergen")

    assert captured["check"] is FakePool.check_connection


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
