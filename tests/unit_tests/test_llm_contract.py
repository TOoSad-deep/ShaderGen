import pytest
from langchain_core.messages import AIMessage

from agent.app.contracts.llm import (
    LLMCallOptions,
    LLMConfigurationError,
    LLMResponse,
    TokenUsage,
)


def test_llm_call_options_reject_invalid_values() -> None:
    with pytest.raises(ValueError, match="model_ref"):
        LLMCallOptions(model_ref="   ")
    with pytest.raises(ValueError, match="thinking"):
        LLMCallOptions(model_ref="openai:gpt-4.1", thinking="invalid")
    with pytest.raises(ValueError, match="capture_reasoning"):
        LLMCallOptions(
            model_ref="openai:gpt-4.1",
            capture_reasoning="yes",  # type: ignore[arg-type]
        )


def test_llm_response_keeps_normalized_metadata() -> None:
    message = AIMessage(content="完成")
    response = LLMResponse(
        message=message,
        text="完成",
        reasoning_content=None,
        model_ref="openai:gpt-4.1",
        latency_ms=12,
        usage=TokenUsage(input_tokens=3, output_tokens=2, total_tokens=5),
    )

    assert response.message is message
    assert response.model_ref == "openai:gpt-4.1"
    assert response.usage is not None
    assert response.usage.total_tokens == 5


def test_gateway_error_exposes_safe_metadata() -> None:
    error = LLMConfigurationError(
        "LLM 配置无效。",
        model_ref="dashscope:qwen3.7-plus",
        provider="dashscope",
    )

    assert str(error) == "LLM 配置无效。"
    assert error.model_ref == "dashscope:qwen3.7-plus"
    assert error.provider == "dashscope"
    assert error.retryable is False
