"""构建 ShaderGen 主图."""

from langgraph.graph import START, StateGraph

from agent.app.contracts.llm import LLMGateway
from agent.app.llms.gateway import LangChainLLMGateway
from agent.app.nodes.model_node import make_model_node
from agent.app.states.agent_state import Context, State


def build_main_graph(gateway: LLMGateway):
    """用指定 Gateway 构建基础对话图."""
    call_model = make_model_node(gateway)
    return (
        StateGraph(State, context_schema=Context)
        .add_node("call_model", call_model)
        .add_edge(START, "call_model")
        .compile(name="ShaderGen")
    )


_default_gateway = LangChainLLMGateway()
graph = build_main_graph(_default_gateway)
