"""PNG 转无贴图 Shader 的最小 scene 骨架 Graph。."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from agent.app.graphs.png_to_shader_min_routing import (
    route_after_base,
    route_after_feature,
    route_after_render,
)
from agent.app.nodes.png_to_shader_min import MinRendererRegistry, make_min_nodes
from agent.app.states.agent_state import PngToShaderMinState
from shaderforge.rendering import PlaywrightWebGL1Renderer
from shaderforge.store import LocalArtifactStore

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_MIN_ARTIFACT_ROOT = ROOT / "output/png-to-shader"
PNG_TO_SHADER_MIN_RECURSION_LIMIT = 64


# 图（PNG-to-Shader 最小 scene 骨架；与 add_node/add_edge/条件边一一对应）：
#
# START -> initialize_run -> perceive_target -> author_initial -> materialize_shader
#                                                                    |
#                                                                    v
# render_and_evaluate -> decide_after_render -- optimize_base --> optimize_base
#          ^                    |                                      |
#          |                    `-- finalize ----------------------+    v
#          |                                                       | decide_after_base
#          |                                                       |   | feature
# author_refine <--------------------------------------------------+---+ refine
#          |                                                       |   | finalize
#          `-> materialize_shader                                  |   v
#                                                        optimize_feature
#                                                               |
#                                                               v
#                                                     decide_after_feature
#                                                        | feature (loop)
#                                                        | refine -> author_refine
#                                                        ` finalize
#                                                               |
#                                                               v
#                                                          finalize -> END
#
# current_best 只在真实渲染且 MAE 改善后更新；失败候选不能覆盖它。Renderer 正常由
# finalize 关闭，Graph 外异常由 Agent Service finally 使用同一 registry 幂等兜底。
def build_png_to_shader_min_graph(
    *,
    artifact_store: LocalArtifactStore | None = None,
    renderer_registry: MinRendererRegistry | None = None,
) -> Any:
    """装配可注入 Artifact 和 Renderer 的 12 节点最小 Graph。."""
    artifacts = artifact_store or LocalArtifactStore(DEFAULT_MIN_ARTIFACT_ROOT)
    registry = renderer_registry or MinRendererRegistry(PlaywrightWebGL1Renderer)
    nodes = make_min_nodes(artifacts, registry)
    graph = cast(Any, StateGraph(PngToShaderMinState))
    for name in (
        "initialize_run",
        "perceive_target",
        "author_initial",
        "materialize_shader",
        "render_and_evaluate",
        "optimize_base",
        "optimize_feature",
        "author_refine",
        "finalize",
        "decide_after_render",
        "decide_after_base",
        "decide_after_feature",
    ):
        graph.add_node(name, nodes[name])

    graph.add_edge(START, "initialize_run")
    graph.add_edge("initialize_run", "perceive_target")
    graph.add_edge("perceive_target", "author_initial")
    graph.add_edge("author_initial", "materialize_shader")
    graph.add_edge("materialize_shader", "render_and_evaluate")
    graph.add_edge("render_and_evaluate", "decide_after_render")
    graph.add_conditional_edges(
        "decide_after_render",
        route_after_render,
        {"optimize_base": "optimize_base", "finalize": "finalize"},
    )
    graph.add_edge("optimize_base", "decide_after_base")
    graph.add_conditional_edges(
        "decide_after_base",
        route_after_base,
        {
            "optimize_feature": "optimize_feature",
            "author_refine": "author_refine",
            "finalize": "finalize",
        },
    )
    graph.add_edge("optimize_feature", "decide_after_feature")
    graph.add_conditional_edges(
        "decide_after_feature",
        route_after_feature,
        {
            "optimize_feature": "optimize_feature",
            "author_refine": "author_refine",
            "finalize": "finalize",
        },
    )
    graph.add_edge("author_refine", "materialize_shader")
    graph.add_edge("finalize", END)
    return graph.compile()


png_to_shader_min_artifact_store = LocalArtifactStore(DEFAULT_MIN_ARTIFACT_ROOT)
png_to_shader_min_renderer_registry = MinRendererRegistry(PlaywrightWebGL1Renderer)
png_to_shader_min_graph = build_png_to_shader_min_graph(
    artifact_store=png_to_shader_min_artifact_store,
    renderer_registry=png_to_shader_min_renderer_registry,
)


__all__ = [
    "DEFAULT_MIN_ARTIFACT_ROOT",
    "PNG_TO_SHADER_MIN_RECURSION_LIMIT",
    "build_png_to_shader_min_graph",
    "png_to_shader_min_artifact_store",
    "png_to_shader_min_graph",
    "png_to_shader_min_renderer_registry",
]
