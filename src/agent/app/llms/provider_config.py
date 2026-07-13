"""OpenAI-compatible provider 凭据和地址配置."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSettings:
    """模型 provider 调用配置."""

    name: str
    api_key: str | None
    base_url: str | None


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
