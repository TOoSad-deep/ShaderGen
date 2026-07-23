"""后端 API 路由聚合."""

from fastapi import APIRouter

from backend.app.api.routes import health, shader


def build_api_router() -> APIRouter:
    """构造产品 API Router."""
    router = APIRouter()
    router.include_router(health.router)
    router.include_router(shader.router)
    return router
