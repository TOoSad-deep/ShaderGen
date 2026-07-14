"""提示词加载工具."""

from dataclasses import dataclass
from importlib.resources import files

import yaml  # type: ignore[import-untyped]


@dataclass(frozen=True)
class PromptDefinition:
    """带可追踪版本的 Prompt 定义."""

    name: str
    version: str
    prompt: str


def load_prompt_definition(name: str) -> PromptDefinition:
    """按名称读取带版本的 YAML Prompt."""
    prompt_file = files("agent.app.prompts").joinpath(f"{name}.yaml")
    data = yaml.safe_load(prompt_file.read_text(encoding="utf-8"))
    prompt = str(data["prompt"]).strip()
    version = str(data.get("version", name)).strip()
    if not version:
        raise ValueError(f"Prompt {name} 的 version 不能为空。")
    return PromptDefinition(name=name, version=version, prompt=prompt)


def load_prompt(name: str) -> str:
    """按名称读取 YAML 提示词."""
    return load_prompt_definition(name).prompt
