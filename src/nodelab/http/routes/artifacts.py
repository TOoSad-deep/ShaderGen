"""Node Lab Artifact 上传、目录和读取 HTTP 路由."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import ValidationError

from nodelab.http.routes.dependencies import (
    ERROR_RESPONSES,
    http_error,
    service,
    validation_http_error,
)
from nodelab.http.schemas import (
    NodeLabArtifactListResponse,
    NodeLabArtifactResponse,
)
from nodelab.http.service import NodeLabError

router = APIRouter()
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024


@router.post(
    "/runs/{lab_run_id}/artifacts",
    response_model=NodeLabArtifactResponse,
    responses=ERROR_RESPONSES,
)
async def upload_artifact(
    lab_run_id: str,
    request: Request,
    file: UploadFile = File(...),
    kind: str = Form(...),
) -> dict[str, Any]:
    """上传最多 8MB 的私有 Lab Artifact."""
    data = await file.read(MAX_ARTIFACT_BYTES + 1)
    if len(data) > MAX_ARTIFACT_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "message": "Node Lab Artifact 超过 8MB 上限。",
                "code": "artifact_too_large",
                "stage": "artifact_upload",
                "retryable": False,
                "lab_run_id": lab_run_id,
                "step_id": None,
                "node_id": None,
            },
        )
    try:
        return service(request).upload_artifact(
            lab_run_id=lab_run_id,
            kind=kind,
            content_type=file.content_type or "application/octet-stream",
            data=data,
        )
    except NodeLabError as exc:
        raise http_error(exc) from exc
    except ValidationError as exc:
        raise validation_http_error() from exc


@router.get(
    "/runs/{lab_run_id}/artifacts",
    response_model=NodeLabArtifactListResponse,
    responses=ERROR_RESPONSES,
)
def list_artifacts(lab_run_id: str, request: Request) -> dict[str, Any]:
    """列出 Artifact descriptor，不读取或返回私有 payload."""
    try:
        return {
            "lab_run_id": lab_run_id,
            "artifacts": service(request).list_artifacts(lab_run_id),
        }
    except NodeLabError as exc:
        raise http_error(exc) from exc


@router.get(
    "/runs/{lab_run_id}/artifacts/{artifact_id}",
    responses=ERROR_RESPONSES,
)
def read_artifact(lab_run_id: str, artifact_id: str, request: Request) -> Response:
    """按不透明 id 读取同一 LabRun 的 Artifact，不接受路径."""
    try:
        descriptor, data = service(request).read_artifact(lab_run_id, artifact_id)
    except NodeLabError as exc:
        raise http_error(exc) from exc
    return Response(
        content=data,
        media_type=str(descriptor["content_type"]),
        headers={
            "X-Artifact-SHA256": str(descriptor["sha256"]),
            "X-Artifact-Id": str(descriptor["artifact_id"]),
        },
    )
