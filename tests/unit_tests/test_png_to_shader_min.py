from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from agent.app.graphs.png_to_shader_min_graph import build_png_to_shader_min_graph
from agent.app.graphs.png_to_shader_min_routing import (
    route_after_base,
    route_after_feature,
    route_after_render,
)
from agent.app.nodes.png_to_shader_min import MinRendererRegistry
from shaderforge.generation import bake_min_uniforms, materialize_min_shader
from shaderforge.perception import perceive_min_target
from shaderforge.scene import AddFeaturePatch, Feature, MinScene, apply_scene_patch
from shaderforge.store import LocalArtifactStore
from shaderforge.validation import validate_shader


def _pink_orb_png() -> bytes:
    image = Image.new("RGB", (96, 96), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((14, 14, 82, 82), fill=(245, 80, 130))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeRenderer:
    closed = False

    async def render(self, _source: str, width: int, height: int):
        image = Image.new("RGB", (width, height), "white")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return SimpleNamespace(
            success=True,
            image_bytes=buffer.getvalue(),
            compile=SimpleNamespace(draw_error=None),
        )

    async def close(self) -> None:
        self.closed = True


def test_scene_template_and_patch_are_strict_and_valid_webgl1() -> None:
    perception = perceive_min_target(_pink_orb_png())
    scene = perception.fallback_scene
    patched = apply_scene_patch(
        scene,
        AddFeaturePatch(
            op="add_feature",
            feature=Feature(id="edge", type="edge_line", intensity=0.1),
        ),
    )

    assert isinstance(MinScene.model_validate(patched.model_dump()), MinScene)
    materialized = materialize_min_shader(patched)
    source = bake_min_uniforms(materialized)
    assert validate_shader(source).valid
    assert "texture2D(" not in source
    assert "iResolution.xy" in materialized.shadertoy_source
    assert "uniform vec3 u_bg" not in materialized.shadertoy_source


def test_min_routing_rejects_unknown_action() -> None:
    assert route_after_render({"next_action": "optimize_base"}) == "optimize_base"
    assert route_after_base({"next_action": "author_refine"}) == "author_refine"
    assert route_after_feature({"next_action": "finalize"}) == "finalize"


@pytest.mark.anyio
async def test_min_graph_writes_trace_and_final_artifacts(tmp_path) -> None:
    artifacts = LocalArtifactStore(tmp_path)
    registry = MinRendererRegistry(_FakeRenderer)  # type: ignore[arg-type]
    graph = build_png_to_shader_min_graph(
        artifact_store=artifacts,
        renderer_registry=registry,
    )

    result = await graph.ainvoke(
        {
            "project_id": "project-1",
            "run_id": "run-1",
            "image": _pink_orb_png(),
            "content_type": "image/png",
            "render_budget": 1,
            "llm_budget": 0,
            "refine_budget": 0,
            "target_mae": 1.0,
        }
    )

    assert result["status"] == "completed"
    assert result["render_count"] == 1
    assert [item["phase"] for item in result["trace"]][-1] == "finalize"
    run = artifacts.resolve_run("run-1")
    assert run.read_bytes("final/render.png")
    assert b'"schema_version":"png_to_shader_min_manifest_v1"' in run.read_bytes(
        "final/manifest.json"
    )
