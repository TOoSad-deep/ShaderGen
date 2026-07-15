from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from agent.app.lab.models import (
    CapabilityExecutionRequest,
    LabRunCreateRequest,
    StepExecutionRequest,
)
from agent.app.lab.runner import NodeLabApplication
from agent.app.services.node_lab import create_node_lab_application
from shaderforge.rendering import CompileResult, RenderResult
from shaderforge.validation import validate_shader

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK = ROOT / "benchmarks/png_to_shader_v1/images"
VALID_SHADER = """precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_image;
uniform vec2 u_resolution;
uniform float u_time;
void main() {
    gl_FragColor = vec4(v_uv, 0.5, 1.0);
}
"""


class FakeRenderer:
    def __init__(self, image: bytes) -> None:
        self.image = image
        self.closed = False

    async def render(
        self, fragment_source: str, width: int, height: int
    ) -> RenderResult:
        return RenderResult(
            success=True,
            image_bytes=self.image,
            width=width,
            height=height,
            compile=CompileResult(
                success=True,
                vertex_log="secret vertex log",
                fragment_log="secret fragment log",
                link_log="secret link log",
                draw_error=None,
                static_validation=validate_shader(fragment_source),
            ),
            console_errors=("secret console error",),
            metadata=None,
            duration_ms=3.0,
        )

    async def close(self) -> None:
        self.closed = True


def _app(tmp_path: Path, image: bytes) -> tuple[NodeLabApplication, list[FakeRenderer]]:
    renderers: list[FakeRenderer] = []

    def factory() -> FakeRenderer:
        renderer = FakeRenderer(image)
        renderers.append(renderer)
        return renderer

    return (
        create_node_lab_application(
            root=tmp_path,
            renderer_factory=factory,
        ),
        renderers,
    )


@pytest.mark.anyio
async def test_image_measure_validate_and_evaluate_capabilities(tmp_path: Path) -> None:
    reference = (BENCHMARK / "solid_circle.png").read_bytes()
    app, _renderers = _app(tmp_path, reference)
    run = app.create_run(LabRunCreateRequest())
    source = app.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="source_image",
        content_type="image/png",
        data=reference,
    )

    normalized = await app.execute_capability(
        CapabilityExecutionRequest(
            lab_run_id=run.lab_run_id,
            capability_id="normalize-target",
            inputs={"source_artifact_id": source.artifact_id},
        )
    )
    normalized_id = normalized.output["normalized_artifact"]["artifact_id"]
    measured = await app.execute_capability(
        CapabilityExecutionRequest(
            lab_run_id=run.lab_run_id,
            capability_id="measure-target",
            inputs={"reference_artifact_id": normalized_id},
        )
    )

    assert normalized.outcome == "success"
    assert measured.output["target_measurements"]["image_width"] == 192
    assert measured.output["target_measurements"]["foreground_confidence"] > 0.85
    assert measured.artifacts[0].kind == "target_measurements"

    shader = app.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="shader_source",
        content_type="application/x-glsl",
        data=VALID_SHADER.encode(),
    )
    valid = await app.execute_capability(
        CapabilityExecutionRequest(
            lab_run_id=run.lab_run_id,
            capability_id="validate-shader",
            inputs={"shader_artifact_id": shader.artifact_id},
        )
    )
    invalid_shader = app.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="shader_source",
        content_type="application/x-glsl",
        data=VALID_SHADER.replace(
            "gl_FragColor = vec4(v_uv, 0.5, 1.0);",
            "gl_FragColor = texture2D(u_image, v_uv);",
        ).encode(),
    )
    invalid = await app.execute_capability(
        CapabilityExecutionRequest(
            lab_run_id=run.lab_run_id,
            capability_id="validate-shader",
            inputs={"shader_artifact_id": invalid_shader.artifact_id},
        )
    )

    assert valid.outcome == "success"
    assert invalid.outcome == "rejected"
    assert invalid.diagnostics["error_codes"] == ["texture_sampling"]

    score = await app.execute_capability(
        CapabilityExecutionRequest(
            lab_run_id=run.lab_run_id,
            capability_id="evaluate-render",
            inputs={
                "reference_artifact_id": source.artifact_id,
                "render_artifact_id": source.artifact_id,
            },
        )
    )
    assert score.output["score"]["total_loss"] == pytest.approx(0.0)
    assert isinstance(score.output["score"]["roi_losses"], dict)


@pytest.mark.anyio
async def test_render_keeps_raw_logs_private_and_closes_renderer(
    tmp_path: Path,
) -> None:
    reference = (BENCHMARK / "solid_circle.png").read_bytes()
    app, renderers = _app(tmp_path, reference)
    run = app.create_run(LabRunCreateRequest())
    shader = app.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="shader_source",
        content_type="application/x-glsl",
        data=VALID_SHADER.encode(),
    )

    response = await app.execute_capability(
        CapabilityExecutionRequest(
            lab_run_id=run.lab_run_id,
            capability_id="render-shader",
            inputs={
                "shader_artifact_id": shader.artifact_id,
                "width": 192,
                "height": 192,
            },
        )
    )

    assert response.outcome == "success"
    assert response.output["render_artifact"]["sha256"]
    assert response.output["render"]["console_error_count"] == 1
    assert "secret" not in str(response.to_dict())
    assert renderers[0].closed is True

    diagnostics_id = response.output["diagnostics_artifact"]["artifact_id"]
    _descriptor, raw = app.read_artifact(run.lab_run_id, diagnostics_id)
    assert json.loads(raw)["fragment_log"] == "secret fragment log"


@pytest.mark.anyio
async def test_capability_rejects_unknown_fields_and_contract_ceiling(
    tmp_path: Path,
) -> None:
    reference = (BENCHMARK / "solid_circle.png").read_bytes()
    app, renderers = _app(tmp_path, reference)
    run = app.create_run(LabRunCreateRequest())
    shader = app.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="shader_source",
        content_type="application/x-glsl",
        data=VALID_SHADER.encode(),
    )

    unknown = await app.execute_capability(
        CapabilityExecutionRequest(
            lab_run_id=run.lab_run_id,
            capability_id="validate-shader",
            inputs={
                "shader_artifact_id": shader.artifact_id,
                "not_declared": True,
            },
        )
    )
    oversized = await app.execute_capability(
        CapabilityExecutionRequest(
            lab_run_id=run.lab_run_id,
            capability_id="render-shader",
            inputs={
                "shader_artifact_id": shader.artifact_id,
                "width": 1025,
                "height": 192,
            },
        )
    )

    assert unknown.execution_status == "failed"
    assert unknown.diagnostics["error"]["code"] == "input_contract_invalid"
    assert oversized.execution_status == "failed"
    assert oversized.diagnostics["error"]["code"] == "input_contract_invalid"
    assert renderers == []


@pytest.mark.anyio
async def test_routing_selector_and_node_adapters_reuse_production_logic(
    tmp_path: Path,
) -> None:
    reference = (BENCHMARK / "solid_circle.png").read_bytes()
    app, _renderers = _app(tmp_path, reference)
    run = app.create_run(LabRunCreateRequest())
    budget = {
        "max_visual_refinements": 2,
        "max_compile_repairs": 2,
        "max_model_calls": 8,
        "max_wall_time_seconds": 300,
    }
    routed = await app.execute_capability(
        CapabilityExecutionRequest(
            lab_run_id=run.lab_run_id,
            capability_id="decide-after-render",
            inputs={"render_status": "success", "budget_policy": budget},
        )
    )
    assert routed.output == {"next_action": "select"}

    score = {
        "metric_version": "basic_oracle_v1",
        "total_loss": 0.2,
        "global_rmse": 0.2,
        "global_mae": 0.2,
        "edge_loss": 0.2,
        "geometry_loss": 0.2,
        "representative_pixel_loss": 0.2,
        "roi_losses": {"subject": 0.2},
        "protected_region_losses": {"center": 0.1},
        "effective_weights": {"global_rmse": 1.0},
        "diagnostics": [],
    }
    candidate = {
        "candidate_id": "candidate-1",
        "parent_candidate_id": None,
        "glsl_sha256": "a" * 64,
        "glsl_ref": "artifact:shader",
        "author_ref": "artifact:author",
        "provenance_ref": "artifact:provenance",
        "compile_ref": "artifact:compile",
        "render_ref": "artifact:render",
        "render_sha256": "b" * 64,
        "metrics_ref": "artifact:metrics",
        "review_ref": None,
        "iteration": 0,
        "changed_problem_domain": "initial_build",
        "prompt_version": "fixture-v1",
        "model_ref": "fixture:none",
        "score_summary": score,
        "hard_constraints_passed": True,
    }
    selected = await app.execute_capability(
        CapabilityExecutionRequest(
            lab_run_id=run.lab_run_id,
            capability_id="select-current-best",
            inputs={"candidate": candidate},
        )
    )
    assert selected.output["decision"]["accepted"] is True

    image = app.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="reference_png",
        content_type="image/png",
        data=reference,
    )
    measured_step = await app.execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            node_id="measure_target",
            execution_mode="deterministic",
            inputs={"reference_artifact_id": image.artifact_id},
        )
    )
    route_step = await app.execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            base_step_id=measured_step.step_id,
            node_id="decide_after_render",
            execution_mode="deterministic",
            inputs={"render_status": "success", "budget_policy": budget},
        )
    )

    assert measured_step.output["target_measurements"]["image_width"] == 192
    assert route_step.output == {"next_action": "select"}
    assert route_step.provenance["implementation"].endswith("decide_after_render")


def _materialized_candidate(
    *,
    candidate_id: str,
    shader_artifact_id: str,
    shader_sha256: str,
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "parent_candidate_id": None,
        "glsl_sha256": shader_sha256,
        "glsl_ref": shader_artifact_id,
        "author_ref": "artifact-author",
        "provenance_ref": "artifact-provenance",
        "compile_ref": None,
        "render_ref": None,
        "render_sha256": None,
        "metrics_ref": None,
        "review_ref": None,
        "iteration": 0,
        "changed_problem_domain": "initial_build",
        "prompt_version": "fixture-v1",
        "model_ref": "fixture:none",
        "score_summary": None,
        "hard_constraints_passed": False,
    }


@pytest.mark.anyio
async def test_production_render_then_select_binds_artifacts_and_preserves_best(
    tmp_path: Path,
) -> None:
    reference = (BENCHMARK / "solid_circle.png").read_bytes()
    app, _renderers = _app(tmp_path, reference)
    run = app.create_run(LabRunCreateRequest())
    reference_artifact = app.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="reference_png",
        content_type="image/png",
        data=reference,
    )
    shader_artifact = app.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="shader_source",
        content_type="application/x-glsl",
        data=VALID_SHADER.encode(),
    )
    candidate = _materialized_candidate(
        candidate_id="candidate-0001",
        shader_artifact_id=shader_artifact.artifact_id,
        shader_sha256=shader_artifact.sha256,
    )

    rendered = await app.execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            node_id="render_and_evaluate",
            execution_mode="deterministic",
            inputs={
                "candidate_record": candidate,
                "candidate_records": [candidate],
                "shader_artifact_id": shader_artifact.artifact_id,
                "glsl_artifact_id": shader_artifact.artifact_id,
                "reference_artifact_id": reference_artifact.artifact_id,
                "target_measurements": {
                    "analysis_width": 192,
                    "analysis_height": 192,
                },
                "events": [],
            },
        )
    )

    assert rendered.outcome == "success"
    assert rendered.output["render_status"] == "success"
    assert rendered.output["static_validation"]["valid"] is True
    assert rendered.output["compile_result"]["success"] is True
    assert "secret" not in json.dumps(rendered.output["compile_result"])
    assert rendered.output["score_breakdown"]["total_loss"] == pytest.approx(0.0)
    completed = rendered.output["candidate_record"]
    assert completed["glsl_sha256"] == sha256(VALID_SHADER.encode()).hexdigest()
    assert completed["render_ref"] == rendered.output["render_artifact"]["artifact_id"]
    assert completed["render_sha256"] == rendered.output["render_artifact"]["sha256"]
    assert (
        completed["metrics_ref"] == rendered.output["metrics_artifact"]["artifact_id"]
    )
    assert completed["score_summary"] == rendered.output["score_breakdown"]
    assert completed["hard_constraints_passed"] is True
    assert rendered.output["candidate_records"] == [completed]

    selected = await app.execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            base_step_id=rendered.step_id,
            node_id="select_current_best",
            execution_mode="deterministic",
            inputs={
                "acceptance_policy": {
                    "min_total_improvement": 0.005,
                    "max_protected_regression": 0.02,
                }
            },
        )
    )

    assert selected.outcome == "success"
    assert selected.output["selection_decision"]["reason"] == "first_valid_candidate"
    assert selected.output["current_best_record"] == completed
    assert selected.output["current_best_id"] == "candidate-0001"
    assert selected.output["no_improvement_count"] == 0

    same_score = {**completed, "candidate_id": "candidate-0002"}
    rejected = await app.execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            base_step_id=selected.step_id,
            node_id="select_current_best",
            execution_mode="deterministic",
            inputs={"candidate_record": same_score},
        )
    )

    assert rejected.outcome == "rejected"
    assert (
        rejected.output["selection_decision"]["reason"]
        == "insufficient_total_improvement"
    )
    assert rejected.output["current_best_record"] == completed
    assert rejected.output["current_best_id"] == "candidate-0001"
    assert rejected.output["no_improvement_count"] == 1


@pytest.mark.anyio
async def test_production_render_rejects_glsl_hash_mismatch_and_static_failure(
    tmp_path: Path,
) -> None:
    reference = (BENCHMARK / "solid_circle.png").read_bytes()
    app, _renderers = _app(tmp_path, reference)
    run = app.create_run(LabRunCreateRequest())
    reference_artifact = app.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="reference_png",
        content_type="image/png",
        data=reference,
    )
    invalid_source = VALID_SHADER.replace(
        "gl_FragColor = vec4(v_uv, 0.5, 1.0);",
        "gl_FragColor = texture2D(u_image, v_uv);",
    )
    shader_artifact = app.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="shader_source",
        content_type="application/x-glsl",
        data=invalid_source.encode(),
    )
    mismatched = _materialized_candidate(
        candidate_id="candidate-bad-hash",
        shader_artifact_id=shader_artifact.artifact_id,
        shader_sha256="0" * 64,
    )
    mismatch = await app.execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            node_id="render_and_evaluate",
            execution_mode="deterministic",
            inputs={
                "candidate_record": mismatched,
                "candidate_records": [mismatched],
                "shader_artifact_id": shader_artifact.artifact_id,
                "glsl_artifact_id": shader_artifact.artifact_id,
                "reference_artifact_id": reference_artifact.artifact_id,
                "target_measurements": {
                    "analysis_width": 192,
                    "analysis_height": 192,
                },
                "width": 192,
                "height": 192,
            },
        )
    )

    assert mismatch.execution_status == "failed"
    assert mismatch.diagnostics["error"]["code"] == "artifact_integrity_failed"

    candidate = _materialized_candidate(
        candidate_id="candidate-static-failure",
        shader_artifact_id=shader_artifact.artifact_id,
        shader_sha256=shader_artifact.sha256,
    )
    failed = await app.execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            node_id="render_and_evaluate",
            execution_mode="deterministic",
            inputs={
                "candidate_record": candidate,
                "candidate_records": [candidate],
                "shader_artifact_id": shader_artifact.artifact_id,
                "glsl_artifact_id": shader_artifact.artifact_id,
                "reference_artifact_id": reference_artifact.artifact_id,
                "target_measurements": {
                    "analysis_width": 192,
                    "analysis_height": 192,
                },
                "width": 192,
                "height": 192,
            },
        )
    )

    assert failed.execution_status == "completed"
    assert failed.outcome == "rejected"
    assert failed.output["render_status"] == "compile_failed"
    assert failed.output["static_validation"]["valid"] is False
    assert failed.output["compile_result"]["success"] is False
    assert failed.output["score_breakdown"] is None
    assert failed.output["candidate_record"]["hard_constraints_passed"] is False
    assert failed.output["candidate_record"]["render_ref"] is None
