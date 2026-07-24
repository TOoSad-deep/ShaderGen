import importlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.app.config import model_config
from agent.app.config.png_to_shader_min import (
    MAX_MIN_GRAPH_RECURSION_LIMIT,
    MIN_GRAPH_RECURSION_SAFETY_MARGIN,
    MIN_PIPELINE_CONFIG,
    derive_min_graph_recursion_limit,
    load_min_pipeline_config,
    required_min_graph_steps,
)
from agent.app.contracts.llm import LLMCallOptions
from agent.app.llms import client_factory
from backend.app.database import agent_memory
from backend.app.main import app


def model_family_module(name: str):
    return importlib.import_module(f"agent.app.llms.families.{name}")


def test_request_validation_failure_logs_safe_field_diagnostics(caplog) -> None:
    response = TestClient(app).post(
        "/api/shader/generate",
        data={
            "instruction": "PRIVATE_USER_TEXT",
        },
    )

    assert response.status_code == 422
    assert "request.validation_failed" in caplog.text
    assert "body.file" in caplog.text
    assert "PRIVATE_USER_TEXT" not in caplog.text


def test_llm_client_factory_configured() -> None:
    assert callable(client_factory.create_chat_model)


def test_model_name_env_uses_stable_default_and_allows_override(monkeypatch) -> None:
    monkeypatch.delenv("SHADER_GEN_MODEL_NAME", raising=False)
    assert model_config.model_name_env() == "openai:gpt-4.1"

    monkeypatch.setenv("SHADER_GEN_MODEL_NAME", "dashscope:qwen3.7-plus")
    assert model_config.model_name_env() == "dashscope:qwen3.7-plus"


def test_scene_mvp_runtime_policy_loads_packaged_yaml() -> None:
    assert MIN_PIPELINE_CONFIG.version == "scene_mvp_runtime_policy_v1"
    assert MIN_PIPELINE_CONFIG.run_classification == "independent_experiment"
    assert MIN_PIPELINE_CONFIG.experiment_id == "scene-mvp-agent-optimization-20260723"
    assert MIN_PIPELINE_CONFIG.report_schema_version == "scene_mvp_run_report_v1"
    assert re.fullmatch(r"[0-9a-f]{64}", MIN_PIPELINE_CONFIG.config_fingerprint)
    assert MIN_PIPELINE_CONFIG.quality_presets["fast"].render_budget == 48
    assert MIN_PIPELINE_CONFIG.quality_presets["balanced"].llm_budget == 4
    high = MIN_PIPELINE_CONFIG.quality_presets["high"]
    assert high.render_budget == 640
    assert high.llm_budget == 9
    assert high.refine_budget == 9
    assert high.target_mae == 0.04
    assert high.target_loss == 0.02
    assert required_min_graph_steps(9, 9) == 81
    assert high.recursion_limit == 81 + MIN_GRAPH_RECURSION_SAFETY_MARGIN == 85
    manual = MIN_PIPELINE_CONFIG.quality_presets["manual"]
    assert manual.render_budget == 1000
    assert manual.llm_budget == 32
    assert manual.refine_budget == 30
    assert required_min_graph_steps(32, 30) == 213
    assert manual.recursion_limit == 217
    assert MIN_PIPELINE_CONFIG.max_llm_budget == 32
    assert MIN_PIPELINE_CONFIG.max_refine_budget == 30
    assert MIN_PIPELINE_CONFIG.max_recursion_limit == 217


def test_scene_mvp_runtime_policy_accepts_custom_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "scene-mvp.yaml"
    config_path.write_text(
        """
version: test_policy_v1
run_classification: independent_experiment
experiment_id: custom-test-experiment
report_schema_version: test_report_v1
targets:
  mae: 0.12
  loss: 0.06
quality_presets:
  fast: {render_budget: 10, llm_budget: 1, refine_budget: 0}
  balanced: {render_budget: 20, llm_budget: 3, refine_budget: 2}
  high: {render_budget: 30, llm_budget: 9, refine_budget: 4}
  manual: {render_budget: 40, llm_budget: 10, refine_budget: 5}
""".strip(),
        encoding="utf-8",
    )

    configured = load_min_pipeline_config(config_path)

    assert configured.version == "test_policy_v1"
    assert configured.run_classification == "independent_experiment"
    assert configured.experiment_id == "custom-test-experiment"
    assert configured.report_schema_version == "test_report_v1"
    assert configured.quality_presets["fast"].render_budget == 10
    assert configured.quality_presets["high"].llm_budget == 9
    assert configured.max_llm_budget == 10
    assert configured.max_refine_budget == 5
    assert configured.quality_presets["high"].recursion_limit == 61
    assert configured.quality_presets["balanced"].target_mae == 0.12
    assert configured.quality_presets["balanced"].target_loss == 0.06


def test_scene_mvp_runtime_policy_fingerprint_is_canonical(tmp_path: Path) -> None:
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text(
        """
version: fingerprint_policy_v1
run_classification: independent_experiment
experiment_id: fingerprint-test
report_schema_version: test_report_v1
targets: {mae: 0.12, loss: 0.06}
quality_presets:
  fast: {render_budget: 10, llm_budget: 1, refine_budget: 0}
  balanced: {render_budget: 20, llm_budget: 3, refine_budget: 2}
  high: {render_budget: 30, llm_budget: 9, refine_budget: 4}
  manual: {render_budget: 40, llm_budget: 10, refine_budget: 5}
""".strip(),
        encoding="utf-8",
    )
    second.write_text(
        """
quality_presets:
  manual:
    refine_budget: 5
    llm_budget: 10
    render_budget: 40
  high:
    refine_budget: 4
    llm_budget: 9
    render_budget: 30
  fast: {refine_budget: 0, render_budget: 10, llm_budget: 1}
  balanced: {llm_budget: 3, refine_budget: 2, render_budget: 20}
targets:
  loss: 0.06
  mae: 0.12
report_schema_version: test_report_v1
experiment_id: fingerprint-test
run_classification: independent_experiment
version: fingerprint_policy_v1
""".strip(),
        encoding="utf-8",
    )

    assert (
        load_min_pipeline_config(first).config_fingerprint
        == load_min_pipeline_config(second).config_fingerprint
    )


def test_scene_mvp_runtime_policy_accepts_exact_frozen_benchmark(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "frozen.yaml"
    config_path.write_text(
        """
version: frozen_policy_v1
run_classification: frozen_benchmark
report_schema_version: frozen_report_v1
targets: {mae: 0.08, loss: 0.04}
quality_presets:
  fast: {render_budget: 48, llm_budget: 2, refine_budget: 1}
  balanced: {render_budget: 96, llm_budget: 4, refine_budget: 2}
  high: {render_budget: 160, llm_budget: 6, refine_budget: 3}
""".strip(),
        encoding="utf-8",
    )

    configured = load_min_pipeline_config(config_path)

    assert configured.run_classification == "frozen_benchmark"
    assert configured.experiment_id is None
    assert configured.quality_presets["high"].render_budget == 160


def test_scene_mvp_frozen_benchmark_rejects_manual_preset(tmp_path: Path) -> None:
    config_path = tmp_path / "frozen-with-manual.yaml"
    config_path.write_text(
        """
version: frozen_policy_with_manual_v1
run_classification: frozen_benchmark
report_schema_version: frozen_report_v1
targets: {mae: 0.08, loss: 0.04}
quality_presets:
  fast: {render_budget: 48, llm_budget: 2, refine_budget: 1}
  balanced: {render_budget: 96, llm_budget: 4, refine_budget: 2}
  high: {render_budget: 160, llm_budget: 6, refine_budget: 3}
  manual: {render_budget: 1000, llm_budget: 32, refine_budget: 30}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="frozen_benchmark quality_presets"):
        load_min_pipeline_config(config_path)


@pytest.mark.parametrize(
    ("targets", "high_budget"),
    (
        (
            "{mae: 0.08, loss: 0.02}",
            "{render_budget: 160, llm_budget: 6, refine_budget: 3}",
        ),
        (
            "{mae: 0.08, loss: 0.04}",
            "{render_budget: 640, llm_budget: 9, refine_budget: 9}",
        ),
    ),
)
def test_scene_mvp_frozen_benchmark_rejects_configuration_drift(
    tmp_path: Path,
    targets: str,
    high_budget: str,
) -> None:
    config_path = tmp_path / "drifted-frozen.yaml"
    config_path.write_text(
        f"""
version: drifted_frozen_policy_v1
run_classification: frozen_benchmark
report_schema_version: frozen_report_v1
targets: {targets}
quality_presets:
  fast: {{render_budget: 48, llm_budget: 2, refine_budget: 1}}
  balanced: {{render_budget: 96, llm_budget: 4, refine_budget: 2}}
  high: {high_budget}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="frozen_benchmark"):
        load_min_pipeline_config(config_path)


def test_scene_mvp_runtime_policy_derives_feature_aware_graph_bound() -> None:
    assert required_min_graph_steps(0, 9, max_features=0) == 9
    assert required_min_graph_steps(6, 3, max_features=4) == 35
    assert derive_min_graph_recursion_limit(6, 3, max_features=4) == 39
    assert derive_min_graph_recursion_limit(9, 9, max_features=4) == 69

    with pytest.raises(ValueError, match="参数队列上限"):
        required_min_graph_steps(2, 1, max_features=13)
    with pytest.raises(ValueError, match="安全上限"):
        derive_min_graph_recursion_limit(100, 99)


@pytest.mark.parametrize(
    "yaml_text",
    (
        """
version: invalid_missing_preset
run_classification: independent_experiment
experiment_id: invalid-test
report_schema_version: test_report_v1
targets: {mae: 0.08, loss: 0.04}
quality_presets:
  fast: {render_budget: 10, llm_budget: 1, refine_budget: 0}
  balanced: {render_budget: 20, llm_budget: 2, refine_budget: 1}
""",
        """
version: invalid_negative_budget
run_classification: independent_experiment
experiment_id: invalid-test
report_schema_version: test_report_v1
targets: {mae: 0.08, loss: 0.04}
quality_presets:
  fast: {render_budget: -1, llm_budget: 1, refine_budget: 0}
  balanced: {render_budget: 20, llm_budget: 2, refine_budget: 1}
  high: {render_budget: 30, llm_budget: 3, refine_budget: 2}
  manual: {render_budget: 40, llm_budget: 4, refine_budget: 3}
""",
        """
version: invalid_unknown_field
run_classification: independent_experiment
experiment_id: invalid-test
report_schema_version: test_report_v1
targets: {mae: 0.08, loss: 0.04, hidden: 1.0}
quality_presets:
  fast: {render_budget: 10, llm_budget: 1, refine_budget: 0}
  balanced: {render_budget: 20, llm_budget: 2, refine_budget: 1}
  high: {render_budget: 30, llm_budget: 3, refine_budget: 2}
  manual: {render_budget: 40, llm_budget: 4, refine_budget: 3}
""",
        f"""
version: invalid_graph_bound
run_classification: independent_experiment
experiment_id: invalid-test
report_schema_version: test_report_v1
targets: {{mae: 0.08, loss: 0.04}}
quality_presets:
  fast: {{render_budget: 10, llm_budget: 1, refine_budget: 0}}
  balanced: {{render_budget: 20, llm_budget: 2, refine_budget: 1}}
  high: {{render_budget: 999, llm_budget: {MAX_MIN_GRAPH_RECURSION_LIMIT}, refine_budget: {MAX_MIN_GRAPH_RECURSION_LIMIT}}}
  manual: {{render_budget: 40, llm_budget: 4, refine_budget: 3}}
""",
    ),
)
def test_scene_mvp_runtime_policy_rejects_invalid_yaml(
    tmp_path: Path,
    yaml_text: str,
) -> None:
    config_path = tmp_path / "invalid-scene-mvp.yaml"
    config_path.write_text(yaml_text.strip(), encoding="utf-8")

    with pytest.raises(ValueError, match="scene_mvp 配置无效"):
        load_min_pipeline_config(config_path)


@pytest.mark.parametrize(
    "yaml_text",
    (
        """
version: missing_experiment_id
run_classification: independent_experiment
report_schema_version: test_report_v1
targets: {mae: 0.08, loss: 0.04}
quality_presets:
  fast: {render_budget: 48, llm_budget: 2, refine_budget: 1}
  balanced: {render_budget: 96, llm_budget: 4, refine_budget: 2}
  high: {render_budget: 160, llm_budget: 6, refine_budget: 3}
  manual: {render_budget: 1000, llm_budget: 32, refine_budget: 30}
""",
        """
version: frozen_with_experiment_id
run_classification: frozen_benchmark
experiment_id: forbidden-experiment
report_schema_version: test_report_v1
targets: {mae: 0.08, loss: 0.04}
quality_presets:
  fast: {render_budget: 48, llm_budget: 2, refine_budget: 1}
  balanced: {render_budget: 96, llm_budget: 4, refine_budget: 2}
  high: {render_budget: 160, llm_budget: 6, refine_budget: 3}
""",
    ),
)
def test_scene_mvp_runtime_policy_rejects_invalid_run_identity(
    tmp_path: Path,
    yaml_text: str,
) -> None:
    config_path = tmp_path / "invalid-run-identity.yaml"
    config_path.write_text(yaml_text.strip(), encoding="utf-8")

    with pytest.raises(ValueError, match="scene_mvp 配置无效"):
        load_min_pipeline_config(config_path)


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
    monkeypatch.setenv(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
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
