"""PNG-to-Shader V1 生成和项目 Memory 接口."""

import logging
import time
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile

from backend.app.schemas.shader import (
    GenerationMode,
    QualityPresetName,
    ShaderGenerationErrorDetail,
    ShaderGenerationErrorResponse,
    ShaderResponse,
)
from backend.app.services.shader import (
    MemoryUnavailableError,
    ProjectBusyError,
    ProjectLockRegistry,
    PublicArtifactNotFoundError,
    clear_png_to_shader_project_memory,
    default_png_to_shader_v1_service,
    read_shader_run_artifact,
)
from backend.app.services.shader_generation import (
    ShaderGenerationCommand,
    ShaderGenerationDependencies,
    ShaderGenerationUseCaseError,
    execute_shader_generation,
)

router = APIRouter(prefix="/api/shader", tags=["shader"])
logger = logging.getLogger("backend.shader")

MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _generation_http_error(
    *,
    status_code: int,
    message: str,
    code: str,
    run_id: UUID,
    stage: str,
    retryable: bool,
    stop_reason: str | None,
) -> HTTPException:
    """构造不暴露图片、GLSL 或模型原文的稳定生成失败契约."""
    detail = ShaderGenerationErrorDetail(
        message=message,
        code=code,
        run_id=run_id,
        stage=stage,
        retryable=retryable,
        stop_reason=stop_reason,
    )
    return HTTPException(
        status_code=status_code,
        detail=detail.model_dump(mode="json"),
    )


async def read_image_upload(file: UploadFile) -> bytes:
    """读取并校验图片上传."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="请上传图片文件。")

    image = await file.read()
    if not image:
        raise HTTPException(status_code=400, detail="图片不能为空。")
    if len(image) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="图片不能超过 8MB。")
    return image


def _runtime(request: Request) -> tuple[Any, ProjectLockRegistry]:
    service = getattr(
        request.app.state,
        "png_to_shader_v1_service",
        None,
    )
    locks = getattr(request.app.state, "project_locks", None)
    if service is None:
        service = default_png_to_shader_v1_service
    if locks is None:
        locks = ProjectLockRegistry()
        request.app.state.project_locks = locks
    return service, locks


@router.post(
    "/generate",
    response_model=ShaderResponse,
    responses={
        400: {"model": ShaderGenerationErrorResponse},
        409: {"model": ShaderGenerationErrorResponse},
        413: {"model": ShaderGenerationErrorResponse},
        422: {"model": ShaderGenerationErrorResponse},
        500: {"model": ShaderGenerationErrorResponse},
        502: {"model": ShaderGenerationErrorResponse},
        503: {"model": ShaderGenerationErrorResponse},
        504: {"model": ShaderGenerationErrorResponse},
    },
)
async def generate_shader(
    request: Request,
    file: UploadFile = File(...),
    project_id: UUID | None = Form(None),
    generation_mode: GenerationMode = Form("procedural_v1"),
    quality_preset: QualityPresetName = Form("balanced"),
    instruction: str = Form("", max_length=2_000),
) -> ShaderResponse:
    """校验 HTTP 输入并调用 V1 生成用例服务."""
    started_at = time.perf_counter()
    resolved_project_id = project_id or uuid4()
    run_id = uuid4()
    try:
        image = await read_image_upload(file)
    except HTTPException as exc:
        duration_ms = (time.perf_counter() - started_at) * 1000
        logger.warning(
            "shader.generate.client_validation_failed run_id=%s project_id=%s "
            "generation_mode=%s stage=request_validation "
            "stop_reason=client_validation status_code=%s retryable=false "
            "duration_ms=%.2f",
            run_id,
            resolved_project_id,
            generation_mode,
            exc.status_code,
            duration_ms,
        )
        raise _generation_http_error(
            status_code=exc.status_code,
            message=str(exc.detail),
            code="client_validation",
            run_id=run_id,
            stage="request_validation",
            retryable=False,
            stop_reason="client_validation",
        ) from exc

    service, locks = _runtime(request)
    command = ShaderGenerationCommand(
        image=image,
        filename=file.filename,
        content_type=file.content_type or "application/octet-stream",
        project_id=resolved_project_id,
        run_id=run_id,
        generation_mode=generation_mode,
        quality_preset=quality_preset,
        instruction=instruction.strip(),
        started_at=started_at,
    )
    dependencies = ShaderGenerationDependencies(
        pool=getattr(request.app.state, "db_pool", None),
        procedural_service=service,
        locks=locks,
    )
    try:
        return await execute_shader_generation(command, dependencies)
    except ShaderGenerationUseCaseError as exc:
        raise _generation_http_error(
            status_code=exc.status_code,
            message=exc.message,
            code=exc.code,
            run_id=exc.run_id,
            stage=exc.stage,
            retryable=exc.retryable,
            stop_reason=exc.stop_reason,
        ) from exc


@router.get("/runs/{run_id}/artifacts/{artifact_name}")
async def get_shader_run_artifact(
    request: Request,
    run_id: UUID,
    artifact_name: str,
) -> Response:
    """下载 final-render、metrics 或 manifest 三种白名单产物."""
    service, _locks = _runtime(request)
    try:
        artifact = read_shader_run_artifact(
            str(run_id),
            artifact_name,
            service=service,
        )
    except (PublicArtifactNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="未找到该运行产物。") from exc
    return Response(
        content=artifact.data,
        media_type=artifact.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{artifact.filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/projects/{project_id}/memory", status_code=204)
async def clear_project_memory(request: Request, project_id: UUID) -> Response:
    """清除当前项目 V1 checkpoint 和长期 Memory，不删除过程账本."""
    service, locks = _runtime(request)
    try:
        async with locks.hold(str(project_id)):
            await clear_png_to_shader_project_memory(
                str(project_id),
                service=service,
            )
    except ProjectBusyError as exc:
        raise HTTPException(
            status_code=409, detail="当前项目已有任务正在执行。"
        ) from exc
    except MemoryUnavailableError as exc:
        logger.exception("shader.memory.clear_failed")
        raise HTTPException(status_code=503, detail="清除项目记忆失败。") from exc
    return Response(status_code=204)
