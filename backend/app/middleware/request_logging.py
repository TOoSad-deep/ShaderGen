"""请求日志中间件."""

import time
from collections.abc import Callable
from logging import Logger
from typing import Awaitable

from starlette.requests import Request
from starlette.responses import Response


def build_request_logging_middleware(
    logger: Logger,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """创建记录请求状态和耗时的中间件."""

    async def log_requests(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started_at = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.exception(
                "request.failed method=%s path=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                duration_ms,
            )
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        logger.info(
            "request.completed method=%s path=%s status_code=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response

    return log_requests
