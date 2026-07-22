from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.app.services.node_lab import create_node_lab_application
from backend.app.api.router import build_api_router
from nodelab.models import CapabilityExecutionRequest, LabRunCreateRequest
from nodelab.runner import NodeLabApplication
from nodelab_service.main import create_app as create_node_lab_service_app
from nodelab_service.settings import NodeLabServiceSettings
from shaderforge.rendering import CompileResult, RenderResult
from shaderforge.validation import validate_shader

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "benchmarks/png_to_shader_v1/images/solid_circle.png"


class FakeRenderer:
    """HTTP batch 单元测试使用的无浏览器 Renderer."""

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


def _test_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[FastAPI, NodeLabApplication]:
    del monkeypatch
    image = REFERENCE.read_bytes()
    application = create_node_lab_application(
        root=tmp_path / "node-lab",
        renderer_factory=lambda: FakeRenderer(image),
    )
    app = create_node_lab_service_app(
        NodeLabServiceSettings(
            root=tmp_path / "node-lab",
            batch_root=tmp_path / "batches",
            pipeline_id=application.pipeline_id,
        ),
        application=application,
    )
    return app, application


def test_node_lab_http_router_is_default_off(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    del monkeypatch
    app = FastAPI()
    app.include_router(build_api_router())

    paths = app.openapi()["paths"]
    assert not any(path.startswith("/api/lab/v1") for path in paths)
    assert TestClient(app).get("/api/lab/v1/health").status_code == 404
    standalone = create_node_lab_service_app(
        NodeLabServiceSettings(root=tmp_path / "standalone")
    )
    assert "/api/lab/v1/health" in standalone.openapi()["paths"]


def test_node_lab_http_exposes_discovery_run_artifact_and_steps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app, application = _test_app(monkeypatch, tmp_path)
    client = TestClient(app)

    health = client.get("/api/lab/v1/health")
    nodes = client.get("/api/lab/v1/nodes")
    capabilities = client.get("/api/lab/v1/capabilities")

    assert health.json() == {
        "status": "ok",
        "enabled": True,
        "service_mode": "standalone",
        "pipeline_id": application.pipeline_id,
        "node_count": 20,
        "capability_count": 8,
        "suite_count": 3,
        "real_model_enabled": False,
    }
    assert nodes.status_code == 200
    assert nodes.json() == [item.to_dict() for item in application.describe_nodes()]
    assert len(nodes.json()) == 20
    assert "prepare_measurement_seed" in {item["node_id"] for item in nodes.json()}
    assert capabilities.status_code == 200
    assert len(capabilities.json()) == 8

    created = client.post(
        "/api/lab/v1/runs",
        json={"project_id": "api-test", "initial_state": {"seed": 7}},
    )
    assert created.status_code == 200
    lab_run_id = created.json()["lab_run_id"]

    uploaded = client.post(
        f"/api/lab/v1/runs/{lab_run_id}/artifacts",
        data={"kind": "reference_png"},
        files={"file": ("reference.png", REFERENCE.read_bytes(), "image/png")},
    )
    assert uploaded.status_code == 200
    reference_artifact_id = uploaded.json()["artifact_id"]

    capability = client.post(
        f"/api/lab/v1/runs/{lab_run_id}/capabilities/measure-target",
        json={"inputs": {"reference_artifact_id": reference_artifact_id}},
    )
    assert capability.status_code == 200
    capability_body = capability.json()
    assert capability_body["execution_status"] == "completed"
    assert capability_body["outcome"] == "success"
    assert capability_body["usage"]["model_call_count"] == 0
    assert capability_body["output"]["target_measurements"]["image_width"] == 192

    step = client.post(
        f"/api/lab/v1/runs/{lab_run_id}/steps",
        json={
            "node_id": "measure_target",
            "execution_mode": "deterministic",
            "inputs": {"reference_artifact_id": reference_artifact_id},
        },
    )
    assert step.status_code == 200
    step_body = step.json()
    assert step_body["execution_status"] == "completed"
    assert step_body["output"]["target_measurements"]["image_width"] == 192

    step_id = step_body["step_id"]
    assert (
        client.get(f"/api/lab/v1/runs/{lab_run_id}/steps/{step_id}").json() == step_body
    )
    listed_steps = client.get(f"/api/lab/v1/runs/{lab_run_id}/steps").json()
    assert listed_steps["lab_run_id"] == lab_run_id
    assert listed_steps["step_ids"] == [step_id]
    assert listed_steps["steps"] == [
        {
            "schema_version": "node_lab_step_summary_v1",
            "lab_run_id": lab_run_id,
            "step_id": step_id,
            "base_step_id": None,
            "node_id": "measure_target",
            "execution_mode": "deterministic",
            "execution_status": "completed",
            "outcome": "success",
            "artifact_count": 1,
            "next_action": None,
            "duration_ms": step_body["duration_ms"],
            "execution_fingerprint": step_body["execution_fingerprint"],
            "created_at": step_body["created_at"],
        }
    ]

    metrics_artifact = capability_body["artifacts"][0]
    downloaded = client.get(
        f"/api/lab/v1/runs/{lab_run_id}/artifacts/{metrics_artifact['artifact_id']}"
    )
    assert downloaded.status_code == 200
    assert downloaded.headers["x-artifact-sha256"] == metrics_artifact["sha256"]
    assert downloaded.json()["image_width"] == 192

    listed_artifacts = client.get(f"/api/lab/v1/runs/{lab_run_id}/artifacts").json()
    assert listed_artifacts["lab_run_id"] == lab_run_id
    assert reference_artifact_id in {
        item["artifact_id"] for item in listed_artifacts["artifacts"]
    }
    assert all(
        item["lab_run_id"] == lab_run_id for item in listed_artifacts["artifacts"]
    )

    second_run = client.post("/api/lab/v1/runs", json={}).json()["lab_run_id"]
    cross_run = client.get(
        f"/api/lab/v1/runs/{second_run}/artifacts/{reference_artifact_id}"
    )
    assert cross_run.status_code == 404
    assert cross_run.json()["detail"]["code"] == "artifact_not_found"

    openapi = app.openapi()["paths"]
    assert "/api/lab/v1/nodes" in openapi
    assert "/api/lab/v1/runs/{lab_run_id}/steps" in openapi
    assert "/api/lab/v1/runs/{lab_run_id}/capabilities/{capability_id}" in openapi
    run_examples = openapi["/api/lab/v1/runs"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]
    step_examples = openapi["/api/lab/v1/runs/{lab_run_id}/steps"]["post"][
        "requestBody"
    ]["content"]["application/json"]["examples"]
    assert set(run_examples) == {"ephemeral", "project_scoped"}
    assert set(step_examples) == {
        "initialize",
        "fixture_from_parent",
        "prompt_preview",
        "mock_parser",
    }
    assert "roles/" not in "\n".join(openapi)
    assert nodes.json()[0]["input_examples"]


def test_node_lab_http_batch_uses_fixed_ai_off_suite_and_persists_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app, _application = _test_app(monkeypatch, tmp_path)
    client = TestClient(app)

    suites = client.get("/api/lab/v1/batch-suites")
    assert suites.status_code == 200
    assert suites.json()["suite_ids"] == [
        "node_lab_ai_off_v1",
        "node_lab_scenario_ai_off_v1",
        "node_lab_renderer_warm_ai_off_v1",
    ]

    validated = client.post(
        "/api/lab/v1/batch-manifests/validate",
        json={"suite_id": "node_lab_ai_off_v1"},
    )
    assert validated.status_code == 200
    assert validated.json()["case_count"] == 8
    assert validated.json()["profiles"] == ["micro", "node", "renderer_cold"]

    completed = client.post(
        "/api/lab/v1/batches",
        json={
            "suite_id": "node_lab_ai_off_v1",
            "suite_run_id": "http-batch-1",
        },
    )
    assert completed.status_code == 200
    report = completed.json()
    assert report["attempt_count"] == 8
    assert report["completed_attempt_count"] == 8
    assert report["interrupted_attempt_count"] == 0
    assert report["correctness_rate"] == 1.0

    fetched = client.get("/api/lab/v1/batches/http-batch-1")
    assert fetched.status_code == 200
    assert fetched.json() == report

    conflict = client.post(
        "/api/lab/v1/batches",
        json={
            "suite_id": "node_lab_scenario_ai_off_v1",
            "suite_run_id": "http-batch-1",
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "batch_conflict"
    assert client.get("/api/lab/v1/batches/http-batch-1").json() == report

    unknown = client.post(
        "/api/lab/v1/batches",
        json={"suite_id": "../../arbitrary-manifest"},
    )
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "input_contract_invalid"
    assert "arbitrary-manifest" not in unknown.text

    valid_unknown = client.post(
        "/api/lab/v1/batches",
        json={"suite_id": "unknown-suite-v1"},
    )
    assert valid_unknown.status_code == 404
    assert valid_unknown.json()["detail"]["code"] == "suite_not_found"


def test_model_preview_and_cost_gates_are_enforced_before_step_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app, _application = _test_app(monkeypatch, tmp_path)
    client = TestClient(app)
    lab_run_id = client.post("/api/lab/v1/runs", json={}).json()["lab_run_id"]
    uploaded = client.post(
        f"/api/lab/v1/runs/{lab_run_id}/artifacts",
        data={"kind": "reference_png"},
        files={"file": ("reference.png", REFERENCE.read_bytes(), "image/png")},
    ).json()
    measured = client.post(
        f"/api/lab/v1/runs/{lab_run_id}/capabilities/measure-target",
        json={"inputs": {"reference_artifact_id": uploaded["artifact_id"]}},
    ).json()["output"]["target_measurements"]
    inputs = {
        "reference_artifact_id": uploaded["artifact_id"],
        "target_measurements": measured,
    }

    preview = client.post(
        f"/api/lab/v1/runs/{lab_run_id}/steps",
        json={
            "node_id": "visual_analysis",
            "execution_mode": "fixture",
            "effect_mode": "preview",
            "preview_only": True,
            "inputs": inputs,
        },
    )

    assert preview.status_code == 200
    assert preview.json()["output"]["preview"]["gateway_call_count"] == 0
    assert preview.json()["usage"]["model_call_count"] == 0
    assert "base64" not in preview.text

    denied_real = client.post(
        f"/api/lab/v1/runs/{lab_run_id}/steps",
        json={
            "node_id": "visual_analysis",
            "execution_mode": "real",
            "allow_model_call": False,
            "inputs": inputs,
        },
    )
    denied_effect = client.post(
        f"/api/lab/v1/runs/{lab_run_id}/steps",
        json={
            "node_id": "promote_validated_strategy",
            "execution_mode": "deterministic",
            "effect_mode": "project_commit",
        },
    )

    assert denied_real.status_code == 403
    assert denied_real.json()["detail"]["code"] == "real_model_not_allowed"
    assert denied_effect.status_code == 403
    assert denied_effect.json()["detail"]["code"] == "effect_not_allowed"
    assert client.get(f"/api/lab/v1/runs/{lab_run_id}/steps").json()["step_ids"] == [
        preview.json()["step_id"]
    ]


@pytest.mark.anyio
async def test_http_and_application_capability_responses_are_semantically_equal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app, application = _test_app(monkeypatch, tmp_path)
    direct_run = application.create_run(LabRunCreateRequest(project_id="direct"))
    direct_artifact = application.upload_artifact(
        lab_run_id=direct_run.lab_run_id,
        kind="reference_png",
        content_type="image/png",
        data=REFERENCE.read_bytes(),
    )
    direct = await application.execute_capability(
        CapabilityExecutionRequest(
            lab_run_id=direct_run.lab_run_id,
            capability_id="measure-target",
            inputs={"reference_artifact_id": direct_artifact.artifact_id},
        )
    )

    with TestClient(app) as client:
        http_run = client.post("/api/lab/v1/runs", json={}).json()["lab_run_id"]
        uploaded = client.post(
            f"/api/lab/v1/runs/{http_run}/artifacts",
            data={"kind": "reference_png"},
            files={"file": ("reference.png", REFERENCE.read_bytes(), "image/png")},
        ).json()
        http = client.post(
            f"/api/lab/v1/runs/{http_run}/capabilities/measure-target",
            json={"inputs": {"reference_artifact_id": uploaded["artifact_id"]}},
        )

    assert http.status_code == 200
    body = http.json()
    assert body["execution_status"] == direct.execution_status
    assert body["outcome"] == direct.outcome
    assert body["output"]["target_measurements"] == direct.output["target_measurements"]
    assert body["provenance"] == direct.provenance
    assert body["usage"] == direct.usage


def test_node_lab_http_errors_are_stable_and_do_not_expose_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app, _application = _test_app(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.get("/api/lab/v1/capabilities/not-registered")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "capability_not_found"
    assert str(tmp_path) not in response.text

    invalid_body = client.post(
        "/api/lab/v1/runs",
        json={"initial_state": {}, "not_declared": "secret-value"},
    )
    assert invalid_body.status_code == 422
    assert invalid_body.json()["detail"] == {
        "message": "Node Lab HTTP 请求校验失败。",
        "code": "input_contract_invalid",
        "stage": "request_validation",
        "retryable": False,
        "lab_run_id": None,
        "step_id": None,
        "node_id": None,
    }
    assert "secret-value" not in invalid_body.text
