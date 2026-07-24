"""最小 scene Graph 节点工厂。."""

from agent.app.nodes.png_to_shader_min.runtime import (
    MinRendererRegistry,
    make_min_nodes,
)
from agent.app.nodes.png_to_shader_min.shader_graph_runtime import (
    make_shader_graph_nodes,
)

__all__ = ["MinRendererRegistry", "make_min_nodes", "make_shader_graph_nodes"]
