"""后端 API 路由聚合."""

from fastapi import APIRouter

from backend.app.api.routes import health, shader
from backend.app.core.settings import BackendSettings


def build_api_router(*, node_lab_enabled: bool | None = None) -> APIRouter:
    """构造可测试的 API Router，避免默认 import 暴露 Lab."""
    enabled = (
        BackendSettings.from_env().node_lab_enabled
        if node_lab_enabled is None
        else node_lab_enabled
    )
    router = APIRouter()
    router.include_router(health.router)
    router.include_router(shader.router)
    if enabled:
        from backend.app.api.routes import node_lab

        router.include_router(node_lab.router)
    return router
