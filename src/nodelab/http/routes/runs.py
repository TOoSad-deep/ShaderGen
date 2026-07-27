"""Node Lab LabRun、Step 与 Capability 执行 HTTP 路由."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Request
from pydantic import ValidationError

from nodelab.http.routes.dependencies import (
    ERROR_RESPONSES,
    http_error,
    service,
    validation_http_error,
)
from nodelab.http.schemas import (
    NODE_LAB_RUN_OPENAPI_EXAMPLES,
    NODE_LAB_STEP_OPENAPI_EXAMPLES,
    NodeLabCapabilityBody,
    NodeLabCapabilityResponse,
    NodeLabRunCreateBody,
    NodeLabRunResponse,
    NodeLabStepBody,
    NodeLabStepListResponse,
    NodeLabStepResponse,
)
from nodelab.http.service import NodeLabError

router = APIRouter()


@router.post(
    "/runs",
    response_model=NodeLabRunResponse,
    responses=ERROR_RESPONSES,
)
def create_run(
    body: Annotated[
        NodeLabRunCreateBody,
        Body(openapi_examples=NODE_LAB_RUN_OPENAPI_EXAMPLES),
    ],
    request: Request,
) -> dict[str, Any]:
    """创建独立 LabRun 和 root snapshot."""
    try:
        return service(request).create_run(
            project_id=body.project_id,
            initial_state=body.initial_state,
        )
    except NodeLabError as exc:
        raise http_error(exc) from exc
    except ValidationError as exc:
        raise validation_http_error() from exc


@router.get(
    "/runs/{lab_run_id}",
    response_model=NodeLabRunResponse,
    responses=ERROR_RESPONSES,
)
def get_run(lab_run_id: str, request: Request) -> dict[str, Any]:
    """读取 LabRun 元数据."""
    try:
        return service(request).get_run(lab_run_id)
    except NodeLabError as exc:
        raise http_error(exc) from exc


@router.get(
    "/runs/{lab_run_id}/steps",
    response_model=NodeLabStepListResponse,
    responses=ERROR_RESPONSES,
)
def list_steps(lab_run_id: str, request: Request) -> dict[str, Any]:
    """列出已原子提交的步骤 id 和可直接重建 DAG 的摘要."""
    try:
        steps = service(request).list_steps(lab_run_id)
        return {
            "lab_run_id": lab_run_id,
            "step_ids": [str(item["step_id"]) for item in steps],
            "steps": steps,
        }
    except NodeLabError as exc:
        raise http_error(exc) from exc


@router.post(
    "/runs/{lab_run_id}/steps",
    response_model=NodeLabStepResponse,
    responses=ERROR_RESPONSES,
)
async def execute_step(
    lab_run_id: str,
    body: Annotated[
        NodeLabStepBody,
        Body(openapi_examples=NODE_LAB_STEP_OPENAPI_EXAMPLES),
    ],
    request: Request,
) -> dict[str, Any]:
    """执行通用 allowlist 节点；模型角色也只复用这一执行入口."""
    try:
        return await service(request).execute_step(
            lab_run_id=lab_run_id,
            node_id=body.node_id,
            execution_mode=body.execution_mode,
            effect_mode=body.effect_mode,
            preview_only=body.preview_only,
            allow_model_call=body.allow_model_call,
            base_step_id=body.base_step_id,
            fixture_id=body.fixture_id,
            mock_response_artifact_id=body.mock_response_artifact_id,
            inputs=body.inputs,
        )
    except NodeLabError as exc:
        raise http_error(exc) from exc
    except ValidationError as exc:
        raise validation_http_error() from exc


@router.get(
    "/runs/{lab_run_id}/steps/{step_id}",
    response_model=NodeLabStepResponse,
    responses=ERROR_RESPONSES,
)
def get_step(lab_run_id: str, step_id: str, request: Request) -> dict[str, Any]:
    """读取已提交步骤响应."""
    try:
        return service(request).get_step(lab_run_id, step_id)
    except NodeLabError as exc:
        raise http_error(exc) from exc


@router.post(
    "/runs/{lab_run_id}/capabilities/{capability_id}",
    response_model=NodeLabCapabilityResponse,
    responses=ERROR_RESPONSES,
)
async def execute_capability(
    lab_run_id: str,
    capability_id: str,
    body: NodeLabCapabilityBody,
    request: Request,
) -> dict[str, Any]:
    """执行独立确定性领域能力."""
    try:
        return await service(request).execute_capability(
            lab_run_id=lab_run_id,
            capability_id=capability_id,
            inputs=body.inputs,
        )
    except NodeLabError as exc:
        raise http_error(exc) from exc
    except ValidationError as exc:
        raise validation_http_error() from exc
