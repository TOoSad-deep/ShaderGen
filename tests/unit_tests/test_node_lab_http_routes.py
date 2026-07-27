from __future__ import annotations

from pathlib import Path
from typing import Any

from nodelab.http.main import create_app
from nodelab.http.settings import NodeLabServiceSettings

EXPECTED_OPERATIONS = {
    ("/api/lab/v1/health", "get"),
    ("/api/lab/v1/batch-suites", "get"),
    ("/api/lab/v1/batch-manifests/validate", "post"),
    ("/api/lab/v1/batches", "post"),
    ("/api/lab/v1/batches/{suite_run_id}", "get"),
    ("/api/lab/v1/nodes", "get"),
    ("/api/lab/v1/nodes/{node_id}", "get"),
    ("/api/lab/v1/capabilities", "get"),
    ("/api/lab/v1/capabilities/{capability_id}", "get"),
    ("/api/lab/v1/runs", "post"),
    ("/api/lab/v1/runs/{lab_run_id}", "get"),
    ("/api/lab/v1/runs/{lab_run_id}/steps", "get"),
    ("/api/lab/v1/runs/{lab_run_id}/steps", "post"),
    ("/api/lab/v1/runs/{lab_run_id}/steps/{step_id}", "get"),
    ("/api/lab/v1/runs/{lab_run_id}/capabilities/{capability_id}", "post"),
    ("/api/lab/v1/runs/{lab_run_id}/artifacts", "get"),
    ("/api/lab/v1/runs/{lab_run_id}/artifacts", "post"),
    ("/api/lab/v1/runs/{lab_run_id}/artifacts/{artifact_id}", "get"),
}


def _openapi(tmp_path: Path) -> dict[str, Any]:
    settings = NodeLabServiceSettings(
        root=tmp_path / "runs",
        batch_root=tmp_path / "batches",
    )
    return create_app(settings).openapi()


def test_route_facade_preserves_complete_http_operation_set(tmp_path: Path) -> None:
    openapi = _openapi(tmp_path)
    operations = {
        (path, method)
        for path, path_item in openapi["paths"].items()
        for method in path_item
        if method in {"get", "post", "put", "patch", "delete"}
    }

    assert operations == EXPECTED_OPERATIONS


def test_split_routes_keep_single_public_tag_and_unique_operation_ids(
    tmp_path: Path,
) -> None:
    openapi = _openapi(tmp_path)
    operation_ids = [
        operation["operationId"]
        for path_item in openapi["paths"].values()
        for method, operation in path_item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    tags = {
        tag
        for path_item in openapi["paths"].values()
        for method, operation in path_item.items()
        if method in {"get", "post", "put", "patch", "delete"}
        for tag in operation["tags"]
    }

    assert len(operation_ids) == len(set(operation_ids))
    assert tags == {"node-lab"}
