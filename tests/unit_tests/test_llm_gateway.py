import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.app.contracts.llm import (
    LLMCallOptions,
    LLMConfigurationError,
    LLMInvocationError,
    LLMResponseError,
)
from agent.app.llms.gateway import LangChainLLMGateway


@pytest.mark.anyio
async def test_gateway_normalizes_response() -> None:
    class FakeClient:
        async def ainvoke(self, messages):
            assert messages == [HumanMessage(content="你好")]
            return AIMessage(
                content="完成",
                additional_kwargs={"reasoning_content": "推理"},
                response_metadata={
                    "model_name": "qwen3.7-plus-202607",
                    # OpenAI-compatible 客户端可能填入适配器身份，不能覆盖真实 provider。
                    "model_provider": "openai",
                },
                usage_metadata={
                    "input_tokens": 3,
                    "output_tokens": 2,
                    "total_tokens": 5,
                },
            )

    times = iter((10.0, 10.125))
    captured = []
    gateway = LangChainLLMGateway(
        client_factory=lambda options: captured.append(options) or FakeClient(),
        clock=lambda: next(times),
    )
    options = LLMCallOptions(
        model_ref="dashscope:qwen3.7-plus",
        thinking="on",
        capture_reasoning=True,
        max_output_tokens=321,
    )

    result = await gateway.ainvoke([HumanMessage(content="你好")], options)

    assert captured == [options]
    assert result.text == "完成"
    assert result.reasoning_content == "推理"
    assert result.model_ref == "dashscope:qwen3.7-plus-202607"
    assert result.requested_model_ref == options.model_ref
    assert result.model_identity_source == "response_metadata"
    assert result.latency_ms == 125
    assert result.usage is not None
    assert result.usage.total_tokens == 5


@pytest.mark.anyio
async def test_gateway_marks_configured_model_identity_fallback() -> None:
    class FakeClient:
        async def ainvoke(self, messages):
            return AIMessage(content="完成")

    gateway = LangChainLLMGateway(client_factory=lambda options: FakeClient())

    result = await gateway.ainvoke(
        [HumanMessage(content="你好")],
        LLMCallOptions(model_ref="qwen:qwen3.7-plus"),
    )

    assert result.model_ref == "dashscope:qwen3.7-plus"
    assert result.requested_model_ref == "qwen:qwen3.7-plus"
    assert result.model_identity_source == "configured_fallback"


@pytest.mark.anyio
async def test_gateway_wraps_configuration_error_without_secret() -> None:
    def fail_factory(options):
        raise ValueError("secret-key")

    gateway = LangChainLLMGateway(client_factory=fail_factory)

    with pytest.raises(LLMConfigurationError) as caught:
        await gateway.ainvoke(
            [HumanMessage(content="你好")],
            LLMCallOptions(model_ref="dashscope:qwen3.7-plus"),
        )

    assert "secret-key" not in str(caught.value)
    assert caught.value.provider == "dashscope"


@pytest.mark.anyio
async def test_gateway_marks_timeout_retryable() -> None:
    class FailingClient:
        async def ainvoke(self, messages):
            raise TimeoutError("timeout")

    gateway = LangChainLLMGateway(client_factory=lambda options: FailingClient())

    with pytest.raises(LLMInvocationError) as caught:
        await gateway.ainvoke(
            [HumanMessage(content="你好")],
            LLMCallOptions(model_ref="openai:gpt-4.1"),
        )

    assert caught.value.retryable is True


@pytest.mark.anyio
async def test_gateway_rejects_non_ai_message() -> None:
    class InvalidClient:
        async def ainvoke(self, messages):
            return HumanMessage(content="错误类型")

    gateway = LangChainLLMGateway(client_factory=lambda options: InvalidClient())

    with pytest.raises(LLMResponseError):
        await gateway.ainvoke(
            [HumanMessage(content="你好")],
            LLMCallOptions(model_ref="openai:gpt-4.1"),
        )
