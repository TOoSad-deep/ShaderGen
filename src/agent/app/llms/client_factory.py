"""按 provider 和 model family 创建 LangChain 聊天客户端."""

from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel

from agent.app.contracts.llm import LLMCallOptions
from agent.app.llms.families import deepseek, glm, openai, qwen
from agent.app.llms.provider_config import PROVIDER_NAMES

MODEL_FAMILY_PREFIXES = ("qwen:", "glm:", "deepseek:", "openai:")
DEFAULT_PROVIDER_BY_FAMILY = {
    "qwen": "dashscope",
    "glm": "glm",
    "deepseek": "deepseek",
    "openai": "openai",
}


@dataclass(frozen=True)
class ChatModelBinding:
    """真实客户端及其解析后的调用身份."""

    client: BaseChatModel
    requested_model_ref: str
    resolved_provider: str
    configured_model_name: str


def create_chat_model(options: LLMCallOptions) -> BaseChatModel:
    """按调用参数创建真实 LangChain 聊天客户端."""
    return create_chat_model_binding(options).client


def create_chat_model_binding(options: LLMCallOptions) -> ChatModelBinding:
    """创建客户端并保留 provider/model 解析事实供 Gateway 审计."""
    provider, model_name = _split_model_reference(options.model_ref)
    family = _model_family(provider, model_name)
    resolved_provider = provider or DEFAULT_PROVIDER_BY_FAMILY[family]
    client: BaseChatModel
    if family == "qwen":
        if options.max_output_tokens is None:
            client = qwen.get_qwen_model(
                model_name,
                provider=provider,
                temperature=options.temperature,
                thinking=options.thinking,
                capture_reasoning=options.capture_reasoning,
                response_format=options.response_format,
            )
        else:
            client = qwen.get_qwen_model(
                model_name,
                provider=provider,
                temperature=options.temperature,
                thinking=options.thinking,
                capture_reasoning=options.capture_reasoning,
                response_format=options.response_format,
                max_output_tokens=options.max_output_tokens,
            )
    elif family == "glm":
        if options.max_output_tokens is None:
            client = glm.get_glm_model(
                model_name,
                provider,
                options.temperature,
                options.response_format,
            )
        else:
            client = glm.get_glm_model(
                model_name,
                provider,
                options.temperature,
                options.response_format,
                options.max_output_tokens,
            )
    elif family == "deepseek":
        if options.max_output_tokens is None:
            client = deepseek.get_deepseek_model(
                model_name,
                provider,
                options.temperature,
                options.response_format,
            )
        else:
            client = deepseek.get_deepseek_model(
                model_name,
                provider,
                options.temperature,
                options.response_format,
                options.max_output_tokens,
            )
    elif family == "openai":
        if options.max_output_tokens is None:
            client = openai.get_openai_model(
                model_name,
                provider,
                options.temperature,
                options.response_format,
            )
        else:
            client = openai.get_openai_model(
                model_name,
                provider,
                options.temperature,
                options.response_format,
                options.max_output_tokens,
            )
    else:  # pragma: no cover - _model_family 已封闭分支
        raise ValueError(f"无法识别模型系列：{model_name}。")
    return ChatModelBinding(
        client=client,
        requested_model_ref=options.model_ref,
        resolved_provider=resolved_provider,
        configured_model_name=model_name,
    )


def resolved_model_reference(model_ref: str) -> tuple[str, str]:
    """返回 model ref 最终使用的 provider 和配置模型名."""
    provider, model_name = _split_model_reference(model_ref)
    family = _model_family(provider, model_name)
    return provider or DEFAULT_PROVIDER_BY_FAMILY[family], model_name


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
