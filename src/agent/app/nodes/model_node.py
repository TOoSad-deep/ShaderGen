"""基础对话图的模型 Node."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langgraph.runtime import Runtime

from agent.app.config.model_config import SHADER_GEN_MODEL_NAME
from agent.app.contracts.llm import LLMCallOptions, LLMGateway
from agent.app.states.agent_state import Context, State

_MISSING = object()


def make_model_node(gateway: LLMGateway):
    """创建只依赖 Gateway 的基础模型 Node."""

    async def call_model(state: State, runtime: Runtime[Context] | None = None):
        """调用 Gateway 并返回消息 partial State."""
        response = await gateway.ainvoke(
            state["messages"],
            _model_call_options(runtime),
        )
        return {"messages": [response.message]}

    return call_model


def _model_call_options(runtime: Any | None) -> LLMCallOptions:
    context = None if runtime is None else getattr(runtime, "context", None)
    thinking = _context_value(context, "model_thinking")
    capture_reasoning = _context_value(context, "capture_reasoning")
    return LLMCallOptions(
        model_ref=SHADER_GEN_MODEL_NAME,
        thinking="default" if thinking is _MISSING else thinking,
        capture_reasoning=(
            None if capture_reasoning is _MISSING else capture_reasoning
        ),
    )


def _context_value(context: Any, name: str) -> Any:
    if context is None:
        return _MISSING
    if isinstance(context, Mapping):
        return context.get(name, _MISSING)
    return getattr(context, name, _MISSING)
