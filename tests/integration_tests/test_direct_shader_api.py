from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image

from backend.app.core.settings import BackendSettings
from backend.app.main import create_app


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), (64, 128, 192)).save(buffer, "PNG")
    return buffer.getvalue()


class _Runtime:
    async def generate(self, image, content_type, **kwargs):
        return SimpleNamespace(
            project_id=kwargs["project_id"],
            run_id=kwargs["run_id"],
            glsl="precision mediump float; void main(){gl_FragColor=vec4(1.0);}",
            render_width=8,
            render_height=8,
            status="completed",
            stop_reason="direct_attempt_completed",
            quality_preset=kwargs["quality_preset"],
            current_best_mae=0.01,
            current_best_loss=0.02,
            metric_breakdown={"global_mae": 0.01},
            template_version="a" * 64,
            render_count=1,
            render_budget=8,
            llm_call_count=2,
            llm_budget=10,
            refine_budget=2,
            config_fingerprint="b" * 64,
            report_schema_version="direct_glsl_attempt_result_v1",
            renderer_path="direct_program_spec_v1",
            target_mae=0.06,
            target_loss=0.08,
            target_reached=True,
            trace=({"phase": "direct_glsl", "status": "completed"},),
            engine="direct_glsl_layerplan_v1",
            representation="shader_program_spec_v1",
            engine_run={
                "selected_attempt_id": "attempt-1",
                "attempt_refs": [
                    {
                        "attempt_id": "attempt-1",
                        "engine": "direct_glsl_layerplan_v1",
                        "representation": "shader_program_spec_v1",
                        "status": "succeeded",
                        "failure_code": None,
                    }
                ],
            },
        )

    async def close(self) -> None:
        return None


class _MalformedRuntime:
    async def generate(self, image, content_type, **kwargs):
        return SimpleNamespace(
            project_id="wrong-project",
            run_id="wrong-run",
            renderer_path="wrong-renderer",
            stop_reason="direct_attempt_completed",
        )


def test_generate_api_returns_only_current_direct_discriminators(
    tmp_path: Path,
) -> None:
    app = create_app(
        BackendSettings(
            public_artifact_root=tmp_path / "public",
            private_attempt_artifact_root=tmp_path / "private",
        )
    )
    with TestClient(app) as client:
        client.app.state.shader_runtime = _Runtime()
        response = client.post(
            "/api/shader/generate",
            files={"file": ("reference.png", _png(), "image/png")},
            data={"quality_preset": "balanced"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["engine"] == "direct_glsl_layerplan_v1"
    assert payload["representation"] == "shader_program_spec_v1"
    assert payload["engine_run"]["attempt_refs"][0]["status"] == "succeeded"


def test_generate_api_returns_503_when_direct_runtime_is_unavailable(
    tmp_path: Path,
) -> None:
    app = create_app(
        BackendSettings(
            public_artifact_root=tmp_path / "public",
            private_attempt_artifact_root=tmp_path / "private",
        )
    )
    with TestClient(app) as client:
        client.app.state.shader_runtime = None
        response = client.post(
            "/api/shader/generate",
            files={"file": ("reference.png", _png(), "image/png")},
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "service_unavailable"


def test_response_contract_failure_marks_progress_failed(tmp_path: Path) -> None:
    app = create_app(
        BackendSettings(
            public_artifact_root=tmp_path / "public",
            private_attempt_artifact_root=tmp_path / "private",
        )
    )
    run_id = uuid4()
    with TestClient(app) as client:
        client.app.state.shader_runtime = _MalformedRuntime()
        response = client.post(
            "/api/shader/generate",
            files={"file": ("reference.png", _png(), "image/png")},
            data={"run_id": str(run_id)},
        )
        progress = client.get(f"/api/shader/runs/{run_id}/progress")
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "response_contract_failed"
    assert progress.status_code == 200
    assert progress.json()["status"] == "failed"
