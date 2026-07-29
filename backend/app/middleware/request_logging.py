"""请求日志中间件."""

import logging
import re
import time
from collections.abc import Callable
from logging import Logger
from typing import Awaitable
from uuid import uuid4

from starlette.requests import Request
from starlette.responses import Response

from backend.app.core.log_context import bind_log_context, reset_log_context

_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _request_id(request: Request) -> str:
    """读取安全的客户端 request id；无效值一律替换为服务端 UUID."""
    candidate = request.headers.get("X-Request-ID", "")
    if _SAFE_REQUEST_ID.fullmatch(candidate):
        return candidate
    return str(uuid4())


def build_request_logging_middleware(
    logger: Logger,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """创建记录请求状态和耗时的中间件."""

    async def log_requests(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started_at = time.perf_counter()
        request_id = _request_id(request)
        request.state.request_id = request_id
        tokens = bind_log_context(request_id=request_id)
        try:
            try:
                response = await call_next(request)
            except Exception:
                failure_tokens = bind_log_context(
                    **getattr(request.state, "log_context", {})
                )
                try:
                    duration_ms = (time.perf_counter() - started_at) * 1000
                    logger.exception(
                        "event=request.failed method=%s path=%s status_code=500 "
                        "error_code=unhandled_exception retryable=false "
                        "duration_ms=%.2f",
                        request.method,
                        request.url.path,
                        duration_ms,
                    )
                finally:
                    reset_log_context(failure_tokens)
                raise

            response.headers["X-Request-ID"] = request_id
            completion_tokens = bind_log_context(
                **getattr(request.state, "log_context", {})
            )
            try:
                duration_ms = (time.perf_counter() - started_at) * 1000
                error_context = getattr(request.state, "log_error", {})
                default_error_code = (
                    f"http_{response.status_code}"
                    if response.status_code >= 400
                    else "-"
                )
                level = logging.INFO
                if response.status_code >= 500:
                    level = logging.ERROR
                    event = "request.failed"
                elif response.status_code >= 400:
                    level = logging.WARNING
                    event = "request.rejected"
                else:
                    event = "request.completed"
                logger.log(
                    level,
                    "event=%s method=%s path=%s status_code=%s "
                    "error_code=%s retryable=%s duration_ms=%.2f",
                    event,
                    request.method,
                    request.url.path,
                    response.status_code,
                    error_context.get("error_code", default_error_code),
                    error_context.get("retryable", "-"),
                    duration_ms,
                )
            finally:
                reset_log_context(completion_tokens)
            return response
        finally:
            reset_log_context(tokens)

    return log_requests
