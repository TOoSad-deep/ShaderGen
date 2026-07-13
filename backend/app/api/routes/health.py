"""健康检查接口."""

import logging

from fastapi import APIRouter, HTTPException, Request

from backend.app.database.session import ping_database

router = APIRouter(tags=["health"])
logger = logging.getLogger("backend.health")


@router.get("/health")
def health_check() -> dict[str, str]:
    """返回后端健康状态."""
    return {"status": "ok"}


@router.get("/health/db")
async def database_health_check(request: Request) -> dict[str, str]:
    """返回数据库健康状态."""
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="数据库连接池未初始化。")

    try:
        ok = await ping_database(pool)
    except Exception as exc:
        logger.exception("database.health.failed")
        raise HTTPException(status_code=503, detail="数据库健康检查失败。") from exc

    if not ok:
        raise HTTPException(status_code=503, detail="数据库健康检查失败。")
    return {"status": "ok"}
