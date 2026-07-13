import pytest
from langchain_core.messages import AIMessage

from agent.app.config.model_config import NodeModelConfig
from agent.app.contracts.llm import LLMCallOptions, LLMResponse
from agent.app.graphs.shader_generation_graph import build_shader_generation_graph
from agent.app.nodes.generate_glsl_node import make_generate_glsl_node
from agent.app.nodes.review_render_node import make_review_render_node


class FakeGateway:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = iter(responses)
        self.calls = []

    async def ainvoke(self, messages, options):
        self.calls.append((messages, options))
        return next(self._responses)


def llm_response(
    text: str,
    *,
    model_ref: str,
    reasoning_content: str | None = None,
    latency_ms: int = 7,
) -> LLMResponse:
    message = AIMessage(
        content=text,
        additional_kwargs=(
            {}
            if reasoning_content is None
            else {"reasoning_content": reasoning_content}
        ),
    )
    return LLMResponse(
        message=message,
        text=text,
        reasoning_content=reasoning_content,
        model_ref=model_ref,
        latency_ms=latency_ms,
    )


@pytest.mark.anyio
async def test_generate_node_uses_gateway_model_identity() -> None:
    model_ref = "deepseek:deepseek-chat"
    gateway = FakeGateway([llm_response("void main() {}", model_ref=model_ref)])
    config = NodeModelConfig(
        call=LLMCallOptions(model_ref=model_ref, thinking="off"),
        print_reasoning=False,
    )
    node = make_generate_glsl_node(gateway, config)

    result = await node({"image": b"image", "content_type": "image/png"})

    _, options = gateway.calls[0]
    assert options == config.call
    assert result["glsl"] == "void main() {}"
    assert result["glsl_model_name"] == model_ref
    assert result["vision_model_name"] == model_ref
    assert result["model_calls"][0]["model"] == model_ref


@pytest.mark.anyio
async def test_generate_node_logs_reasoning_when_enabled(caplog) -> None:
    model_ref = "dashscope:qwen3.7-plus"
    gateway = FakeGateway(
        [
            llm_response(
                "void main() {}",
                model_ref=model_ref,
                reasoning_content="生成推理",
            )
        ]
    )
    config = NodeModelConfig(
        call=LLMCallOptions(model_ref=model_ref, capture_reasoning=True),
        print_reasoning=True,
    )
    node = make_generate_glsl_node(gateway, config)
    caplog.set_level("INFO", logger="agent.model")

    result = await node({"image": b"image", "content_type": "image/png"})

    assert result["model_calls"][0]["reasoning_content"] == "生成推理"
    assert "生成推理" in caplog.text


@pytest.mark.anyio
async def test_review_node_maps_unified_gateway_response(caplog) -> None:
    model_ref = "dashscope:qwen3.7-plus"
    gateway = FakeGateway(
        [
            llm_response(
                '{"evaluation":"接近原图。","suggestions":["保留"]}',
                model_ref=model_ref,
                reasoning_content="评审推理",
            )
        ]
    )
    config = NodeModelConfig(
        call=LLMCallOptions(model_ref=model_ref, capture_reasoning=True),
        print_reasoning=False,
    )
    node = make_review_render_node(gateway, config)
    caplog.set_level("INFO", logger="agent.model")

    result = await node(
        {
            "image": b"original",
            "content_type": "image/png",
            "rendered_image": b"rendered",
            "rendered_content_type": "image/png",
            "glsl": "void main() {}",
        }
    )

    assert result["evaluation"] == "接近原图。"
    assert result["suggestions"] == ("保留",)
    assert result["review_model_name"] == model_ref
    assert result["model_calls"][0]["reasoning_content"] == "评审推理"
    assert "评审推理" not in caplog.text


@pytest.mark.anyio
async def test_shader_graph_builder_injects_one_gateway_into_both_nodes() -> None:
    model_ref = "dashscope:qwen3.7-plus"
    gateway = FakeGateway(
        [
            llm_response("void main() {}", model_ref=model_ref),
            llm_response(
                '{"evaluation":"接近原图。","suggestions":[]}',
                model_ref=model_ref,
            ),
        ]
    )
    graph = build_shader_generation_graph(gateway)

    result = await graph.ainvoke(
        {
            "operation": "generate",
            "project_id": "project-graph",
            "image": b"original",
            "content_type": "image/png",
        }
    )
    review_result = await graph.ainvoke(
        {
            "operation": "review",
            "project_id": "project-graph",
            "image": b"original",
            "content_type": "image/png",
            "rendered_image": b"rendered",
            "rendered_content_type": "image/png",
            "glsl": result["glsl"],
            "last_glsl_sha256": "0" * 64,
            "run_id": "run-review",
        }
    )

    assert len(gateway.calls) == 2
    assert result["glsl"] == "void main() {}"
    assert review_result["evaluation"] == "接近原图。"
