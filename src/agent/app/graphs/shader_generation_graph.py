"""Shader 生成、渲染评审和 Memory 晋升图."""

from typing import Any, cast

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.store.memory import InMemoryStore

from agent.app.contracts.llm import LLMGateway
from agent.app.llms.gateway import LangChainLLMGateway
from agent.app.nodes.generate_glsl_node import (
    GENERATE_GLSL_MODEL_CONFIG,
    make_generate_glsl_node,
)
from agent.app.nodes.prepare_context_node import make_prepare_context_node
from agent.app.nodes.promote_memory_node import promote_memory
from agent.app.nodes.review_render_node import (
    REVIEW_RENDER_MODEL_CONFIG,
    make_review_render_node,
)
from agent.app.states.agent_state import ShaderPipelineState


def _route_operation(state: ShaderPipelineState) -> str:
    operation = state.get("operation")
    if operation not in {"generate", "review"}:
        raise ValueError("operation 必须是 generate 或 review。")
    return operation


# 图（生成与评审）：
#
#   +-------+     +-----------------+    operation=generate    +---------------+
#   | START | --> | prepare_context | -----------------------> | generate_glsl | --> END
#   +-------+     +-----------------+                          +---------------+
#                         |
#                         | operation=review
#                         v
#                  +---------------+     +----------------+     +-----+
#                  | review_render | --> | promote_memory | --> | END |
#                  +---------------+     +----------------+     +-----+
#
# `_route_operation` 仅接受 generate / review；其他值会在路由前失败，避免进入
# 未定义分支。
def build_shader_generation_graph(
    gateway: LLMGateway,
    *,
    checkpointer: Any = None,
    store: Any = None,
) -> Any:
    """用指定 Gateway 构建 Shader 生成与评审图."""
    return (
        cast(Any, StateGraph(ShaderPipelineState))
        .add_node("prepare_context", make_prepare_context_node())
        .add_node(
            "generate_glsl",
            make_generate_glsl_node(gateway, GENERATE_GLSL_MODEL_CONFIG),
        )
        .add_node(
            "review_render",
            make_review_render_node(gateway, REVIEW_RENDER_MODEL_CONFIG),
        )
        .add_node("promote_memory", promote_memory)
        .add_edge(START, "prepare_context")
        .add_conditional_edges(
            "prepare_context",
            _route_operation,
            {"generate": "generate_glsl", "review": "review_render"},
        )
        .add_edge("generate_glsl", END)
        .add_edge("review_render", "promote_memory")
        .add_edge("promote_memory", END)
        .compile(
            checkpointer=checkpointer,
            store=store,
            name="ShaderGeneration",
        )
    )


_default_gateway = LangChainLLMGateway()
shader_generation_checkpointer = InMemorySaver()
shader_generation_store = InMemoryStore()


def build_default_shader_generation_graph(
    *,
    checkpointer: Any,
    store: Any,
) -> Any:
    """使用默认真实 Gateway 和外部 persistence 构建 Shader 图."""
    return build_shader_generation_graph(
        _default_gateway,
        checkpointer=checkpointer,
        store=store,
    )


shader_generation_graph = build_shader_generation_graph(
    _default_gateway,
    checkpointer=shader_generation_checkpointer,
    store=shader_generation_store,
)
