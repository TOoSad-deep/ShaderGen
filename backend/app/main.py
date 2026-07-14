"""FastAPI 后端入口."""

import json
import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from backend.app.api.router import api_router
from backend.app.core.logging import configure_logging
from backend.app.database.agent_memory import close_agent_memory, open_agent_memory
from backend.app.database.session import close_database_pool, open_database_pool
from backend.app.middleware.request_logging import build_request_logging_middleware
from backend.app.schemas.shader import ShaderGenerationErrorDetail
from backend.app.services.shader import ProjectLockRegistry

configure_logging()
logger = logging.getLogger("backend.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """记录后端生命周期."""
    logger.info("backend.startup")
    app.state.project_locks = ProjectLockRegistry()
    await open_database_pool(app)
    try:
        await open_agent_memory(app)
        yield
    finally:
        await close_agent_memory(app)
        await close_database_pool(app)
        app.state.project_locks = None
        logger.info("backend.shutdown")


app = FastAPI(title="ShaderGen API", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def log_request_validation_error(
    request: Request,
    exc: RequestValidationError,
):
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


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(build_request_logging_middleware(logger))
app.include_router(api_router)
