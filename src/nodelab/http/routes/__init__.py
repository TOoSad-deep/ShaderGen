"""Node Lab HTTP 资源路由的稳定聚合入口."""

from fastapi import APIRouter

from nodelab.http.routes.artifacts import router as artifact_router
from nodelab.http.routes.batch import router as batch_router
from nodelab.http.routes.catalog import router as catalog_router
from nodelab.http.routes.health import router as health_router
from nodelab.http.routes.runs import router as run_router

router = APIRouter(prefix="/api/lab/v1", tags=["node-lab"])
router.include_router(health_router)
router.include_router(batch_router)
router.include_router(catalog_router)
router.include_router(run_router)
router.include_router(artifact_router)

__all__ = ["router"]
