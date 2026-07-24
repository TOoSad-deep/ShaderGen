"""ShaderGraph Model Author 的 Prompt 定义与严格 Schema 装配.

本模块只装配契约、Parser 与 Prompt，尚未接入 Graph；未来 author 节点复用
`invoke_min_author` 的有界调用与结构修复，消息体由节点按契约组装。
"""

from agent.app.parsers.shader_graph_author import (
    shader_graph_author_patch_json_schema,
    shader_graph_document_json_schema,
)
from agent.app.prompts.prompt_loader import load_prompt_definition

SHADER_GRAPH_AUTHOR_INITIAL_PROMPT = load_prompt_definition(
    "shader_graph_author_initial_v1"
)
SHADER_GRAPH_AUTHOR_REFINE_PROMPT = load_prompt_definition(
    "shader_graph_author_refine_v1"
)

__all__ = [
    "SHADER_GRAPH_AUTHOR_INITIAL_PROMPT",
    "SHADER_GRAPH_AUTHOR_REFINE_PROMPT",
    "shader_graph_author_patch_json_schema",
    "shader_graph_document_json_schema",
]
