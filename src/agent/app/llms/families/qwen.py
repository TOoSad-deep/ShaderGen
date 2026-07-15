"""Qwen model-family 客户端和响应适配."""

from __future__ import annotations

from typing import Any

from langchain_core.outputs import ChatResult
from langchain_openai import ChatOpenAI

from agent.app.config.model_config import bool_env, optional_bool_env
from agent.app.contracts.llm import (
    ResponseFormat,
    ThinkingMode,
    normalize_thinking_mode,
)
from agent.app.llms.provider_config import (
    provider_settings,
    response_format_model_kwargs,
)

SHADER_GEN_QWEN_ENABLE_THINKING = optional_bool_env("SHADER_GEN_QWEN_ENABLE_THINKING")
SHADER_GEN_QWEN_OUTPUT_THINKING = bool_env("SHADER_GEN_QWEN_OUTPUT_THINKING")


class QwenChatOpenAI(ChatOpenAI):
    """保留可选 Qwen reasoning_content 的 ChatOpenAI 变体."""

    output_thinking: bool = False

    def _create_chat_result(
        self,
        response: dict[str, Any] | Any,
        generation_info: dict[str, Any] | None = None,
    ) -> ChatResult:
        """把 Qwen reasoning_content 规范化进 AIMessage."""
        result = super()._create_chat_result(response, generation_info)
        if not self.output_thinking:
            return result

        response_dict = _response_to_dict(response)
        for generation, choice in zip(
            result.generations,
            response_dict.get("choices", []),
        ):
            reasoning_content = choice.get("message", {}).get("reasoning_content")
            if reasoning_content:
                generation.message.additional_kwargs["reasoning_content"] = (
                    reasoning_content
                )
        return result


def _response_to_dict(response: dict[str, Any] | Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        response_dict = response.model_dump()
        if isinstance(response_dict, dict):
            return response_dict
    return {}


def _resolve_thinking_enabled(
    thinking: ThinkingMode | str | None,
    default: bool | None,
) -> bool | None:
    normalized = normalize_thinking_mode(thinking)
    if normalized == "on":
        return True
    if normalized == "off":
        return False
    return default


def _resolve_capture_reasoning(value: bool | None, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError("capture_reasoning 只能配置为 true/false。")
    return value


def get_qwen_model(
    model: str,
    provider: str | None = None,
    temperature: float = 0,
    thinking: ThinkingMode | str | None = "default",
    capture_reasoning: bool | None = None,
    response_format: ResponseFormat | str = "text",
    max_output_tokens: int | None = None,
) -> QwenChatOpenAI:
    """创建 Qwen 系列聊天客户端."""
    settings = provider_settings(provider, default_provider="dashscope")
    enable_thinking = _resolve_thinking_enabled(
        thinking,
        SHADER_GEN_QWEN_ENABLE_THINKING,
    )
    return QwenChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=settings.secret_api_key,
        base_url=settings.base_url,
        model_kwargs=response_format_model_kwargs(response_format),
        max_completion_tokens=max_output_tokens,
        extra_body=(
            None if enable_thinking is None else {"enable_thinking": enable_thinking}
        ),
        output_thinking=_resolve_capture_reasoning(
            capture_reasoning,
            SHADER_GEN_QWEN_OUTPUT_THINKING,
        ),
    )
