"""Kimi model-family 客户端工厂."""

import os

from langchain_openai import ChatOpenAI

from agent.app.contracts.llm import ResponseFormat
from agent.app.llms.provider_config import (
    llm_request_timeout_seconds,
    provider_settings,
    response_format_model_kwargs,
)

# Kimi Code 端点当前只允许 temperature=1，调用方温度一律不下发。
KIMI_REQUIRED_TEMPERATURE = 1.0
# Kimi Code 端点支持 low/high/max 三档 thinking effort，默认低档。
KIMI_REASONING_EFFORTS = frozenset({"low", "high", "max"})
DEFAULT_KIMI_REASONING_EFFORT = "low"


def _reasoning_effort_env() -> str:
    """读取并校验 Kimi thinking effort 环境变量."""
    value = (
        os.getenv("SHADER_GEN_KIMI_REASONING_EFFORT", DEFAULT_KIMI_REASONING_EFFORT)
        .strip()
        .lower()
    )
    if value not in KIMI_REASONING_EFFORTS:
        raise ValueError("SHADER_GEN_KIMI_REASONING_EFFORT 只能配置为 low/high/max。")
    return value


SHADER_GEN_KIMI_REASONING_EFFORT = _reasoning_effort_env()


def get_kimi_model(
    model: str,
    provider: str | None = None,
    temperature: float = 0,
    response_format: ResponseFormat | str = "text",
    max_output_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> ChatOpenAI:
    """创建 Kimi 系列聊天客户端."""
    del temperature  # Kimi Code 端点仅允许 temperature=1，由 family 固定。
    effort = SHADER_GEN_KIMI_REASONING_EFFORT if reasoning_effort is None else reasoning_effort
    normalized_effort = effort.strip().lower()
    if normalized_effort not in KIMI_REASONING_EFFORTS:
        raise ValueError("kimi reasoning_effort 只能配置为 low/high/max。")
    settings = provider_settings(provider, default_provider="kimi")
    return ChatOpenAI(
        model=model,
        temperature=KIMI_REQUIRED_TEMPERATURE,
        reasoning_effort=normalized_effort,
        api_key=settings.secret_api_key,
        base_url=settings.base_url,
        model_kwargs=response_format_model_kwargs(response_format),
        max_completion_tokens=max_output_tokens,
        timeout=llm_request_timeout_seconds(),
    )
