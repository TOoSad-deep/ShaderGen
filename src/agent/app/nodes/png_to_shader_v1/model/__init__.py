"""PNG-to-Shader V1 模型 Node、预算包装与结构化输出入口."""

from .bounded import make_bounded_model_node
from .shader_author import (
    AUTHOR_PROMPTS,
    SHADER_AUTHOR_MODEL_CONFIG,
    make_shader_author_compile_repair_node,
    make_shader_author_initial_node,
    make_shader_author_node,
    make_shader_author_visual_refine_node,
)
from .structured_output import (
    STRUCTURED_OUTPUT_REPAIR_PROMPT,
    StructuredCallResult,
    StructuredOutputExhaustedError,
    StructuredOutputInvocationError,
    invoke_structured_output,
)
from .visual_analysis import (
    VISUAL_ANALYSIS_MODEL_CONFIG,
    VISUAL_ANALYSIS_PROMPT,
    make_visual_analysis_node,
)
from .visual_critic import (
    VISUAL_CRITIC_MODEL_CONFIG,
    VISUAL_CRITIC_PROMPT,
    make_visual_critic_node,
)

__all__ = [
    "AUTHOR_PROMPTS",
    "SHADER_AUTHOR_MODEL_CONFIG",
    "STRUCTURED_OUTPUT_REPAIR_PROMPT",
    "StructuredCallResult",
    "StructuredOutputExhaustedError",
    "StructuredOutputInvocationError",
    "VISUAL_ANALYSIS_MODEL_CONFIG",
    "VISUAL_ANALYSIS_PROMPT",
    "VISUAL_CRITIC_MODEL_CONFIG",
    "VISUAL_CRITIC_PROMPT",
    "invoke_structured_output",
    "make_bounded_model_node",
    "make_shader_author_compile_repair_node",
    "make_shader_author_initial_node",
    "make_shader_author_node",
    "make_shader_author_visual_refine_node",
    "make_visual_analysis_node",
    "make_visual_critic_node",
]
