"""PNG-to-Shader 产品生成和项目 Memory 接口."""

import logging
import time
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile

from backend.app.schemas.shader import (
    MinRunProgressResponse,
    QualityPresetName,
    ShaderGenerationErrorDetail,
    ShaderGenerationErrorResponse,
    ShaderResponse,
)
from backend.app.services.run_progress import RunProgressRegistry
from backend.app.services.shader import (
    ProjectLockRegistry,
    PublicArtifactNotFoundError,
    get_default_png_to_shader_min_service,
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


def _runtime(request: Request) -> tuple[Any | None, ProjectLockRegistry]:
    locks = getattr(request.app.state, "project_locks", None)
    service = getattr(request.app.state, "png_to_shader_min_service", None)
    if service is None:
        service = get_default_png_to_shader_min_service()
    if locks is None:
        locks = ProjectLockRegistry()
        request.app.state.project_locks = locks
    return service, locks


def _progress_registry(request: Request) -> RunProgressRegistry:
    """获取应用级运行进度注册表，缺省时惰性创建（与 project_locks 同模式）."""
    registry = getattr(request.app.state, "run_progress", None)
    if registry is None:
        registry = RunProgressRegistry()
        request.app.state.run_progress = registry
    return registry


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
    run_id: UUID | None = Form(None),
    quality_preset: QualityPresetName = Form("balanced"),
    instruction: str = Form("", max_length=2_000),
) -> ShaderResponse:
    """校验 HTTP 输入并调用对应产品模式的生成用例服务."""
    started_at = time.perf_counter()
    resolved_project_id = project_id or uuid4()
    # 客户端可显式携带 run_id，以便在 POST 阻塞期间轮询运行进度。
    run_id = run_id or uuid4()
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
            "scene_mvp",
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
        quality_preset=quality_preset,
        instruction=instruction.strip(),
        started_at=started_at,
    )
    dependencies = ShaderGenerationDependencies(
        pool=getattr(request.app.state, "db_pool", None),
        min_service=service,
        locks=locks,
        progress=_progress_registry(request),
        production_shadow=getattr(
            request.app.state,
            "production_shadow_coordinator",
            None,
        ),
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


@router.get("/runs/{run_id}/progress", response_model=MinRunProgressResponse)
async def get_shader_run_progress(
    request: Request,
    run_id: UUID,
    after: int = 0,
) -> MinRunProgressResponse:
    """增量读取 scene_mvp 运行进度；未知 run_id 返回 pending 空进度."""
    data = _progress_registry(request).read(str(run_id), after=max(0, after))
    return MinRunProgressResponse(run_id=run_id, **data)


@router.get("/runs/{run_id}/progress/render")
async def get_shader_run_progress_render(request: Request, run_id: UUID) -> Response:
    """返回运行中最新渲染帧；尚无帧时 404."""
    png, _render_seq = _progress_registry(request).read_render(str(run_id))
    if png is None:
        raise HTTPException(status_code=404, detail="当前运行还没有可展示的渲染帧。")
    return Response(
        content=png,
        media_type="image/png",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/runs/{run_id}/artifacts/{artifact_name}")
async def get_shader_run_artifact(
    request: Request,
    run_id: UUID,
    artifact_name: str,
) -> Response:
    """下载 final-render、metrics 或 manifest 三种白名单产物."""
    service, _locks = _runtime(request)
    if service is None:
        raise HTTPException(status_code=503, detail="scene_mvp 服务尚未就绪。")
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
