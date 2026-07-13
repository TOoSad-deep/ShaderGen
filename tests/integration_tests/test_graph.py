import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.app.config.model_config import SHADER_GEN_MODEL_NAME
from agent.app.contracts.llm import LLMResponse
from agent.app.graphs.main_graph import build_main_graph

pytestmark = pytest.mark.anyio


@pytest.mark.langsmith
async def test_agent_simple_passthrough() -> None:
    output_message = AIMessage(content="你好，我是 ShaderGen。")

    class FakeGateway:
        async def ainvoke(self, messages, options):
            assert len(messages) == 1
            assert isinstance(messages[0], HumanMessage)
            assert messages[0].content == "你好"
            assert options.model_ref == SHADER_GEN_MODEL_NAME
            return LLMResponse(
                message=output_message,
                text=output_message.text,
                reasoning_content=None,
                model_ref=options.model_ref,
                latency_ms=1,
            )

    graph = build_main_graph(FakeGateway())

    res = await graph.ainvoke({"messages": [HumanMessage(content="你好")]})

    assert res["messages"][-1] == output_message
