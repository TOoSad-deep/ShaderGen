from io import BytesIO
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from PIL import Image, ImageDraw

from agent.app.contracts.llm import LLMResponse
from agent.app.nodes.png_to_shader_min import (
    MinRendererRegistry,
    make_shader_graph_nodes,
)
from shaderforge.dsl import (
    ShaderDocument,
    adapt_min_scene_to_shader_graph,
    compile_dsl_shader,
)
from shaderforge.perception import perceive_min_target
from shaderforge.store import LocalArtifactStore


def _pink_orb_png() -> bytes:
    image = Image.new("RGB", (96, 96), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((14, 14, 82, 82), fill=(245, 80, 130))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeGateway:
    def __init__(self, *responses: str | Exception) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def ainvoke(self, _messages, _options):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return LLMResponse(
            message=AIMessage(content=response),
            text=response,
            reasoning_content=None,
            model_ref="fake:min-author",
            requested_model_ref="fake:min-author",
            latency_ms=1,
        )


class _FakeRenderer:
    def __init__(self) -> None:
        self.closed = False

    async def prepare(self, _source, width, height, _uniform_schema):
        return SimpleNamespace(width=width, height=height)

    async def close(self) -> None:
        self.closed = True


def _product_state(image: bytes, *, llm_budget: int) -> dict[str, object]:
    perception = perceive_min_target(image)
    return {
        "project_id": "author-project",
        "run_id": "author-run",
        "image": image,
        "content_type": "image/png",
        "instruction": "保留粉色主体",
        "perception": perception.summary,
        "target_rgb": perception.target_rgb,
        "fallback_shader_graph": perception.fallback_document.model_dump(
            mode="json", by_alias=True
        ),
        "llm_budget": llm_budget,
        "llm_call_count": 0,
        "refine_count": 0,
        "trace": (),
    }


def test_perception_fallback_document_is_direct_shader_graph_output() -> None:
    perception = perceive_min_target(_pink_orb_png())

    document = perception.fallback_document
    assert isinstance(document, ShaderDocument)
    # 与确定性迁移映射保持一致，但产品热路径不再需要自己执行转换.
    assert document == adapt_min_scene_to_shader_graph(perception.fallback_scene)
    compiled = compile_dsl_shader(document)
    assert compiled.fragment_source
    assert document.canvas.width == perception.width
    assert document.canvas.height == perception.height
    layer_ids = [layer.id for layer in document.layers]
    assert layer_ids == ["legacy_shadow_1", "legacy_body"]


@pytest.mark.anyio
async def test_perceive_node_emits_fallback_shader_graph(tmp_path) -> None:
    nodes = make_shader_graph_nodes(
        LocalArtifactStore(tmp_path),
        MinRendererRegistry(_FakeRenderer),  # type: ignore[arg-type]
        _FakeGateway(RuntimeError("must not be called")),
    )
    state = {"image": _pink_orb_png(), "trace": ()}

    update = await nodes["perceive_target"](state)

    document = ShaderDocument.model_validate(update["fallback_shader_graph"])
    assert isinstance(document, ShaderDocument)
    # legacy Builder 兼容键仍然保留.
    assert update["fallback_scene"]
    assert update["scene"] == update["fallback_scene"]


@pytest.mark.anyio
async def test_product_author_initial_uses_fallback_document_without_min_scene(
    tmp_path,
) -> None:
    image = _pink_orb_png()
    state = _product_state(image, llm_budget=0)
    # 产品热路径 state 中不存在任何 MinScene 中间表示.
    assert "scene" not in state
    assert "fallback_scene" not in state
    gateway = _FakeGateway(RuntimeError("must not be called"))
    nodes = make_shader_graph_nodes(
        LocalArtifactStore(tmp_path),
        MinRendererRegistry(_FakeRenderer),  # type: ignore[arg-type]
        gateway,
    )

    update = await nodes["author_initial"](state)

    assert gateway.calls == 0
    assert update["scene"] == state["fallback_shader_graph"]
    assert update["fallback_shader_graph"] == state["fallback_shader_graph"]
    assert update["trace"][-1]["author_source"] == "perception_fallback"


@pytest.mark.anyio
async def test_product_author_initial_model_failure_falls_back_to_document(
    tmp_path,
) -> None:
    image = _pink_orb_png()
    state = _product_state(image, llm_budget=2)
    gateway = _FakeGateway("not-a-shader-document")
    nodes = make_shader_graph_nodes(
        LocalArtifactStore(tmp_path),
        MinRendererRegistry(_FakeRenderer),  # type: ignore[arg-type]
        gateway,
    )

    update = await nodes["author_initial"](state)

    assert update["scene"] == state["fallback_shader_graph"]
    assert update["trace"][-1]["author_source"] == "perception_fallback"
