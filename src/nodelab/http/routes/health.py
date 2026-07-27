"""Node Lab HTTP 健康检查路由."""

from fastapi import APIRouter, Request

from nodelab.http.routes.dependencies import service
from nodelab.http.schemas import NodeLabHealthResponse

router = APIRouter()


@router.get("/health", response_model=NodeLabHealthResponse)
def health(request: Request) -> NodeLabHealthResponse:
    """返回 Lab 与真实模型门禁状态，不触发 Renderer 或模型."""
    node_lab_service = service(request)
    return NodeLabHealthResponse(
        pipeline_id=node_lab_service.application.pipeline_id,
        node_count=len(node_lab_service.application.describe_nodes()),
        capability_count=len(node_lab_service.application.describe_capabilities()),
        suite_count=len(node_lab_service.application.describe_suites()),
        real_model_enabled=node_lab_service.real_model_enabled,
    )
