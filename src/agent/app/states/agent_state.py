"""ShaderGen 图的状态定义."""

from typing import Annotated, Any

from langgraph.channels import UntrackedValue
from langgraph.graph import MessagesState
from typing_extensions import TypedDict

from agent.app.contracts.llm import ThinkingMode


class Context(TypedDict, total=False):
    """智能体运行时上下文参数."""

    model_thinking: ThinkingMode
    capture_reasoning: bool


class State(MessagesState):
    """智能体消息状态."""


class ShaderPipelineState(TypedDict, total=False):
    """图片到 GLSL 及渲染评审流水线状态."""

    project_id: str
    phase: str
    iteration: int
    last_glsl_sha256: str
    last_generation_model: str
    last_generated_at: str
    last_review_summary: str
    last_suggestions: tuple[str, ...]

    operation: Annotated[str, UntrackedValue]
    image: Annotated[bytes, UntrackedValue]
    content_type: Annotated[str, UntrackedValue]
    rendered_image: Annotated[bytes, UntrackedValue]
    rendered_content_type: Annotated[str, UntrackedValue]
    glsl: Annotated[str, UntrackedValue]
    context_pack: Annotated[dict[str, Any], UntrackedValue]
    selected_memory_ids: Annotated[tuple[str, ...], UntrackedValue]
    memory_status: Annotated[str, UntrackedValue]
    run_id: Annotated[str, UntrackedValue]
    glsl_model_name: Annotated[str, UntrackedValue]
    vision_model_name: Annotated[str, UntrackedValue]
    evaluation: Annotated[str, UntrackedValue]
    suggestions: Annotated[tuple[str, ...], UntrackedValue]
    review_model_name: Annotated[str, UntrackedValue]
    model_calls: Annotated[tuple[dict[str, Any], ...], UntrackedValue]
    events: Annotated[tuple[dict[str, Any], ...], UntrackedValue]
    logs: Annotated[tuple[dict[str, Any], ...], UntrackedValue]
