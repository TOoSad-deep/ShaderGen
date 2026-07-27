"""装配当前 scene_mvp 的独立 Node Lab Application."""

from __future__ import annotations

from pathlib import Path

from agent.app.contracts.llm import LLMGateway
from agent.app.llms.gateway import LangChainLLMGateway
from agent.app.nodes.png_to_shader_min.node_lab import (
    create_scene_mvp_node_provider,
)
from nodelab.http.settings import NodeLabServiceSettings
from nodelab.runner import NodeLabApplication


def create_application(settings: NodeLabServiceSettings) -> NodeLabApplication:
    """为受信任独立进程注入当前生产 Node、Renderer 和可选 Gateway."""
    gateway: LLMGateway | None = (
        LangChainLLMGateway() if settings.real_model_enabled else None
    )
    provider = create_scene_mvp_node_provider(
        real_model_enabled=settings.real_model_enabled,
        model_gateway=gateway,
    )
    return NodeLabApplication.at_root(
        settings.root,
        node_provider=provider,
        benchmark_workspace_root=Path.cwd(),
        benchmark_source_paths=(Path(__file__).resolve(),),
    )


__all__ = ["create_application"]
