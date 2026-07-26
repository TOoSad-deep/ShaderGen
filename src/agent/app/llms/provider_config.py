"""OpenAI-compatible provider 凭据和地址配置."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr

from agent.app.contracts.llm import ResponseFormat, normalize_response_format


@dataclass(frozen=True)
class ProviderSettings:
    """模型 provider 调用配置."""

    name: str
    api_key: str | None
    base_url: str | None

    @property
    def secret_api_key(self) -> SecretStr | None:
        """返回 LangChain 客户端接受的脱敏 API key 类型."""
        return SecretStr(self.api_key) if self.api_key else None


@dataclass(frozen=True)
class ProviderEnv:
    """模型 provider 环境变量约定."""

    api_key_env: str
    base_url_env: str
    default_base_url: str | None
    require_api_key: bool = True


PROVIDER_ENVS = {
    "dashscope": ProviderEnv(
        api_key_env="DASHSCOPE_API_KEY",
        base_url_env="DASHSCOPE_BASE_URL",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    "glm": ProviderEnv(
        api_key_env="GLM_API_KEY",
        base_url_env="GLM_BASE_URL",
        default_base_url="https://open.bigmodel.cn/api/paas/v4",
    ),
    "deepseek": ProviderEnv(
        api_key_env="DEEPSEEK_API_KEY",
        base_url_env="DEEPSEEK_BASE_URL",
        default_base_url="https://api.deepseek.com",
    ),
    "kimi": ProviderEnv(
        api_key_env="KIMI_API_KEY",
        base_url_env="KIMI_BASE_URL",
        default_base_url="https://api.kimi.com/coding/v1",
    ),
    "openai": ProviderEnv(
        api_key_env="OPENAI_API_KEY",
        base_url_env="OPENAI_BASE_URL",
        default_base_url=None,
        require_api_key=False,
    ),
}
PROVIDER_NAMES = frozenset(PROVIDER_ENVS)


def provider_settings(
    provider: str | None,
    default_provider: str,
) -> ProviderSettings:
    """返回 OpenAI-compatible provider 配置."""
    provider_name = provider or default_provider
    provider_env = PROVIDER_ENVS.get(provider_name)
    if provider_env is None:
        raise ValueError(f"不支持的模型 provider：{provider_name}。")

    api_key = os.getenv(provider_env.api_key_env) or None
    if provider_env.require_api_key and not api_key:
        raise ValueError(f"使用 {provider_name} provider 需要配置 {provider_env.api_key_env}。")

    base_url = os.getenv(provider_env.base_url_env) or provider_env.default_base_url
    return ProviderSettings(name=provider_name, api_key=api_key, base_url=base_url)


def response_format_model_kwargs(
    response_format: ResponseFormat | str,
) -> dict[str, Any]:
    """把中立输出格式映射为 OpenAI-compatible response_format."""
    normalized = normalize_response_format(response_format)
    if normalized == "text":
        return {}
    return {"response_format": {"type": normalized}}
