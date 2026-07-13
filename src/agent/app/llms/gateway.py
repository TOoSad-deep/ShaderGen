"""LangChain LLM Gateway 实现."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from time import perf_counter

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage

from agent.app.contracts.llm import (
    LLMCallOptions,
    LLMConfigurationError,
    LLMInvocationError,
    LLMResponse,
    LLMResponseError,
    TokenUsage,
)
from agent.app.llms.client_factory import create_chat_model
from agent.app.llms.provider_config import PROVIDER_NAMES

ClientFactory = Callable[[LLMCallOptions], BaseChatModel]


class LangChainLLMGateway:
    """通过 LangChain 客户端执行统一 LLM 调用."""

    def __init__(
        self,
        client_factory: ClientFactory = create_chat_model,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        """注入客户端工厂和单调时钟."""
        self._client_factory = client_factory
        self._clock = clock

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        options: LLMCallOptions,
    ) -> LLMResponse:
        """调用 LangChain 客户端并返回统一响应."""
        provider = _provider_name(options.model_ref)
        try:
            client = self._client_factory(options)
        except Exception as exc:
            raise LLMConfigurationError(
                "LLM 配置无效。",
                model_ref=options.model_ref,
                provider=provider,
            ) from exc

        started_at = self._clock()
        try:
            message = await client.ainvoke(list(messages))
        except Exception as exc:
            raise LLMInvocationError(
                "LLM 调用失败。",
                model_ref=options.model_ref,
                provider=provider,
                retryable=_is_retryable(exc),
            ) from exc
        latency_ms = int((self._clock() - started_at) * 1000)

        if not isinstance(message, AIMessage):
            raise LLMResponseError(
                "LLM 响应类型无效。",
                model_ref=options.model_ref,
                provider=provider,
                retryable=False,
            )

        return LLMResponse(
            message=message,
            text=message.text,
            reasoning_content=_reasoning_content(message),
            model_ref=options.model_ref,
            latency_ms=latency_ms,
            usage=_token_usage(message),
        )


def _provider_name(model_ref: str) -> str | None:
    prefix, separator, _ = model_ref.partition(":")
    return prefix if separator and prefix in PROVIDER_NAMES else None


def _reasoning_content(message: AIMessage) -> str | None:
    value = message.additional_kwargs.get("reasoning_content")
    return str(value) if value else None


def _token_usage(message: AIMessage) -> TokenUsage | None:
    usage = message.usage_metadata
    if not usage:
        return None
    return TokenUsage(
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        total_tokens=usage.get("total_tokens"),
    )


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    name = type(exc).__name__.lower()
    return any(token in name for token in ("timeout", "ratelimit", "connection"))
