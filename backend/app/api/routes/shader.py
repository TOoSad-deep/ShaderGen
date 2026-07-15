"""Shader 生成、评审和项目 Memory 接口."""

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
    ShaderReview,
    ShaderReviewResponse,
)
from backend.app.services.agent_process_store import (
    record_shader_review_failure,
    record_shader_review_success,
    start_shader_review_run,
)
from backend.app.services.shader import (
    MemoryUnavailableError,
    ProjectBusyError,
    ProjectLockRegistry,
    PublicArtifactNotFoundError,
    clear_png_to_shader_project_memory,
    clear_shader_project_memory,
    default_png_to_shader_v1_service,
    default_shader_generation_service,
    read_shader_run_artifact,
    review_shader_render,
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


def _runtime(request: Request) -> tuple[Any, Any, ProjectLockRegistry]:
    service = getattr(request.app.state, "shader_service", None)
    procedural_service = getattr(
        request.app.state,
        "png_to_shader_v1_service",
        None,
    )
    locks = getattr(request.app.state, "project_locks", None)
    if service is None:
        service = default_shader_generation_service
    if procedural_service is None:
        procedural_service = default_png_to_shader_v1_service
    if locks is None:
        locks = ProjectLockRegistry()
        request.app.state.project_locks = locks
    return service, procedural_service, locks


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
    generation_mode: GenerationMode = Form("legacy"),
    quality_preset: QualityPresetName = Form("balanced"),
    instruction: str = Form("", max_length=2_000),
) -> ShaderResponse:
    """校验 HTTP 输入并调用 Shader 生成用例服务."""
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

    legacy_service, procedural_service, locks = _runtime(request)
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
        legacy_service=legacy_service,
        procedural_service=procedural_service,
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
    _service, procedural_service, _locks = _runtime(request)
    try:
        artifact = read_shader_run_artifact(
            str(run_id),
            artifact_name,
            service=procedural_service,
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


@router.post("/review", response_model=ShaderReviewResponse)
async def review_shader(
    request: Request,
    original_file: UploadFile = File(...),
    rendered_file: UploadFile = File(...),
    glsl: str = Form(...),
    project_id: UUID = Form(...),
) -> ShaderReviewResponse:
    """上传原图、渲染图和 GLSL，在同一项目中评审并晋升 Memory."""
    original_image = await read_image_upload(original_file)
    rendered_image = await read_image_upload(rendered_file)
    if not glsl.strip():
        raise HTTPException(status_code=400, detail="GLSL 代码不能为空。")

    pool = getattr(request.app.state, "db_pool", None)
    service, _procedural_service, locks = _runtime(request)
    run_id = uuid4()
    run_started = False
    try:
        async with locks.hold(str(project_id)):
            if pool is not None:
                await start_shader_review_run(
                    pool,
                    run_id=run_id,
                    project_id=project_id,
                    original_content_type=original_file.content_type
                    or "application/octet-stream",
                    original_size_bytes=len(original_image),
                    rendered_content_type=rendered_file.content_type
                    or "application/octet-stream",
                    rendered_size_bytes=len(rendered_image),
                    glsl_chars=len(glsl),
                )
                run_started = True

            result = await review_shader_render(
                original_image,
                original_file.content_type or "application/octet-stream",
                rendered_image,
                rendered_file.content_type or "application/octet-stream",
                glsl,
                project_id=str(project_id),
                run_id=str(run_id),
                service=service,
            )
    except ProjectBusyError as exc:
        raise HTTPException(
            status_code=409, detail="当前项目已有任务正在执行。"
        ) from exc
    except MemoryUnavailableError as exc:
        if pool is not None and run_started:
            await record_shader_review_failure(pool, run_id=run_id, error=exc)
        logger.exception("shader.review.memory_unavailable")
        raise HTTPException(status_code=503, detail="任务记忆暂时不可用。") from exc
    except Exception as exc:
        if pool is not None and run_started:
            await record_shader_review_failure(pool, run_id=run_id, error=exc)
        logger.exception("shader.review.failed")
        raise HTTPException(status_code=502, detail="评审渲染图失败。") from exc

    if pool is not None:
        await record_shader_review_success(
            pool,
            run_id=run_id,
            model_name=result.review_model_name,
            evaluation=result.evaluation,
            suggestion_count=len(result.suggestions),
            model_calls=result.model_calls,
            events=result.events,
            logs=result.logs,
        )

    return ShaderReviewResponse(
        project_id=project_id,
        review=ShaderReview(
            evaluation=result.evaluation,
            suggestions=list(result.suggestions),
        ),
        memory_status=result.memory_status,
    )


@router.delete("/projects/{project_id}/memory", status_code=204)
async def clear_project_memory(request: Request, project_id: UUID) -> Response:
    """清除当前项目 checkpoint 和长期 Memory，不删除过程账本."""
    service, procedural_service, locks = _runtime(request)
    try:
        async with locks.hold(str(project_id)):
            await clear_png_to_shader_project_memory(
                str(project_id),
                service=procedural_service,
            )
            await clear_shader_project_memory(str(project_id), service=service)
    except ProjectBusyError as exc:
        raise HTTPException(
            status_code=409, detail="当前项目已有任务正在执行。"
        ) from exc
    except MemoryUnavailableError as exc:
        logger.exception("shader.memory.clear_failed")
        raise HTTPException(status_code=503, detail="清除项目记忆失败。") from exc
    return Response(status_code=204)
