"""后端 API 路由聚合."""

from fastapi import APIRouter

from backend.app.api.routes import health, shader

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(shader.router)
