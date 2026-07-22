from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agent.app.services.node_lab import create_node_lab_application
from nodelab_service.main import create_app
from nodelab_service.settings import NodeLabServiceSettings

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "benchmarks/png_to_shader_v1/images/solid_circle.png"


def test_standalone_http_to_provider_to_shaderforge_step_flow(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """贯通 HTTP、Agent Application API、Artifact Store 和测量事实层."""
    del monkeypatch
    application = create_node_lab_application(root=tmp_path / "node-lab")
    app = create_app(
        NodeLabServiceSettings(
            root=tmp_path / "node-lab",
            pipeline_id=application.pipeline_id,
        ),
        application=application,
    )
    client = TestClient(app)

    lab_run_id = client.post("/api/lab/v1/runs", json={}).json()["lab_run_id"]
    uploaded = client.post(
        f"/api/lab/v1/runs/{lab_run_id}/artifacts",
        data={"kind": "reference_png"},
        files={"file": ("reference.png", REFERENCE.read_bytes(), "image/png")},
    )
    assert uploaded.status_code == 200

    executed = client.post(
        f"/api/lab/v1/runs/{lab_run_id}/steps",
        json={
            "node_id": "measure_target",
            "execution_mode": "deterministic",
            "inputs": {
                "reference_artifact_id": uploaded.json()["artifact_id"],
            },
        },
    )

    assert executed.status_code == 200
    body = executed.json()
    assert body["execution_status"] == "completed"
    assert body["outcome"] == "success"
    assert body["output"]["target_measurements"]["image_width"] == 192
    assert body["usage"] == {"model_call_count": 0, "browser_launch_count": 0}
    assert (
        client.get(f"/api/lab/v1/runs/{lab_run_id}/steps/{body['step_id']}").json()
        == body
    )
