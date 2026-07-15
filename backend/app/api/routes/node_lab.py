"""默认关闭的 Node Lab 本地调试与模块测试 HTTP API."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Body,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from pydantic import ValidationError

from backend.app.schemas.node_lab import (
    NODE_LAB_RUN_OPENAPI_EXAMPLES,
    NODE_LAB_STEP_OPENAPI_EXAMPLES,
    NodeLabArtifactListResponse,
    NodeLabArtifactResponse,
    NodeLabBatchReportResponse,
    NodeLabBatchRunBody,
    NodeLabBatchSuiteListResponse,
    NodeLabBatchValidateBody,
    NodeLabBatchValidationResponse,
    NodeLabCapabilityBody,
    NodeLabCapabilityDescriptorResponse,
    NodeLabCapabilityResponse,
    NodeLabErrorResponse,
    NodeLabHealthResponse,
    NodeLabNodeDescriptorResponse,
    NodeLabRunCreateBody,
    NodeLabRunResponse,
    NodeLabStepBody,
    NodeLabStepListResponse,
    NodeLabStepResponse,
)
from backend.app.services.node_lab import (
    NodeLabBackendService,
    NodeLabError,
    create_default_node_lab_backend_service,
)

router = APIRouter(prefix="/api/lab/v1", tags=["node-lab"])
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"model": NodeLabErrorResponse},
    404: {"model": NodeLabErrorResponse},
    409: {"model": NodeLabErrorResponse},
    413: {"model": NodeLabErrorResponse},
    422: {"model": NodeLabErrorResponse},
    500: {"model": NodeLabErrorResponse},
    503: {"model": NodeLabErrorResponse},
}


def _service(request: Request) -> NodeLabBackendService:
    """按 FastAPI 生命周期惰性持有单个 Node Lab Application."""
    service = getattr(request.app.state, "node_lab_service", None)
    if service is None:
        service = create_default_node_lab_backend_service()
        request.app.state.node_lab_service = service
    return service


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


def _http_error(exc: NodeLabError) -> HTTPException:
    """把 Agent 稳定错误映射到 FastAPI detail envelope."""
    return HTTPException(status_code=_status_for(exc.code), detail=exc.to_detail())


def _validation_http_error() -> HTTPException:
    """收敛 Backend 到 Agent DTO 的校验失败，不返回字段原值."""
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


@router.get("/health", response_model=NodeLabHealthResponse)
def health(request: Request) -> NodeLabHealthResponse:
    """返回 Lab 与真实模型门禁状态，不触发 Renderer 或模型."""
    return NodeLabHealthResponse(
        real_model_enabled=_service(request).real_model_enabled,
    )


@router.get("/batch-suites", response_model=NodeLabBatchSuiteListResponse)
def list_batch_suites(request: Request) -> NodeLabBatchSuiteListResponse:
    """列出 HTTP 可运行的固定 AI-off suite，不返回 manifest 路径."""
    return NodeLabBatchSuiteListResponse.model_validate(
        {"suite_ids": list(_service(request).describe_suites())}
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
        return _service(request).validate_batch_suite(body.suite_id)
    except NodeLabError as exc:
        raise _http_error(exc) from exc
    except (ValidationError, ValueError) as exc:
        raise _validation_http_error() from exc


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
        return await _service(request).run_batch(
            suite_id=body.suite_id,
            suite_run_id=body.suite_run_id,
        )
    except NodeLabError as exc:
        raise _http_error(exc) from exc
    except ValidationError as exc:
        raise _validation_http_error() from exc


@router.get(
    "/batches/{suite_run_id}",
    response_model=NodeLabBatchReportResponse,
    responses=ERROR_RESPONSES,
)
def get_batch(suite_run_id: str, request: Request) -> dict[str, Any]:
    """读取已完成或恢复后重新聚合的 batch report."""
    try:
        return _service(request).get_batch_report(suite_run_id)
    except NodeLabError as exc:
        raise _http_error(exc) from exc


@router.get("/nodes", response_model=list[NodeLabNodeDescriptorResponse])
def list_nodes(request: Request) -> list[dict[str, Any]]:
    """列出生产图 19 节点的当前实现状态和 Schema."""
    return _service(request).describe_nodes()


@router.get("/nodes/{node_id}", response_model=NodeLabNodeDescriptorResponse)
def get_node(node_id: str, request: Request) -> dict[str, Any]:
    """读取单个 allowlist 节点 descriptor."""
    try:
        return _service(request).describe_nodes(node_id)[0]
    except NodeLabError as exc:
        raise _http_error(exc) from exc


@router.get(
    "/capabilities",
    response_model=list[NodeLabCapabilityDescriptorResponse],
)
def list_capabilities(request: Request) -> list[dict[str, Any]]:
    """列出八个确定性能力 descriptor."""
    return _service(request).describe_capabilities()


@router.get(
    "/capabilities/{capability_id}",
    response_model=NodeLabCapabilityDescriptorResponse,
)
def get_capability(capability_id: str, request: Request) -> dict[str, Any]:
    """读取单个确定性 capability descriptor."""
    try:
        return _service(request).describe_capabilities(capability_id)[0]
    except NodeLabError as exc:
        raise _http_error(exc) from exc


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
        return _service(request).create_run(
            project_id=body.project_id,
            initial_state=body.initial_state,
        )
    except NodeLabError as exc:
        raise _http_error(exc) from exc
    except ValidationError as exc:
        raise _validation_http_error() from exc


@router.get(
    "/runs/{lab_run_id}",
    response_model=NodeLabRunResponse,
    responses=ERROR_RESPONSES,
)
def get_run(lab_run_id: str, request: Request) -> dict[str, Any]:
    """读取 LabRun 元数据."""
    try:
        return _service(request).get_run(lab_run_id)
    except NodeLabError as exc:
        raise _http_error(exc) from exc


@router.get(
    "/runs/{lab_run_id}/steps",
    response_model=NodeLabStepListResponse,
    responses=ERROR_RESPONSES,
)
def list_steps(lab_run_id: str, request: Request) -> dict[str, Any]:
    """列出已原子提交的步骤 id 和可直接重建 DAG 的摘要."""
    try:
        steps = _service(request).list_steps(lab_run_id)
        return {
            "lab_run_id": lab_run_id,
            "step_ids": [str(item["step_id"]) for item in steps],
            "steps": steps,
        }
    except NodeLabError as exc:
        raise _http_error(exc) from exc


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
        return await _service(request).execute_step(
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
        raise _http_error(exc) from exc
    except ValidationError as exc:
        raise _validation_http_error() from exc


@router.get(
    "/runs/{lab_run_id}/steps/{step_id}",
    response_model=NodeLabStepResponse,
    responses=ERROR_RESPONSES,
)
def get_step(lab_run_id: str, step_id: str, request: Request) -> dict[str, Any]:
    """读取已提交步骤响应."""
    try:
        return _service(request).get_step(lab_run_id, step_id)
    except NodeLabError as exc:
        raise _http_error(exc) from exc


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
        return await _service(request).execute_capability(
            lab_run_id=lab_run_id,
            capability_id=capability_id,
            inputs=body.inputs,
        )
    except NodeLabError as exc:
        raise _http_error(exc) from exc
    except ValidationError as exc:
        raise _validation_http_error() from exc


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
        return _service(request).upload_artifact(
            lab_run_id=lab_run_id,
            kind=kind,
            content_type=file.content_type or "application/octet-stream",
            data=data,
        )
    except NodeLabError as exc:
        raise _http_error(exc) from exc
    except ValidationError as exc:
        raise _validation_http_error() from exc


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
            "artifacts": _service(request).list_artifacts(lab_run_id),
        }
    except NodeLabError as exc:
        raise _http_error(exc) from exc


@router.get(
    "/runs/{lab_run_id}/artifacts/{artifact_id}",
    responses=ERROR_RESPONSES,
)
def read_artifact(lab_run_id: str, artifact_id: str, request: Request) -> Response:
    """按不透明 id 读取同一 LabRun 的 Artifact，不接受路径."""
    try:
        descriptor, data = _service(request).read_artifact(lab_run_id, artifact_id)
    except NodeLabError as exc:
        raise _http_error(exc) from exc
    return Response(
        content=data,
        media_type=str(descriptor["content_type"]),
        headers={
            "X-Artifact-SHA256": str(descriptor["sha256"]),
            "X-Artifact-Id": str(descriptor["artifact_id"]),
        },
    )
