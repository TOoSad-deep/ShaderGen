from __future__ import annotations

from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from agent.app.nodes.png_to_shader_min.node_lab import (
    SCENE_MVP_NODE_LAB_PIPELINE_ID,
    create_scene_mvp_node_provider,
)
from nodelab.http.factory import load_application
from nodelab.http.main import create_app
from nodelab.http.settings import NodeLabServiceSettings
from nodelab.models import (
    LabRunCreateRequest,
    NodeLabError,
    StepExecutionRequest,
)
from nodelab.runner import NodeLabApplication

EXPECTED_NODE_IDS = {
    "initialize_run",
    "perceive_target",
    "author_initial",
    "materialize_shader",
    "render_and_evaluate",
    "decide_after_render",
    "optimize_base",
    "decide_after_base",
    "optimize_feature",
    "decide_after_feature",
    "author_refine",
    "finalize",
}


def _pink_orb_png() -> bytes:
    image = Image.new("RGB", (64, 64), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((10, 10, 54, 54), fill=(235, 75, 125))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _application(tmp_path) -> NodeLabApplication:
    return NodeLabApplication.at_root(
        tmp_path / "node-lab",
        node_provider=create_scene_mvp_node_provider(),
    )


def test_scene_mvp_provider_exposes_current_graph_nodes_and_safe_modes() -> None:
    provider = create_scene_mvp_node_provider()
    descriptors = provider.describe_nodes()
    by_id = {descriptor.node_id: descriptor for descriptor in descriptors}

    assert provider.pipeline_id == SCENE_MVP_NODE_LAB_PIPELINE_ID
    assert set(by_id) == EXPECTED_NODE_IDS
    assert len(descriptors) == 12
    assert by_id["author_initial"].execution_modes == ["deterministic", "real"]
    assert by_id["author_refine"].execution_modes == ["deterministic", "real"]
    assert by_id["author_initial"].requires_model is True
    assert by_id["render_and_evaluate"].requires_browser is True
    assert by_id["initialize_run"].input_examples[0].artifact_inputs == {
        "source_artifact_id": "reference_png"
    }


def test_scene_mvp_provider_declares_artifact_inputs_for_hydratable_nodes() -> None:
    provider = create_scene_mvp_node_provider()
    by_id = {descriptor.node_id: descriptor for descriptor in provider.describe_nodes()}

    for node_id in ("perceive_target", "author_initial", "author_refine"):
        example = by_id[node_id].input_examples[0]
        assert example.inputs.get("source_artifact_id") == "replace-with-reference-artifact-id"
        assert example.artifact_inputs == {"source_artifact_id": "reference_png"}
        assert example.base_step_node_id is not None

    for node_id in ("render_and_evaluate", "optimize_base", "optimize_feature"):
        example = by_id[node_id].input_examples[0]
        assert example.inputs.get("target_rgb_artifact_id") == "replace-with-target-rgb-artifact-id"
        assert example.artifact_inputs == {"target_rgb_artifact_id": "target_rgb_npy"}
        assert example.base_step_node_id is not None

    for node_id in (
        "materialize_shader",
        "decide_after_render",
        "decide_after_base",
        "decide_after_feature",
        "finalize",
    ):
        example = by_id[node_id].input_examples[0]
        assert example.inputs == {}
        assert example.artifact_inputs == {}


def test_scene_mvp_factory_loads_through_standalone_http_service(tmp_path) -> None:
    settings = NodeLabServiceSettings(
        root=tmp_path / "runs",
        batch_root=tmp_path / "batches",
        application_factory=(
            "agent.app.services.node_lab:create_application"
        ),
    )
    application = load_application(settings)
    client = TestClient(create_app(settings, application=application))

    health = client.get("/api/lab/v1/health")
    nodes = client.get("/api/lab/v1/nodes")

    assert health.status_code == 200
    assert health.json()["pipeline_id"] == SCENE_MVP_NODE_LAB_PIPELINE_ID
    assert health.json()["node_count"] == 12
    assert nodes.status_code == 200
    assert {item["node_id"] for item in nodes.json()} == EXPECTED_NODE_IDS


@pytest.mark.anyio
async def test_scene_mvp_provider_hydrates_reference_and_projects_target_rgb(
    tmp_path,
) -> None:
    application = _application(tmp_path)
    run = application.create_run(
        LabRunCreateRequest(project_id="node-lab-scene-mvp")
    )
    reference = application.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="reference_png",
        content_type="image/png",
        data=_pink_orb_png(),
    )
    initialized = await application.execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            node_id="initialize_run",
            execution_mode="deterministic",
            inputs={
                "source_artifact_id": reference.artifact_id,
                "quality_preset": "fast",
            },
        )
    )
    perceived = await application.execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            node_id="perceive_target",
            execution_mode="deterministic",
            base_step_id=initialized.step_id,
        )
    )

    assert initialized.execution_status == "completed"
    assert initialized.output["render_budget"] == 48
    assert perceived.execution_status == "completed"
    assert perceived.output["fallback_shader_graph"]["schema_version"] == (
        "shader_graph_v1"
    )
    assert "target_rgb" not in perceived.output
    target_artifact_id = perceived.output["target_rgb_artifact_id"]
    target_descriptor, target_payload = application.read_artifact(
        run.lab_run_id,
        target_artifact_id,
    )
    assert target_descriptor.kind == "target_rgb_npy"
    assert target_descriptor.content_type == "application/x-npy"
    assert target_payload.startswith(b"\x93NUMPY")


@pytest.mark.anyio
async def test_scene_mvp_route_node_runs_and_real_mode_fails_closed(tmp_path) -> None:
    application = _application(tmp_path)
    run = application.create_run(
        LabRunCreateRequest(
            project_id="node-lab-scene-mvp",
            initial_state={
                "render_count": 1,
                "render_budget": 4,
                "target_loss": 0.1,
                "current_best_loss": 0.2,
            },
        )
    )
    routed = await application.execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            node_id="decide_after_render",
            execution_mode="deterministic",
        )
    )

    assert routed.execution_status == "completed"
    assert routed.output == {
        "next_action": "optimize_base",
        "stop_reason": "continue",
    }
    assert routed.next_action == "optimize_base"

    with pytest.raises(NodeLabError, match="双重开关") as exc_info:
        await application.execute_step(
            StepExecutionRequest(
                lab_run_id=run.lab_run_id,
                node_id="author_initial",
                execution_mode="real",
                allow_model_call=True,
            )
        )
    assert exc_info.value.code == "real_model_not_allowed"
