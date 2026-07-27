from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from agent.app.nodes.png_to_shader_min.node_lab import (
    create_scene_mvp_node_provider,
)
from nodelab.models import LabRunCreateRequest, StepExecutionRequest
from nodelab.runner import NodeLabApplication


def _reference_png() -> bytes:
    image = Image.new("RGB", (64, 64), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((10, 10, 54, 54), fill=(235, 75, 125))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _PreparedRenderer:
    def __init__(self) -> None:
        self.width = 0
        self.height = 0
        self.prepare_duration_ms = 1.0
        self.render_durations_ms: tuple[float, ...] = ()
        self.closed = False

    @property
    def render_count(self) -> int:
        return len(self.render_durations_ms)

    async def render_uniforms(self, _values, *, capture_png=False):
        self.render_durations_ms = (*self.render_durations_ms, 0.5)
        rgb = Image.new("RGB", (self.width, self.height), "white").tobytes()
        return SimpleNamespace(
            success=True,
            rgb_bytes=rgb,
            image_bytes=None,
            draw_error=None,
        )

    async def close(self) -> None:
        self.closed = True


class _Renderer:
    instances: list[_Renderer] = []

    def __init__(self) -> None:
        self.prepared = _PreparedRenderer()
        self.closed = False
        self.instances.append(self)

    async def prepare(self, _source, width, height, _uniform_schema):
        self.prepared.width = width
        self.prepared.height = height
        return self.prepared

    async def close(self) -> None:
        self.closed = True


@pytest.mark.anyio
async def test_scene_mvp_nodes_run_through_node_lab_to_final_artifacts(
    tmp_path,
) -> None:
    _Renderer.instances.clear()
    application = NodeLabApplication.at_root(
        tmp_path / "node-lab",
        node_provider=create_scene_mvp_node_provider(
            renderer_factory=_Renderer,  # type: ignore[arg-type]
        ),
    )
    run = application.create_run(
        LabRunCreateRequest(project_id="node-lab-scene-mvp")
    )
    reference = application.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="reference_png",
        content_type="image/png",
        data=_reference_png(),
    )

    async def execute(
        node_id: str,
        *,
        base_step_id: str | None,
        inputs: dict[str, object] | None = None,
    ):
        response = await application.execute_step(
            StepExecutionRequest(
                lab_run_id=run.lab_run_id,
                node_id=node_id,
                execution_mode="deterministic",
                base_step_id=base_step_id,
                inputs=inputs or {},
            )
        )
        assert response.execution_status == "completed", response.diagnostics
        return response

    initialized = await execute(
        "initialize_run",
        base_step_id=None,
        inputs={
            "source_artifact_id": reference.artifact_id,
            "quality_preset": "fast",
        },
    )
    perceived = await execute(
        "perceive_target",
        base_step_id=initialized.step_id,
    )
    authored = await execute(
        "author_initial",
        base_step_id=perceived.step_id,
    )
    materialized = await execute(
        "materialize_shader",
        base_step_id=authored.step_id,
    )
    rendered = await execute(
        "render_and_evaluate",
        base_step_id=materialized.step_id,
    )
    routed_render = await execute(
        "decide_after_render",
        base_step_id=rendered.step_id,
    )
    optimized_base = await execute(
        "optimize_base",
        base_step_id=routed_render.step_id,
    )
    routed_base = await execute(
        "decide_after_base",
        base_step_id=optimized_base.step_id,
    )
    optimized_feature = await execute(
        "optimize_feature",
        base_step_id=routed_base.step_id,
    )
    routed_feature = await execute(
        "decide_after_feature",
        base_step_id=optimized_feature.step_id,
    )
    refined = await execute(
        "author_refine",
        base_step_id=routed_feature.step_id,
    )
    rematerialized = await execute(
        "materialize_shader",
        base_step_id=refined.step_id,
    )
    rerendered = await execute(
        "render_and_evaluate",
        base_step_id=rematerialized.step_id,
    )
    finalized = await execute(
        "finalize",
        base_step_id=rerendered.step_id,
    )

    assert rendered.output["current_best"]["schema_version"] == (
        "scene_mvp_node_lab_snapshot_v1"
    )
    assert "current_render" not in rendered.output
    assert rendered.output["current_render_artifact_id"]
    assert refined.output["refine_branch_resolved"] is True
    assert finalized.output["status"] == "completed"
    assert finalized.output["final_result"]["scene"]["schema_version"] == (
        "shader_graph_v1"
    )
    artifact_kinds = {
        descriptor.kind for descriptor in application.list_artifacts(run.lab_run_id)
    }
    assert {
        "reference_png",
        "target_rgb_npy",
        "candidate_render_png",
        "final_glsl",
        "final_render_png",
        "final_shader_graph",
        "final_metrics",
        "final_manifest",
    } <= artifact_kinds
    assert _Renderer.instances
    assert all(renderer.closed for renderer in _Renderer.instances)
