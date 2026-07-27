"""FastAPI 后端入口."""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import cast
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response
from starlette.types import ExceptionHandler, Lifespan

from backend.app.api.router import build_api_router
from backend.app.core.logging import configure_logging
from backend.app.core.settings import BackendSettings
from backend.app.database.session import close_database_pool, open_database_pool
from backend.app.middleware.request_logging import build_request_logging_middleware
from backend.app.schemas.shader import ShaderGenerationErrorDetail
from backend.app.services.production_shadow import (
    ProductionShadowConfig,
    ProductionShadowCoordinator,
)
from backend.app.services.run_progress import RunProgressRegistry
from backend.app.services.shader import (
    ProjectLockRegistry,
    close_png_to_shader_min_service,
    get_default_png_to_shader_min_service,
)

logger = logging.getLogger("backend.app")


def _clear_runtime_state(app: FastAPI) -> None:
    """清空只在单次应用生命周期内有效的依赖."""
    app.state.png_to_shader_min_service = None
    app.state.project_locks = None
    app.state.run_progress = None
    app.state.production_shadow_coordinator = None


def build_lifespan(settings: BackendSettings) -> Lifespan[FastAPI]:
    """为给定冻结配置构造可测试的应用生命周期."""

    @asynccontextmanager
    async def lifespan_context(app: FastAPI) -> AsyncIterator[None]:
        logger.info("backend.startup")
        app.state.project_locks = ProjectLockRegistry()
        app.state.run_progress = RunProgressRegistry()
        app.state.png_to_shader_min_service = None
        app.state.production_shadow_coordinator = None
        app.state.db_pool = None
        try:
            async with AsyncExitStack() as cleanup:
                cleanup.callback(_clear_runtime_state, app)

                await open_database_pool(app, settings.database_url)
                cleanup.push_async_callback(close_database_pool, app)

                app.state.png_to_shader_min_service = (
                    get_default_png_to_shader_min_service()
                )
                cleanup.push_async_callback(
                    close_png_to_shader_min_service,
                    app.state.png_to_shader_min_service,
                )

                shadow = ProductionShadowCoordinator(
                    policy=settings.engine_policy,
                    resolution=settings.engine_policy_resolution,
                    config=ProductionShadowConfig(
                        output_root=settings.production_shadow_artifact_root,
                        queue_capacity=settings.production_shadow_queue_capacity,
                        worker_count=settings.production_shadow_worker_count,
                        attempt_timeout_seconds=(
                            settings.production_shadow_attempt_timeout_seconds
                        ),
                        close_timeout_seconds=(
                            settings.production_shadow_close_timeout_seconds
                        ),
                        resource_close_timeout_seconds=(
                            settings.production_shadow_resource_close_timeout_seconds
                        ),
                    ),
                )
                app.state.production_shadow_coordinator = shadow
                cleanup.push_async_callback(shadow.close)
                await shadow.start()
                yield
        finally:
            logger.info("backend.shutdown")

    return lifespan_context


async def log_request_validation_error(
    request: Request,
    exc: RequestValidationError,
) -> Response:
    """记录安全 422 字段诊断；生成接口返回稳定错误 envelope."""
    errors = [
        {
            "location": ".".join(str(part) for part in error.get("loc", ())),
            "type": str(error.get("type", "validation_error")),
            "message": str(error.get("msg", "校验失败"))[:300],
        }
        for error in exc.errors()[:20]
    ]
    serialized_errors = json.dumps(
        errors,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if request.url.path == "/api/shader/generate":
        run_id = uuid4()
        logger.warning(
            "request.validation_failed method=%s path=%s run_id=%s "
            "stage=request_validation stop_reason=client_validation "
            "retryable=false error_count=%s errors=%s",
            request.method,
            request.url.path,
            run_id,
            len(exc.errors()),
            serialized_errors,
        )
        detail = ShaderGenerationErrorDetail(
            message="Shader 生成请求参数校验失败。",
            code="client_validation",
            run_id=run_id,
            stage="request_validation",
            retryable=False,
            stop_reason="client_validation",
        )
        return JSONResponse(
            status_code=422,
            content={"detail": detail.model_dump(mode="json")},
        )
    logger.warning(
        "request.validation_failed method=%s path=%s error_count=%s errors=%s",
        request.method,
        request.url.path,
        len(exc.errors()),
        serialized_errors,
    )
    return await request_validation_exception_handler(request, exc)


def create_app(settings: BackendSettings | None = None) -> FastAPI:
    """从显式配置创建完整 FastAPI 应用."""
    resolved = settings or BackendSettings.from_env()
    application = FastAPI(
        title="ShaderGen API",
        lifespan=build_lifespan(resolved),
    )
    application.state.settings = resolved
    application.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, log_request_validation_error),
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.middleware("http")(build_request_logging_middleware(logger))
    application.include_router(build_api_router())
    return application


settings = BackendSettings.from_env()
configure_logging(settings.log_level)
lifespan = build_lifespan(settings)
app = create_app(settings)
