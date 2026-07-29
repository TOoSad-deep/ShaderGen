"""LangChain LLM Gateway 实现."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from time import perf_counter

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage

from agent.app.contracts.llm import (
    EffectiveCallIdentity,
    LLMCallOptions,
    LLMConfigurationError,
    LLMInvocationError,
    LLMResponse,
    LLMResponseError,
    ModelIdentitySource,
    TokenUsage,
)
from agent.app.llms.client_factory import (
    ChatModelBinding,
    create_chat_model_binding,
    resolve_effective_sampling,
    resolved_model_reference,
)
from agent.app.llms.provider_config import PROVIDER_NAMES

ClientFactory = Callable[[LLMCallOptions], BaseChatModel | ChatModelBinding]

logger = logging.getLogger("agent.llm")


class LangChainLLMGateway:
    """通过 LangChain 客户端执行统一 LLM 调用."""

    def __init__(
        self,
        client_factory: ClientFactory = create_chat_model_binding,
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
            created = self._client_factory(options)
            binding = _normalize_binding(created, options)
        except Exception as exc:
            # Provider exceptions can embed request metadata or credentials in their
            # messages.  Keep terminal diagnostics to stable classification fields.
            logger.warning(
                "llm.gateway_failure event=%s provider=%s model_ref=%s "
                "error_type=%s retryable=%s stage=%s",
                "llm.configuration_failed",
                provider,
                options.model_ref,
                type(exc).__name__,
                False,
                "configuration",
            )
            raise LLMConfigurationError(
                "LLM 配置无效。",
                model_ref=options.model_ref,
                provider=provider,
            ) from exc

        started_at = self._clock()
        try:
            message = await binding.client.ainvoke(list(messages))
        except Exception as exc:
            retryable = _is_retryable(exc)
            # Do not use exc_info here: third-party client tracebacks may include
            # provider response bodies, prompts, or other sensitive request data.
            logger.warning(
                "llm.gateway_failure event=%s provider=%s model_ref=%s "
                "error_type=%s retryable=%s stage=%s",
                "llm.invocation_failed",
                binding.resolved_provider,
                options.model_ref,
                type(exc).__name__,
                retryable,
                "invocation",
            )
            raise LLMInvocationError(
                "LLM 调用失败。",
                model_ref=options.model_ref,
                provider=provider,
                retryable=retryable,
            ) from exc
        latency_ms = int((self._clock() - started_at) * 1000)

        if not isinstance(message, AIMessage):
            raise LLMResponseError(
                "LLM 响应类型无效。",
                model_ref=options.model_ref,
                provider=provider,
                retryable=False,
            )

        model_ref, identity_source = _actual_model_ref(message, binding)
        return LLMResponse(
            message=message,
            text=message.text,
            reasoning_content=_reasoning_content(message),
            model_ref=model_ref,
            latency_ms=latency_ms,
            usage=_token_usage(message),
            requested_model_ref=options.model_ref,
            model_identity_source=identity_source,
            effective_identity=EffectiveCallIdentity(
                provider=binding.resolved_provider,
                model_ref=model_ref,
                model_identity_source=identity_source,
                sampling=binding.effective_sampling
                or resolve_effective_sampling(options),
            ),
        )


def _provider_name(model_ref: str) -> str | None:
    try:
        provider, _ = resolved_model_reference(model_ref)
        return provider
    except ValueError:
        pass
    prefix, separator, _ = model_ref.partition(":")
    return prefix if separator and prefix in PROVIDER_NAMES else None


def _normalize_binding(
    created: BaseChatModel | ChatModelBinding,
    options: LLMCallOptions,
) -> ChatModelBinding:
    if isinstance(created, ChatModelBinding):
        return created
    provider, model_name = resolved_model_reference(options.model_ref)
    return ChatModelBinding(
        client=created,
        requested_model_ref=options.model_ref,
        resolved_provider=provider,
        configured_model_name=model_name,
        effective_sampling=resolve_effective_sampling(options),
    )


def _actual_model_ref(
    message: AIMessage,
    binding: ChatModelBinding,
) -> tuple[str, ModelIdentitySource]:
    reported = message.response_metadata.get(
        "model_name"
    ) or message.response_metadata.get("model")
    if isinstance(reported, str) and reported.strip():
        name = reported.strip()
        prefix, separator, remainder = name.partition(":")
        if separator and prefix in PROVIDER_NAMES and remainder:
            return name, "response_metadata"
        return f"{binding.resolved_provider}:{name}", "response_metadata"
    return (
        f"{binding.resolved_provider}:{binding.configured_model_name}",
        "configured_fallback",
    )


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
