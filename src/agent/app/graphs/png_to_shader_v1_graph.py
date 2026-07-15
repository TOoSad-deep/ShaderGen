"""PNG 转无贴图 Shader V1 的独立有界 LangGraph."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, cast

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.store.memory import InMemoryStore

from agent.app.contracts.llm import LLMGateway
from agent.app.graphs.png_to_shader_v1_routing import (
    decide_after_render,
    decide_after_selection,
    model_node_outcome,
    route_after_candidate_selection,
    route_next_action,
)
from agent.app.llms.gateway import LangChainLLMGateway
from agent.app.nodes.bounded_model_node import make_bounded_model_node
from agent.app.nodes.png_to_shader_v1_run_nodes import (
    Clock,
    RendererFactory,
    RenderEvaluator,
    RunRendererRegistry,
    make_finalize_png_to_shader_v1_node,
    make_initialize_png_to_shader_v1_node,
    make_load_current_best_node,
    make_materialize_candidate_node,
    make_measure_target_node,
    make_persist_visual_analysis_node,
    make_persist_visual_review_node,
    make_prepare_compile_repair_node,
    make_prepare_measurement_seed_node,
    make_render_and_evaluate_node,
    make_select_current_best_node,
)
from agent.app.nodes.prepare_context_node import make_prepare_context_node
from agent.app.nodes.promote_validated_strategy_node import (
    make_promote_validated_strategy_node,
)
from agent.app.nodes.shader_author_node import (
    make_shader_author_compile_repair_node,
    make_shader_author_initial_node,
    make_shader_author_visual_refine_node,
)
from agent.app.nodes.visual_analysis_node import make_visual_analysis_node
from agent.app.nodes.visual_critic_node import make_visual_critic_node
from agent.app.states.agent_state import PngToShaderV1State
from shaderforge.evaluation import evaluate_render
from shaderforge.rendering import PlaywrightWebGL1Renderer
from shaderforge.store import LocalArtifactStore

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ARTIFACT_ROOT = ROOT / "output/png-to-shader"
PNG_TO_SHADER_V1_RECURSION_LIMIT = 96


def _default_renderer_factory(
    replay_on_worker_failure: int,
) -> PlaywrightWebGL1Renderer:
    return PlaywrightWebGL1Renderer(
        replay_on_worker_failure=replay_on_worker_failure,
    )


# 图（PNG 转无贴图 Shader V1；连线和路由均与下方 graph.add_* 调用一一对应）：
#
# START -> initialize_run -> prepare_context -> measure_target -> visual_analysis
#                                                                  |
#                                     continue                     | finalize
#                                      v                            v
# persist_visual_analysis -> author_initial -> materialize_candidate -> render_and_evaluate
#                              | finalize                                  |
#                              v                                           v
#                           finalize <----------------------- decide_after_render
#                                                                  |
#                  +-----------------------+-----------------------+------------------+
#                  | select                | compile_repair        | finalize         |
#                  v                       v                       v                  |
# select_current_best                             prepare_compile_repair              |
#                  |         |                     -> author_compile_repair -----------+
#                  | seed    | decide                         | continue
#                  v         v                                v
# prepare_measurement_seed   decide_after_selection           |
#                  |                    |                     |
#                  +-> materialize      | visual_critic / finalize
#                                       v
# load_current_best -> visual_critic -> persist_visual_review -> author_visual_refine
#                         | finalize                              | finalize / continue
#                         +---------------------------------------+---------> finalize / materialize_candidate
#
# finalize -> promote_validated_strategy -> END
#
# 所有模型节点都经 `model_node_outcome` 的 bounded 包装：continue 才进入下一阶段，
# 预算、结构化输出或模型失败均直接进入 finalize。候选重试始终回到
# materialize_candidate；只有 select_current_best 后的 current_best 可供 Critic、
# finalize 与 Memory 晋升读取。
def build_png_to_shader_v1_graph(
    gateway: LLMGateway,
    *,
    artifact_store: LocalArtifactStore | None = None,
    renderer_factory: RendererFactory = _default_renderer_factory,
    evaluator: RenderEvaluator = evaluate_render,
    clock: Clock = time.monotonic,
    enable_measurement_seed: bool = True,
    checkpointer: Any = None,
    store: Any = None,
) -> Any:
    """装配三角色、真实事实层、单调选择器与全部硬预算."""
    artifacts = artifact_store or LocalArtifactStore(DEFAULT_ARTIFACT_ROOT)
    renderer_registry = RunRendererRegistry(renderer_factory)
    selection_router = (
        route_after_candidate_selection
        if enable_measurement_seed
        else lambda _state: "decide"
    )

    visual_analysis = make_bounded_model_node(
        make_visual_analysis_node(gateway),
        stage="visual_analysis",
        clock=clock,
    )
    initial_author = make_bounded_model_node(
        make_shader_author_initial_node(gateway),
        stage="author_initial",
        clock=clock,
    )
    compile_repair_author = make_bounded_model_node(
        make_shader_author_compile_repair_node(gateway),
        stage="author_compile_repair",
        clock=clock,
        attempt_counter_field="compile_repair_count",
    )
    visual_critic = make_bounded_model_node(
        make_visual_critic_node(gateway),
        stage="visual_critic",
        clock=clock,
    )
    visual_refine_author = make_bounded_model_node(
        make_shader_author_visual_refine_node(gateway),
        stage="author_visual_refine",
        clock=clock,
        attempt_counter_field="visual_refinement_count",
    )

    graph = cast(Any, StateGraph(PngToShaderV1State))
    graph.add_node(
        "initialize_run",
        make_initialize_png_to_shader_v1_node(artifacts, clock=clock),
    )
    graph.add_node("prepare_context", make_prepare_context_node())
    graph.add_node("measure_target", make_measure_target_node(artifacts))
    graph.add_node("visual_analysis", visual_analysis)
    graph.add_node(
        "persist_visual_analysis",
        make_persist_visual_analysis_node(artifacts),
    )
    graph.add_node("author_initial", initial_author)
    graph.add_node(
        "materialize_candidate",
        make_materialize_candidate_node(artifacts),
    )
    graph.add_node(
        "render_and_evaluate",
        make_render_and_evaluate_node(
            artifacts,
            renderer_registry,
            evaluator,
            clock=clock,
        ),
    )
    graph.add_node("decide_after_render", decide_after_render)
    graph.add_node("prepare_compile_repair", make_prepare_compile_repair_node())
    graph.add_node("author_compile_repair", compile_repair_author)
    graph.add_node("select_current_best", make_select_current_best_node(artifacts))
    graph.add_node("prepare_measurement_seed", make_prepare_measurement_seed_node())
    graph.add_node("decide_after_selection", decide_after_selection)
    graph.add_node("load_current_best", make_load_current_best_node(artifacts))
    graph.add_node("visual_critic", visual_critic)
    graph.add_node(
        "persist_visual_review",
        make_persist_visual_review_node(artifacts),
    )
    graph.add_node("author_visual_refine", visual_refine_author)
    graph.add_node(
        "finalize",
        make_finalize_png_to_shader_v1_node(
            artifacts,
            renderer_registry,
            clock=clock,
        ),
    )
    graph.add_node(
        "promote_validated_strategy",
        make_promote_validated_strategy_node(artifacts),
    )

    graph.add_edge(START, "initialize_run")
    graph.add_edge("initialize_run", "prepare_context")
    graph.add_edge("prepare_context", "measure_target")
    graph.add_edge("measure_target", "visual_analysis")
    graph.add_conditional_edges(
        "visual_analysis",
        model_node_outcome,
        {"continue": "persist_visual_analysis", "finalize": "finalize"},
    )
    graph.add_edge("persist_visual_analysis", "author_initial")
    graph.add_conditional_edges(
        "author_initial",
        model_node_outcome,
        {"continue": "materialize_candidate", "finalize": "finalize"},
    )
    graph.add_edge("materialize_candidate", "render_and_evaluate")
    graph.add_edge("render_and_evaluate", "decide_after_render")
    graph.add_conditional_edges(
        "decide_after_render",
        route_next_action,
        {
            "select": "select_current_best",
            "compile_repair": "prepare_compile_repair",
            "finalize": "finalize",
        },
    )
    graph.add_edge("prepare_compile_repair", "author_compile_repair")
    graph.add_conditional_edges(
        "author_compile_repair",
        model_node_outcome,
        {"continue": "materialize_candidate", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "select_current_best",
        selection_router,
        {
            "measurement_seed": "prepare_measurement_seed",
            "decide": "decide_after_selection",
        },
    )
    graph.add_edge("prepare_measurement_seed", "materialize_candidate")
    graph.add_conditional_edges(
        "decide_after_selection",
        route_next_action,
        {"visual_critic": "load_current_best", "finalize": "finalize"},
    )
    graph.add_edge("load_current_best", "visual_critic")
    graph.add_conditional_edges(
        "visual_critic",
        model_node_outcome,
        {"continue": "persist_visual_review", "finalize": "finalize"},
    )
    graph.add_edge("persist_visual_review", "author_visual_refine")
    graph.add_conditional_edges(
        "author_visual_refine",
        model_node_outcome,
        {"continue": "materialize_candidate", "finalize": "finalize"},
    )
    graph.add_edge("finalize", "promote_validated_strategy")
    graph.add_edge("promote_validated_strategy", END)
    return graph.compile(
        checkpointer=checkpointer,
        store=store,
        name="PngToShaderV1",
    ).with_config({"recursion_limit": PNG_TO_SHADER_V1_RECURSION_LIMIT})


_default_gateway = LangChainLLMGateway()


def build_default_png_to_shader_v1_graph(
    *,
    artifact_store: LocalArtifactStore,
    checkpointer: Any,
    store: Any,
) -> Any:
    """使用默认 Gateway 与外部 persistence 装配 V1 Graph."""
    return build_png_to_shader_v1_graph(
        _default_gateway,
        artifact_store=artifact_store,
        checkpointer=checkpointer,
        store=store,
    )


png_to_shader_v1_checkpointer = InMemorySaver()
png_to_shader_v1_store = InMemoryStore()
png_to_shader_v1_artifact_store = LocalArtifactStore(DEFAULT_ARTIFACT_ROOT)
png_to_shader_v1_graph = build_png_to_shader_v1_graph(
    _default_gateway,
    artifact_store=png_to_shader_v1_artifact_store,
    checkpointer=png_to_shader_v1_checkpointer,
    store=png_to_shader_v1_store,
)
