"""PNG 转无贴图 Shader 的最小 scene 骨架 Graph。."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from agent.app.config.png_to_shader_min import MIN_PIPELINE_CONFIG
from agent.app.contracts.llm import LLMGateway
from agent.app.graphs.png_to_shader_min_routing import (
    route_after_base,
    route_after_feature,
    route_after_render,
)
from agent.app.llms.gateway import LangChainLLMGateway
from agent.app.nodes.png_to_shader_min import MinRendererRegistry, make_min_nodes
from agent.app.states.agent_state import PngToShaderMinState
from shaderforge.rendering import PlaywrightWebGL1Renderer
from shaderforge.store import LocalArtifactStore

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_MIN_ARTIFACT_ROOT = ROOT / "output/png-to-shader"
# 兼容只读取最大安全上限的调用方；产品执行按具体 quality policy 注入 run 级值。
PNG_TO_SHADER_MIN_RECURSION_LIMIT = MIN_PIPELINE_CONFIG.max_recursion_limit


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
# Model Author 只通过 Builder 注入的 LLMGateway 调用；Initial 的模型 scene 与感知
# fallback 在预算允许时都先真实渲染并择优，current_best 只在前景/高光/阴影复合
# loss 改善后更新；全局 MAE 保留为诊断，失败/非法 patch 候选不能覆盖 best。
# Refine 的合法非重复 typed patch 先在独立 branch 内执行最多 12 次真实 draw
# （含 raw draw）的 Patch-local 成熟，再与只读 current_best 严格比较；未改善、
# 重复、非法或 Renderer 失败的分支整体丢弃，随后经 optimize_base no-op 过桥。
# 每个 run 的 recursion limit 按 LLM/Refine 预算与最多四个 feature 的合法最坏路径
# 推导并留出框架余量；它只防御意外路由循环，不能作为正常预算停止条件。
# Renderer 正常由 finalize 关闭，Graph 外异常由 Agent Service finally 使用同一
# registry 幂等兜底。
def build_png_to_shader_min_graph(
    *,
    artifact_store: LocalArtifactStore | None = None,
    renderer_registry: MinRendererRegistry | None = None,
    gateway: LLMGateway | None = None,
) -> Any:
    """装配可注入 Gateway、Artifact 和 Renderer 的 12 节点最小 Graph。."""
    artifacts = artifact_store or LocalArtifactStore(DEFAULT_MIN_ARTIFACT_ROOT)
    registry = renderer_registry or MinRendererRegistry(PlaywrightWebGL1Renderer)
    model_gateway = gateway or LangChainLLMGateway()
    nodes = make_min_nodes(artifacts, registry, model_gateway)
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
png_to_shader_min_gateway = LangChainLLMGateway()
png_to_shader_min_graph = build_png_to_shader_min_graph(
    artifact_store=png_to_shader_min_artifact_store,
    renderer_registry=png_to_shader_min_renderer_registry,
    gateway=png_to_shader_min_gateway,
)


__all__ = [
    "DEFAULT_MIN_ARTIFACT_ROOT",
    "PNG_TO_SHADER_MIN_RECURSION_LIMIT",
    "build_png_to_shader_min_graph",
    "png_to_shader_min_artifact_store",
    "png_to_shader_min_gateway",
    "png_to_shader_min_graph",
    "png_to_shader_min_renderer_registry",
]
