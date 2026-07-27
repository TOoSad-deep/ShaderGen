"""Node Lab 固定 batch suite HTTP 路由."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import ValidationError

from nodelab.http.routes.dependencies import (
    ERROR_RESPONSES,
    http_error,
    service,
    validation_http_error,
)
from nodelab.http.schemas import (
    NodeLabBatchReportResponse,
    NodeLabBatchRunBody,
    NodeLabBatchSuiteListResponse,
    NodeLabBatchValidateBody,
    NodeLabBatchValidationResponse,
)
from nodelab.http.service import NodeLabError

router = APIRouter()


@router.get("/batch-suites", response_model=NodeLabBatchSuiteListResponse)
def list_batch_suites(request: Request) -> NodeLabBatchSuiteListResponse:
    """列出 HTTP 可运行的固定 AI-off suite，不返回 manifest 路径."""
    return NodeLabBatchSuiteListResponse.model_validate(
        {"suite_ids": list(service(request).describe_suites())}
    )


@router.post(
    "/batch-manifests/validate",
    response_model=NodeLabBatchValidationResponse,
    responses=ERROR_RESPONSES,
)
def validate_batch_manifest(
    body: NodeLabBatchValidateBody,
    request: Request,
) -> dict[str, object]:
    """在执行前校验固定 suite 的 schema、hash 与 allowlist."""
    try:
        return service(request).validate_batch_suite(body.suite_id)
    except NodeLabError as exc:
        raise http_error(exc) from exc
    except (ValidationError, ValueError) as exc:
        raise validation_http_error() from exc


@router.post(
    "/batches",
    response_model=NodeLabBatchReportResponse,
    responses=ERROR_RESPONSES,
)
async def run_batch(
    body: NodeLabBatchRunBody,
    request: Request,
) -> dict[str, object]:
    """同步运行固定 AI-off suite；不提供真实模型或任意路径入口."""
    try:
        return await service(request).run_batch(
            suite_id=body.suite_id,
            suite_run_id=body.suite_run_id,
        )
    except NodeLabError as exc:
        raise http_error(exc) from exc
    except ValidationError as exc:
        raise validation_http_error() from exc


@router.get(
    "/batches/{suite_run_id}",
    response_model=NodeLabBatchReportResponse,
    responses=ERROR_RESPONSES,
)
def get_batch(suite_run_id: str, request: Request) -> dict[str, Any]:
    """读取已完成或恢复后重新聚合的 batch report."""
    try:
        return service(request).get_batch_report(suite_run_id)
    except NodeLabError as exc:
        raise http_error(exc) from exc
