"""Node Lab 独立 FastAPI 服务入口."""

from __future__ import annotations

import json
import logging
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response
from starlette.types import ExceptionHandler

from nodelab.runner import NodeLabApplication
from nodelab_service.factory import load_application
from nodelab_service.routes import router
from nodelab_service.service import NodeLabHttpService, create_node_lab_http_service
from nodelab_service.settings import NodeLabServiceSettings

logger = logging.getLogger("nodelab_service")


async def log_request_validation_error(
    request: Request,
    exc: RequestValidationError,
) -> Response:
    """返回稳定 422，不记录字段原值."""
    errors = [
        {
            "location": ".".join(str(part) for part in error.get("loc", ())),
            "type": str(error.get("type", "validation_error")),
        }
        for error in exc.errors()[:20]
    ]
    logger.warning(
        "node_lab.request.validation_failed method=%s path=%s errors=%s",
        request.method,
        request.url.path,
        json.dumps(errors, sort_keys=True, separators=(",", ":")),
    )
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "message": "Node Lab HTTP 请求校验失败。",
                "code": "input_contract_invalid",
                "stage": "request_validation",
                "retryable": False,
                "lab_run_id": None,
                "step_id": None,
                "node_id": None,
            }
        },
    )


def create_app(
    settings: NodeLabServiceSettings | None = None,
    *,
    application: NodeLabApplication | None = None,
) -> FastAPI:
    """创建不依赖 Agent 或产品 Backend 的独立服务."""
    resolved = settings or NodeLabServiceSettings.from_env()
    resolved_application = application or load_application(resolved)
    service: NodeLabHttpService = create_node_lab_http_service(
        application=resolved_application,
        batch_output_root=resolved.batch_root,
        real_model_enabled=resolved.real_model_enabled,
    )
    api = FastAPI(
        title="Node Lab Service",
        version="1.0.0",
        description="Pipeline 无关的 Node 调试、证据与 benchmark 服务。",
    )
    api.state.settings = resolved
    api.state.node_lab_service = service
    api.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, log_request_validation_error),
    )
    api.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    api.include_router(router)
    return api


__all__ = ["create_app", "log_request_validation_error"]
