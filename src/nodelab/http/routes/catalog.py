"""Node Lab Node 与 Capability descriptor HTTP 路由."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from nodelab.http.routes.dependencies import http_error, service
from nodelab.http.schemas import (
    NodeLabCapabilityDescriptorResponse,
    NodeLabNodeDescriptorResponse,
)
from nodelab.http.service import NodeLabError

router = APIRouter()


@router.get("/nodes", response_model=list[NodeLabNodeDescriptorResponse])
def list_nodes(request: Request) -> list[dict[str, Any]]:
    """列出当前 Pipeline Provider 的生产节点和 Schema."""
    return service(request).describe_nodes()


@router.get("/nodes/{node_id}", response_model=NodeLabNodeDescriptorResponse)
def get_node(node_id: str, request: Request) -> dict[str, Any]:
    """读取单个 allowlist 节点 descriptor."""
    try:
        return service(request).describe_nodes(node_id)[0]
    except NodeLabError as exc:
        raise http_error(exc) from exc


@router.get(
    "/capabilities",
    response_model=list[NodeLabCapabilityDescriptorResponse],
)
def list_capabilities(request: Request) -> list[dict[str, Any]]:
    """列出当前 Pipeline Provider 的独立 capability descriptor."""
    return service(request).describe_capabilities()


@router.get(
    "/capabilities/{capability_id}",
    response_model=NodeLabCapabilityDescriptorResponse,
)
def get_capability(capability_id: str, request: Request) -> dict[str, Any]:
    """读取单个确定性 capability descriptor."""
    try:
        return service(request).describe_capabilities(capability_id)[0]
    except NodeLabError as exc:
        raise http_error(exc) from exc
