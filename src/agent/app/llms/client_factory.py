"""按 provider 和 model family 创建 LangChain 聊天客户端."""

from langchain_core.language_models.chat_models import BaseChatModel

from agent.app.contracts.llm import LLMCallOptions
from agent.app.llms.families import deepseek, glm, openai, qwen
from agent.app.llms.provider_config import PROVIDER_NAMES

MODEL_FAMILY_PREFIXES = ("qwen:", "glm:", "deepseek:", "openai:")


def create_chat_model(options: LLMCallOptions) -> BaseChatModel:
    """按调用参数创建真实 LangChain 聊天客户端."""
    provider, model_name = _split_model_reference(options.model_ref)
    family = _model_family(provider, model_name)
    if family == "qwen":
        return qwen.get_qwen_model(
            model_name,
            provider=provider,
            temperature=options.temperature,
            thinking=options.thinking,
            capture_reasoning=options.capture_reasoning,
        )
    if family == "glm":
        return glm.get_glm_model(model_name, provider, options.temperature)
    if family == "deepseek":
        return deepseek.get_deepseek_model(model_name, provider, options.temperature)
    if family == "openai":
        return openai.get_openai_model(model_name, provider, options.temperature)
    raise ValueError(f"无法识别模型系列：{model_name}。")


def _split_model_reference(model_ref: str) -> tuple[str | None, str]:
    prefix, separator, model_name = model_ref.partition(":")
    if not separator:
        return None, model_ref
    if prefix in PROVIDER_NAMES:
        return prefix, model_name
    if model_ref.startswith(MODEL_FAMILY_PREFIXES):
        return None, model_name
    return None, model_ref


def _model_family(provider: str | None, model_name: str) -> str:
    normalized = model_name.lower()
    if normalized.startswith(("qwen", "qwq")):
        return "qwen"
    if normalized.startswith("glm"):
        return "glm"
    if normalized.startswith("deepseek"):
        return "deepseek"
    if normalized.startswith(("gpt", "o1", "o3", "o4")):
        return "openai"
    if provider in {"glm", "deepseek", "openai"}:
        return provider
    if provider is None:
        return "openai"
    raise ValueError(f"{provider}:{model_name} 未声明可用的模型系列。")
