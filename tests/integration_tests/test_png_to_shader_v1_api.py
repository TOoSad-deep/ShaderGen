from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from agent.app.services.png_to_shader_v1 import PngToShaderV1Service
from backend.app.main import app
from shaderforge.store import LocalArtifactStore


def score() -> dict:
    return {
        "metric_version": "basic_oracle_v1",
        "total_loss": 0.1,
        "global_rmse": 0.08,
        "global_mae": 0.07,
        "edge_loss": 0.12,
        "geometry_loss": 0.09,
        "representative_pixel_loss": 0.08,
        "roi_losses": {},
        "protected_region_losses": {},
        "effective_weights": {"global_rmse": 0.35},
        "diagnostics": [],
    }


def policy_evidence(state: dict) -> dict:
    return {
        "runtime_policy_schema_version": state[
            "runtime_policy_schema_version"
        ],
        "runtime_policy_sha256": state["runtime_policy_sha256"],
        "budget_policy": state["budget_policy"],
        "acceptance_policy": state["acceptance_policy"],
    }


class ArtifactGraph:
    def __init__(self, artifacts: LocalArtifactStore) -> None:
        self.artifacts = artifacts

    async def ainvoke(self, state, config):
        assert config["configurable"]["thread_id"].startswith("png-to-shader-v1:")
        run = self.artifacts.register_run(state["project_id"], state["run_id"])
        run.write_bytes("final/render.png", b"server-png", content_type="image/png")
        run.write_json("final/metrics.json", score())
        run.write_json(
            "final/manifest.json",
            {
                "project_id": state["project_id"],
                "run_id": state["run_id"],
                **policy_evidence(state),
            },
        )
        return {
            "memory_status": "ephemeral",
            "final_result": {
                "success": True,
                "candidate_id": "candidate-0001",
                "glsl": "precision mediump float; void main(){gl_FragColor=vec4(1.0);}",
                "score_breakdown": score(),
                "stop_reason": "quality_threshold_met",
                "visual_refinement_count": 0,
                "render_width": 32,
                "render_height": 24,
                **policy_evidence(state),
            },
            "events": (
                {
                    "stage": "selection",
                    "event_type": "current_best_updated",
                    "payload": {"candidate_id": "candidate-0001"},
                },
            ),
            "model_calls": (),
            "logs": (),
        }


class UnscoredArtifactGraph:
    """模拟已通过 WebGL 硬门禁、但 evaluator 不可用的降级终态."""

    def __init__(self, artifacts: LocalArtifactStore) -> None:
        self.artifacts = artifacts

    async def ainvoke(self, state, config):
        run = self.artifacts.register_run(state["project_id"], state["run_id"])
        run.write_bytes("final/render.png", b"fallback-png", content_type="image/png")
        run.write_json(
            "final/manifest.json",
            {
                "project_id": state["project_id"],
                "run_id": state["run_id"],
                "score_breakdown": None,
                "metrics_ref": None,
                **policy_evidence(state),
            },
        )
        return {
            "memory_status": "ephemeral",
            "final_result": {
                "success": True,
                "candidate_id": "candidate-0001",
                "glsl": "precision mediump float; void main(){gl_FragColor=vec4(1.0);}",
                "score_breakdown": None,
                "metrics_ref": None,
                "unscored_fallback": True,
                "stop_reason": "completed_with_best_effort",
                "visual_refinement_count": 0,
                "render_width": 32,
                "render_height": 24,
                **policy_evidence(state),
            },
            "events": (
                {
                    "stage": "evaluate",
                    "event_type": "evaluation_failed",
                    "payload": {"error_type": "EvaluatorUnavailableError"},
                },
            ),
            "model_calls": (),
            "logs": (),
        }


def test_api_runs_agent_service_and_serves_only_final_artifact(tmp_path: Path) -> None:
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    service = PngToShaderV1Service(
        ArtifactGraph(artifacts),
        InMemorySaver(),
        InMemoryStore(),
        artifacts,
        "ephemeral",
    )
    project_id = uuid4()
    previous = getattr(app.state, "png_to_shader_v1_service", None)
    app.state.png_to_shader_v1_service = service
    try:
        client = TestClient(app)
        generated = client.post(
            "/api/shader/generate",
            files={"file": ("target.png", b"target", "image/png")},
            data={
                "project_id": str(project_id),
                "generation_mode": "procedural_v1",
                "quality_preset": "ultra",
            },
        )
        render = client.get(generated.json()["final_render_url"])
        private = client.get(
            f"/api/shader/runs/{generated.json()['run_id']}/artifacts/shader-source"
        )
    finally:
        app.state.png_to_shader_v1_service = previous

    assert generated.status_code == 200
    assert generated.json()["project_id"] == str(project_id)
    assert generated.json()["stop_reason"] == "quality_threshold_met"
    assert generated.json()["quality_preset"] == "ultra"
    assert generated.json()["unscored_fallback"] is False
    assert render.status_code == 200
    assert render.content == b"server-png"
    assert private.status_code == 404


def test_api_returns_unscored_webgl_candidate_without_fake_metrics(
    tmp_path: Path,
) -> None:
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    service = PngToShaderV1Service(
        UnscoredArtifactGraph(artifacts),
        InMemorySaver(),
        InMemoryStore(),
        artifacts,
        "ephemeral",
    )
    project_id = uuid4()
    previous = getattr(app.state, "png_to_shader_v1_service", None)
    app.state.png_to_shader_v1_service = service
    try:
        client = TestClient(app)
        generated = client.post(
            "/api/shader/generate",
            files={"file": ("target.png", b"target", "image/png")},
            data={
                "project_id": str(project_id),
                "generation_mode": "procedural_v1",
                "quality_preset": "fast",
            },
        )
        payload = generated.json()
        render = client.get(payload["final_render_url"])
        missing_metrics = client.get(
            f"/api/shader/runs/{payload['run_id']}/artifacts/metrics"
        )
    finally:
        app.state.png_to_shader_v1_service = previous

    assert generated.status_code == 200
    assert payload["stop_reason"] == "completed_with_best_effort"
    assert payload["best_candidate_id"] == "candidate-0001"
    assert "gl_FragColor" in payload["glsl"]
    assert payload["score"] is None
    assert payload["metrics_url"] is None
    assert payload["unscored_fallback"] is True
    assert payload["final_render_url"].endswith("/artifacts/final-render")
    assert render.status_code == 200
    assert render.content == b"fallback-png"
    assert missing_metrics.status_code == 404
