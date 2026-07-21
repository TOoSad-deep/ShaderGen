from __future__ import annotations

import asyncio
import json
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import scripts.run_node_lab_transport_benchmark as transport_benchmark
from agent.app.services.node_lab import create_node_lab_application
from backend.app.api.routes.node_lab import router as node_lab_router
from backend.app.services.node_lab import NodeLabBackendService
from shaderforge.rendering import CompileResult, RenderResult
from shaderforge.validation import validate_shader

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "benchmarks/png_to_shader_v1/images/solid_circle.png"
PINK_GEL = ROOT / "benchmarks/png_to_shader_v1/images/pink_gel.png"


class ReferenceRenderer:
    """让集成测试贯通 Renderer Adapter，但不启动真实浏览器."""

    def __init__(self, image: bytes) -> None:
        self._image = image

    async def render(
        self,
        fragment_source: str,
        width: int,
        height: int,
    ) -> RenderResult:
        validation = validate_shader(fragment_source)
        return RenderResult(
            success=validation.valid,
            image_bytes=self._image if validation.valid else None,
            width=width,
            height=height,
            compile=CompileResult(
                success=validation.valid,
                vertex_log="",
                fragment_log="",
                link_log="",
                draw_error=None,
                static_validation=validation,
            ),
            console_errors=(),
            metadata=None,
            duration_ms=1.0,
        )

    async def close(self) -> None:
        """匹配 Renderer 生命周期契约."""


def test_http_batch_runs_five_step_scenario_and_preserves_evidence(
    tmp_path: Path,
) -> None:
    image = PINK_GEL.read_bytes()
    application = create_node_lab_application(
        root=tmp_path / "lab",
        renderer_factory=lambda: ReferenceRenderer(image),
    )
    batch_root = tmp_path / "batch"
    app = FastAPI()
    app.state.node_lab_service = NodeLabBackendService(
        application,
        batch_output_root=batch_root,
    )
    app.include_router(node_lab_router)

    with TestClient(app) as client:
        response = client.post(
            "/api/lab/v1/batches",
            json={
                "suite_id": "node_lab_scenario_ai_off_v1",
                "suite_run_id": "scenario-integration",
            },
        )
        fetched = client.get("/api/lab/v1/batches/scenario-integration")

    assert response.status_code == 200
    assert fetched.json() == response.json()
    assert response.json()["correctness_rate"] == 1.0
    execution_path = (
        batch_root / "scenario-integration/cases/pink_gel_fact_layer_scenario/"
        "attempts/attempt-001/execution.json"
    )
    execution = json.loads(execution_path.read_bytes())
    assert [item["step_id"] for item in execution["responses"]] == [
        "normalize",
        "measure",
        "validate",
        "render",
        "evaluate",
    ]
    assert len(execution["artifact_evidence"]) >= 8
    for item in execution["artifact_evidence"]:
        payload = batch_root / "scenario-integration" / item["relative_path"]
        assert sha256(payload.read_bytes()).hexdigest() == item["descriptor"]["sha256"]

    pipeline_path = (
        batch_root / "scenario-integration/cases/deterministic_run_lifecycle_pipeline/"
        "attempts/attempt-001/execution.json"
    )
    pipeline = json.loads(pipeline_path.read_bytes())
    assert pipeline["target_type"] == "pipeline"
    assert response.json()["attempt_count"] == 2
    assert pipeline["correctness_passed"] is True


@pytest.mark.anyio
async def test_transport_benchmark_compares_direct_http_and_resumes(
    tmp_path: Path,
) -> None:
    kwargs = {
        "reference_path": REFERENCE,
        "output_root": tmp_path / "transport",
        "lab_root": tmp_path / "transport-lab",
        "suite_run_id": "transport-integration",
        "repetitions": 2,
        "warmups": 1,
    }
    first = await transport_benchmark.run_transport_benchmark(**kwargs)
    second = await transport_benchmark.run_transport_benchmark(**kwargs)

    assert second == first
    assert first["attempt_count"] == 2
    assert first["correctness_rate"] == 1.0
    assert first["failed_attempts"] == []
    assert first["duration_ms"]["http_total"]["p95"] is None
    attempt = json.loads(
        (
            tmp_path
            / "transport/transport-integration/attempts/attempt-001/execution.json"
        ).read_bytes()
    )
    assert (
        attempt["evidence"]["direct_output_sha256"]
        == attempt["evidence"]["http_output_sha256"]
    )


@pytest.mark.anyio
async def test_transport_interruption_is_preserved_in_resumed_denominator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    application = create_node_lab_application(root=tmp_path / "lab")

    class InterruptOnce:
        def __init__(self) -> None:
            self.interrupted = False

        def __getattr__(self, name: str):
            return getattr(application, name)

        async def execute_capability(self, request):
            if not self.interrupted:
                self.interrupted = True
                raise asyncio.CancelledError
            return await application.execute_capability(request)

    wrapper = InterruptOnce()
    monkeypatch.setattr(
        transport_benchmark,
        "create_node_lab_application",
        lambda *, root: wrapper,
    )
    kwargs = {
        "reference_path": REFERENCE,
        "output_root": tmp_path / "transport",
        "lab_root": tmp_path / "transport-lab",
        "suite_run_id": "transport-interrupted",
        "repetitions": 1,
        "warmups": 0,
    }
    with pytest.raises(asyncio.CancelledError):
        await transport_benchmark.run_transport_benchmark(**kwargs)

    interruption = (
        tmp_path / "transport/transport-interrupted/attempts/attempt-001/"
        "interruptions/interruption-001.json"
    )
    assert interruption.is_file()
    monkeypatch.setattr(
        transport_benchmark,
        "create_node_lab_application",
        lambda *, root: application,
    )
    report = await transport_benchmark.run_transport_benchmark(**kwargs)

    assert report["attempt_count"] == 2
    assert report["completed_attempt_count"] == 1
    assert report["interrupted_attempt_count"] == 1
    assert report["passed_attempt_count"] == 1
    assert report["failed_attempt_count"] == 1
    assert report["correctness_rate"] == 0.5
    assert interruption.is_file()
