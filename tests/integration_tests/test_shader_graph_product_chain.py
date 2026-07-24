"""ShaderGraph 产品候选链路的最小真实 WebGL1 纵向验收."""

from io import BytesIO

import pytest
from langchain_core.messages import AIMessage
from PIL import Image, ImageDraw

from agent.app.contracts.llm import LLMResponse
from agent.app.graphs.png_to_shader_min_graph import build_png_to_shader_min_graph
from agent.app.nodes.png_to_shader_min import MinRendererRegistry
from shaderforge.rendering import PlaywrightWebGL1Renderer
from shaderforge.store import LocalArtifactStore

_KIMI_SHADER_GRAPH = (
    '{"schema_version":"shader_graph_v1","canvas":{"width":192,"height":192,'
    '"background":[1.0,1.0,1.0,1.0]},"layers":[{"id":"layer_shadow",'
    '"shape":{"id":"shadow_ellipse","kind":"ellipse","transform":{"translate":'
    '[0.0,-0.55]},"radii":[0.5,0.1]},"fill":{"kind":"solid","color":'
    '[0.45,0.3,0.4,0.45]}},{"id":"layer_sphere","shape":{"id":"sphere_circle",'
    '"kind":"circle","transform":{"translate":[0.0,0.12]},"radius":0.62},'
    '"fill":{"kind":"radial","center":[-0.18,0.38],"radius":0.95,"inner_color":'
    '[1.0,0.8,0.92,1.0],"outer_color":[0.72,0.18,0.48,1.0]},"effects":'
    '[{"kind":"rim","width":0.035,"softness":0.6,"color":[1.0,0.88,0.96,0.9]}]},'
    '{"id":"layer_highlight","shape":{"id":"highlight_ellipse","kind":"ellipse",'
    '"transform":{"translate":[-0.2,0.37]},"radii":[0.14,0.09]},"fill":'
    '{"kind":"solid","color":[1.0,1.0,1.0,0.85]}}]}'
)


class _KimiCanaryGateway:
    """把本次真实 Kimi Code 最终 JSON 注入生产 LLMGateway 契约."""

    async def ainvoke(self, _messages, _options) -> LLMResponse:
        return LLMResponse(
            message=AIMessage(content=_KIMI_SHADER_GRAPH),
            text=_KIMI_SHADER_GRAPH,
            reasoning_content=None,
            model_ref="kimi-code:canary",
            requested_model_ref="kimi-code:canary",
            latency_ms=1,
        )


def _reference_png(size: int = 64) -> bytes:
    image = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(image)
    margin = round(size * 0.17)
    draw.ellipse(
        (margin, round(size * 0.14), size - margin, round(size * 0.86)),
        fill=(228, 74, 126),
    )
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.anyio
async def test_shader_graph_product_chain_reaches_final_artifacts(tmp_path) -> None:
    registry = MinRendererRegistry(PlaywrightWebGL1Renderer)
    artifacts = LocalArtifactStore(tmp_path)
    graph = build_png_to_shader_min_graph(
        artifact_store=artifacts,
        renderer_registry=registry,
        shader_graph_product=True,
    )
    try:
        result = await graph.ainvoke(
            {
                "project_id": "graph-product-project",
                "run_id": "graph-product-run",
                "image": _reference_png(),
                "content_type": "image/png",
                "render_budget": 6,
                "llm_budget": 0,
                "refine_budget": 0,
                "target_mae": 0.0,
                "target_loss": 0.0,
            }
        )
    finally:
        await registry.close("graph-product-project", "graph-product-run")

    final = result["final_result"]
    assert final["status"] == "completed"
    assert final["renderer_path"] == "compiled_graph_program_cache_v1"
    assert final["scene"]["schema_version"] == "shader_graph_v1"
    assert final["render_count"] == 6
    assert final["current_best_loss"] >= 0.0
    run = artifacts.resolve_run("graph-product-run")
    assert run.read_bytes("final/render.png")
    assert b'"schema_version":"shader_graph_v1"' in run.read_bytes(
        "final/shader-graph.json"
    )
    assert b'"png_to_shader_graph_manifest_v1"' in run.read_bytes("final/manifest.json")


@pytest.mark.anyio
async def test_kimi_shader_graph_initial_author_reaches_real_product_render(
    tmp_path,
) -> None:
    registry = MinRendererRegistry(PlaywrightWebGL1Renderer)
    artifacts = LocalArtifactStore(tmp_path)
    graph = build_png_to_shader_min_graph(
        artifact_store=artifacts,
        renderer_registry=registry,
        gateway=_KimiCanaryGateway(),
        shader_graph_product=True,
    )
    try:
        result = await graph.ainvoke(
            {
                "project_id": "graph-kimi-project",
                "run_id": "graph-kimi-run",
                "image": _reference_png(192),
                "content_type": "image/png",
                "render_budget": 2,
                "llm_budget": 1,
                "refine_budget": 0,
                "target_mae": 1.0,
                "target_loss": 1.0,
            }
        )
    finally:
        await registry.close("graph-kimi-project", "graph-kimi-run")

    assert any(
        item.get("phase") == "author_initial" and item.get("author_source") == "model"
        for item in result["trace"]
    )
    assert result["llm_call_count"] == 1
    assert result["final_result"]["renderer_path"] == (
        "compiled_graph_program_cache_v1"
    )
    assert result["final_result"]["scene"]["schema_version"] == "shader_graph_v1"
