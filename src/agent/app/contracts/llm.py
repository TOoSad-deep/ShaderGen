"""LLM Gateway 的中立调用契约."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from langchain_core.messages import AIMessage, BaseMessage

ThinkingMode = Literal["default", "on", "off"]
ResponseFormat = Literal["text", "json_object"]
ModelIdentitySource = Literal["response_metadata", "configured_fallback"]
THINKING_MODES = {"default", "on", "off"}
RESPONSE_FORMATS = {"text", "json_object"}


def normalize_thinking_mode(value: ThinkingMode | str | None) -> ThinkingMode:
    """规范化模型 thinking 语义值."""
    normalized = "default" if value is None else value
    if normalized not in THINKING_MODES:
        raise ValueError("thinking 只能配置为 default/on/off。")
    return cast(ThinkingMode, normalized)


def normalize_response_format(value: ResponseFormat | str) -> ResponseFormat:
    """规范化模型输出格式语义值."""
    if value not in RESPONSE_FORMATS:
        raise ValueError("response_format 只能配置为 text/json_object。")
    return cast(ResponseFormat, value)


@dataclass(frozen=True)
class TokenUsage:
    """统一 token 使用量."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class LLMCallOptions:
    """单次 LLM 调用的语义参数."""

    model_ref: str
    temperature: float = 0
    thinking: ThinkingMode | str | None = "default"
    capture_reasoning: bool | None = None
    response_format: ResponseFormat = "text"
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        """校验并规范化调用参数."""
        if not self.model_ref.strip():
            raise ValueError("model_ref 不能为空。")
        object.__setattr__(self, "thinking", normalize_thinking_mode(self.thinking))
        object.__setattr__(
            self,
            "response_format",
            normalize_response_format(self.response_format),
        )
        if self.capture_reasoning is not None and not isinstance(
            self.capture_reasoning, bool
        ):
            raise ValueError("capture_reasoning 只能配置为 true/false。")
        if self.max_output_tokens is not None and (
            not isinstance(self.max_output_tokens, int)
            or isinstance(self.max_output_tokens, bool)
            or self.max_output_tokens <= 0
        ):
            raise ValueError("max_output_tokens 必须是正整数或 null。")


@dataclass(frozen=True)
class LLMResponse:
    """供应商无关的 LLM 响应."""

    message: AIMessage
    text: str
    reasoning_content: str | None
    model_ref: str
    latency_ms: int
    usage: TokenUsage | None = None
    requested_model_ref: str | None = None
    model_identity_source: ModelIdentitySource = "configured_fallback"


class LLMGateway(Protocol):
    """Node 可依赖的唯一 LLM 调用接口."""

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        options: LLMCallOptions,
    ) -> LLMResponse:
        """调用模型并返回统一响应."""
        ...


class LLMGatewayError(RuntimeError):
    """统一 LLM 错误基类."""

    def __init__(
        self,
        message: str,
        *,
        model_ref: str,
        provider: str | None = None,
        retryable: bool = False,
    ) -> None:
        """创建包含安全调用元数据的错误."""
        super().__init__(message)
        self.model_ref = model_ref
        self.provider = provider
        self.retryable = retryable


class LLMConfigurationError(LLMGatewayError):
    """LLM 配置错误."""

    def __init__(
        self,
        message: str,
        *,
        model_ref: str,
        provider: str | None,
    ) -> None:
        """创建不可重试的配置错误."""
        super().__init__(
            message,
            model_ref=model_ref,
            provider=provider,
            retryable=False,
        )


class LLMInvocationError(LLMGatewayError):
    """LLM 外部调用错误."""


class LLMResponseError(LLMGatewayError):
    """LLM 响应规范化错误."""
