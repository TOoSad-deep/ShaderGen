"""Node Lab HTTP Route 共用的服务解析与安全错误映射."""

from __future__ import annotations

from typing import Any, cast

from fastapi import HTTPException, Request

from nodelab.http.schemas import NodeLabErrorResponse
from nodelab.http.service import NodeLabError, NodeLabHttpService

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"model": NodeLabErrorResponse},
    404: {"model": NodeLabErrorResponse},
    409: {"model": NodeLabErrorResponse},
    413: {"model": NodeLabErrorResponse},
    422: {"model": NodeLabErrorResponse},
    500: {"model": NodeLabErrorResponse},
    503: {"model": NodeLabErrorResponse},
}


def service(request: Request) -> NodeLabHttpService:
    """读取独立服务组合根已冻结的 Node Lab Application."""
    node_lab_service = getattr(request.app.state, "node_lab_service", None)
    if node_lab_service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Node Lab Service 尚未完成 Application 装配。",
                "code": "service_not_configured",
                "stage": "service_startup",
                "retryable": False,
                "lab_run_id": None,
                "step_id": None,
                "node_id": None,
            },
        )
    return cast(NodeLabHttpService, node_lab_service)


def _status_for(code: str) -> int:
    if code in {
        "node_not_found",
        "capability_not_found",
        "lab_run_not_found",
        "step_not_found",
        "artifact_not_found",
        "fixture_not_found",
        "suite_not_found",
        "batch_not_found",
    }:
        return 404
    if code in {
        "unsupported_execution_mode",
        "executor_not_configured",
        "real_model_not_allowed",
        "effect_not_allowed",
    }:
        return 403
    if code in {
        "artifact_integrity_failed",
        "lab_run_conflict",
        "step_conflict",
        "artifact_conflict",
        "fixture_node_mismatch",
        "batch_conflict",
        "node_prerequisite_missing",
        "project_scope_mismatch",
    }:
        return 409
    if code == "artifact_too_large":
        return 413
    if code in {
        "input_contract_invalid",
        "mock_response_invalid",
        "node_adapter_not_implemented",
    }:
        return 422
    if code in {"renderer_unavailable", "memory_unavailable"}:
        return 503
    return 500


def http_error(exc: NodeLabError) -> HTTPException:
    """把 Application 稳定错误映射到 FastAPI detail envelope."""
    return HTTPException(status_code=_status_for(exc.code), detail=exc.to_detail())


def validation_http_error() -> HTTPException:
    """收敛 HTTP 到 Application DTO 的校验失败，不返回字段原值."""
    return HTTPException(
        status_code=422,
        detail={
            "message": "Node Lab 请求不符合 Application API 契约。",
            "code": "input_contract_invalid",
            "stage": "request_validation",
            "retryable": False,
            "lab_run_id": None,
            "step_id": None,
            "node_id": None,
        },
    )
