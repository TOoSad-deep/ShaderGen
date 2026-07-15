"""后端 API 路由聚合."""

import os

from fastapi import APIRouter

from backend.app.api.routes import health, shader


def node_lab_enabled() -> bool:
    """只在显式本地开关开启时暴露 Node Lab HTTP/Swagger 路由."""
    return os.getenv("SHADERGEN_NODE_LAB_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def build_api_router() -> APIRouter:
    """构造可测试的 API Router，避免默认 import 暴露 Lab."""
    router = APIRouter()
    router.include_router(health.router)
    router.include_router(shader.router)
    if node_lab_enabled():
        from backend.app.api.routes import node_lab

        router.include_router(node_lab.router)
    return router


api_router = build_api_router()
