"""Shader 生成、评审和项目 Memory 接口."""

import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile

from backend.app.schemas.shader import (
    ShaderResponse,
    ShaderReview,
    ShaderReviewResponse,
)
from backend.app.services.agent_process_store import (
    record_shader_generation_failure,
    record_shader_generation_success,
    record_shader_review_failure,
    record_shader_review_success,
    start_shader_generation_run,
    start_shader_review_run,
)
from backend.app.services.shader import (
    MemoryUnavailableError,
    ProjectBusyError,
    ProjectLockRegistry,
    clear_shader_project_memory,
    default_shader_generation_service,
    generate_shader_from_image,
    get_shader_generation_models,
    review_shader_render,
)

router = APIRouter(prefix="/api/shader", tags=["shader"])
logger = logging.getLogger("backend.shader")

MAX_IMAGE_BYTES = 8 * 1024 * 1024


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


def _runtime(request: Request):
    service = getattr(request.app.state, "shader_service", None)
    locks = getattr(request.app.state, "project_locks", None)
    if service is None:
        service = default_shader_generation_service
    if locks is None:
        locks = ProjectLockRegistry()
        request.app.state.project_locks = locks
    return service, locks


@router.post("/generate", response_model=ShaderResponse)
async def generate_shader(
    request: Request,
    file: UploadFile = File(...),
    project_id: UUID | None = Form(None),
) -> ShaderResponse:
    """上传图片并在指定或新建项目中生成 GLSL."""
    image = await read_image_upload(file)
    resolved_project_id = project_id or uuid4()
    run_id = uuid4()
    pool = getattr(request.app.state, "db_pool", None)
    service, locks = _runtime(request)
    glsl_model_name, vision_model_name = get_shader_generation_models()
    run_started = False

    try:
        async with locks.hold(str(resolved_project_id)):
            if pool is not None:
                await start_shader_generation_run(
                    pool,
                    run_id=run_id,
                    project_id=resolved_project_id,
                    filename=file.filename,
                    content_type=file.content_type or "application/octet-stream",
                    size_bytes=len(image),
                    glsl_model_name=glsl_model_name,
                    vision_model_name=vision_model_name,
                )
                run_started = True

            result = await generate_shader_from_image(
                image,
                file.content_type or "application/octet-stream",
                project_id=str(resolved_project_id),
                run_id=str(run_id),
                service=service,
            )
    except ProjectBusyError as exc:
        raise HTTPException(status_code=409, detail="当前项目已有任务正在执行。") from exc
    except MemoryUnavailableError as exc:
        if pool is not None and run_started:
            await record_shader_generation_failure(pool, run_id=run_id, error=exc)
        logger.exception("shader.generate.memory_unavailable")
        raise HTTPException(status_code=503, detail="任务记忆暂时不可用。") from exc
    except Exception as exc:
        if pool is not None and run_started:
            await record_shader_generation_failure(pool, run_id=run_id, error=exc)
        logger.exception("shader.generate.failed")
        raise HTTPException(status_code=502, detail="生成 GLSL 失败。") from exc

    if pool is not None:
        await record_shader_generation_success(
            pool,
            run_id=run_id,
            model_name=result.glsl_model_name,
            glsl_chars=len(result.glsl),
            model_calls=result.model_calls,
            events=result.events,
            logs=result.logs,
        )

    return ShaderResponse(
        project_id=resolved_project_id,
        glsl=result.glsl,
        memory_status=result.memory_status,
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
    service, locks = _runtime(request)
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
        raise HTTPException(status_code=409, detail="当前项目已有任务正在执行。") from exc
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
    service, locks = _runtime(request)
    try:
        async with locks.hold(str(project_id)):
            await clear_shader_project_memory(str(project_id), service=service)
    except ProjectBusyError as exc:
        raise HTTPException(status_code=409, detail="当前项目已有任务正在执行。") from exc
    except MemoryUnavailableError as exc:
        logger.exception("shader.memory.clear_failed")
        raise HTTPException(status_code=503, detail="清除项目记忆失败。") from exc
    return Response(status_code=204)
