from __future__ import annotations

from pathlib import Path

from nodelab.http import schemas
from nodelab.http.main import create_app
from nodelab.http.schemas import batch, errors, execution
from nodelab.http.settings import NodeLabServiceSettings


def test_schema_facade_preserves_public_model_identities() -> None:
    assert schemas.NodeLabRunCreateBody is execution.NodeLabRunCreateBody
    assert schemas.NodeLabStepResponse is execution.NodeLabStepResponse
    assert schemas.NodeLabBatchRunBody is batch.NodeLabBatchRunBody
    assert schemas.NodeLabBatchReportResponse is batch.NodeLabBatchReportResponse
    assert schemas.NodeLabErrorResponse is errors.NodeLabErrorResponse
    assert set(schemas.__all__) == {
        name for name in dir(schemas) if name.startswith("NodeLab") or name.startswith("NODE_LAB")
    }


def test_openapi_keeps_stable_schema_names_after_internal_split(tmp_path: Path) -> None:
    settings = NodeLabServiceSettings(
        root=tmp_path / "runs",
        batch_root=tmp_path / "batches",
    )
    openapi = create_app(settings).openapi()
    component_schemas = openapi["components"]["schemas"]

    assert {
        "NodeLabRunCreateBody",
        "NodeLabStepBody",
        "NodeLabStepResponse",
        "NodeLabBatchRunBody",
        "NodeLabBatchReportResponse",
        "NodeLabErrorResponse",
    } <= component_schemas.keys()
    assert "/api/lab/v1/runs/{lab_run_id}/steps" in openapi["paths"]
    assert "/api/lab/v1/batches" in openapi["paths"]
