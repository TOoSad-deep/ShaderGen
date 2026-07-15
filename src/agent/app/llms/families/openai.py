"""OpenAI model-family 客户端工厂."""

from langchain_openai import ChatOpenAI

from agent.app.contracts.llm import ResponseFormat
from agent.app.llms.provider_config import (
    provider_settings,
    response_format_model_kwargs,
)


def get_openai_model(
    model: str,
    provider: str | None = None,
    temperature: float = 0,
    response_format: ResponseFormat | str = "text",
    max_output_tokens: int | None = None,
) -> ChatOpenAI:
    """创建 OpenAI 系列聊天客户端."""
    settings = provider_settings(provider, default_provider="openai")
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=settings.secret_api_key,
        base_url=settings.base_url,
        model_kwargs=response_format_model_kwargs(response_format),
        max_completion_tokens=max_output_tokens,
    )
