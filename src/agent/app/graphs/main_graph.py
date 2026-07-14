"""构建 ShaderGen 主图."""

from typing import Any, cast

from langgraph.graph import START, StateGraph

from agent.app.contracts.llm import LLMGateway
from agent.app.llms.gateway import LangChainLLMGateway
from agent.app.nodes.model_node import make_model_node
from agent.app.states.agent_state import Context, State


# 图（基础对话）：
#
#   +-------+     +------------+     +----------------+
#   | START | --> | call_model | --> | END（隐式终止） |
#   +-------+     +------------+     +----------------+
#
# 此图没有条件路由；`call_model` 完成后，LangGraph 自动结束执行。
def build_main_graph(gateway: LLMGateway) -> Any:
    """用指定 Gateway 构建基础对话图."""
    call_model = make_model_node(gateway)
    return (
        cast(Any, StateGraph(State, context_schema=Context))
        .add_node("call_model", call_model)
        .add_edge(START, "call_model")
        .compile(name="ShaderGen")
    )


_default_gateway = LangChainLLMGateway()
graph = build_main_graph(_default_gateway)
