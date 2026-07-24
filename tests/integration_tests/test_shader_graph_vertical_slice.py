from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from agent.app.graphs.png_to_shader_min_graph import build_png_to_shader_min_graph
from agent.app.nodes.png_to_shader_min import MinRendererRegistry
from agent.app.nodes.png_to_shader_min.shader_graph_shadow import (
    ShaderGraphShadowRunner,
)
from shaderforge.dsl import compile_dsl_shader, parse_dsl_document
from shaderforge.rendering import (
    GraphProgramKey,
    GraphProgramRegistry,
    PlaywrightWebGL1Renderer,
)
from shaderforge.store import LocalArtifactStore


def _reference_png() -> bytes:
    image = Image.new("RGB", (64, 64), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((10, 10, 54, 54), fill=(235, 75, 125))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.anyio
async def test_shader_graph_compiles_caches_and_renders_in_real_webgl1() -> None:
    document = parse_dsl_document(
        {
            "canvas": {
                "width": 64,
                "height": 64,
                "background": [1.0, 1.0, 1.0, 1.0],
            },
            "layers": [
                {
                    "id": "back",
                    "opacity": 0.75,
                    "shape": {
                        "id": "back_circle",
                        "kind": "circle",
                        "radius": 0.55,
                    },
                    "fill": {
                        "kind": "radial",
                        "center": [-0.15, 0.2],
                        "radius": 0.8,
                        "inner_color": [1.0, 0.85, 0.9, 1.0],
                        "outer_color": [0.8, 0.15, 0.35, 0.9],
                    },
                    "effects": [
                        {
                            "kind": "shadow",
                            "offset": [0.08, -0.08],
                            "blur": 0.06,
                            "spread": 0.0,
                            "color": [0.0, 0.0, 0.0, 0.3],
                        }
                    ],
                },
                {
                    "id": "front",
                    "shape": {
                        "id": "front_cut",
                        "kind": "subtract",
                        "base": {
                            "id": "box",
                            "kind": "rounded_box",
                            "half_size": [0.36, 0.24],
                            "corner_radius": 0.08,
                            "transform": {
                                "translate": [0.12, 0.04],
                                "rotation": [0.984807753, 0.173648178],
                            },
                        },
                        "cut": {
                            "id": "hole",
                            "kind": "ellipse",
                            "radii": [0.13, 0.08],
                            "transform": {"translate": [0.12, 0.04]},
                        },
                    },
                    "fill": {
                        "kind": "linear",
                        "from": [-0.3, -0.2],
                        "to": [0.4, 0.3],
                        "start_color": [0.2, 0.5, 1.0, 0.9],
                        "end_color": [0.8, 0.9, 1.0, 0.9],
                    },
                    "effects": [
                        {
                            "kind": "rim",
                            "width": 0.05,
                            "softness": 0.02,
                            "color": [1.0, 1.0, 1.0, 0.7],
                        }
                    ],
                },
            ],
        }
    )
    compiled = compile_dsl_shader(document)
    renderer = PlaywrightWebGL1Renderer()
    registry = GraphProgramRegistry(renderer, max_programs=2, max_compiles=2)
    key = GraphProgramKey(
        compiler_version=compiled.compiler_version,
        topology_sha256=compiled.topology_sha256,
        active_parameter_manifest_sha256=compiled.parameter_manifest_sha256,
        baked_parameter_sha256=compiled.glsl_sha256,
        width=document.canvas.width,
        height=document.canvas.height,
    )
    try:
        prepared = await registry.get_or_prepare(
            key,
            compiled.fragment_source,
            compiled.uniform_schema,
        )
        reused = await registry.get_or_prepare(
            key,
            compiled.fragment_source,
            compiled.uniform_schema,
        )
        assert reused is prepared
        rendered = await prepared.render_uniforms(
            compiled.uniform_values,
            capture_png=True,
        )
        assert rendered.success is True
        assert rendered.rgb_bytes is not None
        assert len(rendered.rgb_bytes) == 64 * 64 * 3
        assert rendered.image_bytes is not None
        assert registry.summary()["compile_count"] == 1
        assert registry.summary()["cache_hit_count"] == 1
    finally:
        await registry.close_all()
        await renderer.close()


@pytest.mark.anyio
async def test_scene_mvp_finalize_runs_real_shader_graph_shadow(tmp_path) -> None:
    registry = MinRendererRegistry(PlaywrightWebGL1Renderer)
    artifacts = LocalArtifactStore(tmp_path)
    graph = build_png_to_shader_min_graph(
        artifact_store=artifacts,
        renderer_registry=registry,
        shader_graph_shadow=ShaderGraphShadowRunner(PlaywrightWebGL1Renderer),
    )
    try:
        result = await graph.ainvoke(
            {
                "project_id": "graph-shadow-project",
                "run_id": "graph-shadow-run",
                "image": _reference_png(),
                "content_type": "image/png",
                "render_budget": 1,
                "llm_budget": 0,
                "refine_budget": 0,
                "target_mae": 1.0,
                "target_loss": 1.0,
            }
        )
    finally:
        await registry.close("graph-shadow-project", "graph-shadow-run")

    shadow = result["final_result"]["shader_graph_shadow"]
    assert shadow["status"] == "rendered"
    assert shadow["renderer_path"] == "compiled_graph_program_cache_v1"
    assert shadow["layer_count"] == 2
    assert shadow["compile_count"] == 1
    assert shadow["shader_graph"]["layers"][0]["id"] == "legacy_shadow_1"
    assert shadow["shader_graph"]["layers"][1]["id"] == "legacy_body"
    run = artifacts.resolve_run("graph-shadow-run")
    assert run.read_bytes("final/shader-graph-shadow.png")
    assert run.read_bytes("final/shader-graph-shadow.glsl")
    assert run.read_bytes("final/shader-graph.json")
