"""模型配置."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from agent.app.contracts.llm import LLMCallOptions

load_dotenv()

TRUE_ENV_VALUES = {"1", "true", "yes", "on"}
FALSE_ENV_VALUES = {"0", "false", "no", "off"}
DEFAULT_SHADER_GEN_MODEL_NAME = "openai:gpt-4.1"


def _parse_bool_env(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in TRUE_ENV_VALUES:
        return True
    if normalized in FALSE_ENV_VALUES:
        return False
    raise ValueError(f"{name} 只能配置为 true/false/1/0/yes/no/on/off。")


def optional_bool_env(name: str) -> bool | None:
    """返回可选布尔环境变量值."""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return _parse_bool_env(name, value)


def bool_env(name: str, default: bool = False) -> bool:
    """返回布尔环境变量值."""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return _parse_bool_env(name, value)


def model_name_env() -> str:
    """返回当前模型配置，并在未设置时使用稳定默认值."""
    return os.getenv("SHADER_GEN_MODEL_NAME", DEFAULT_SHADER_GEN_MODEL_NAME)


SHADER_GEN_MODEL_NAME = model_name_env()


@dataclass(frozen=True)
class NodeModelConfig:
    """Node 级 LLM 调用和 reasoning 日志配置."""

    call: LLMCallOptions
    print_reasoning: bool = False
