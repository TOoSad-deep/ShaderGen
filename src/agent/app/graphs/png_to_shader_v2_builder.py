"""PNG-to-Shader V2.3 development-only 有界 LangGraph Builder。"""
# ruff: noqa: D415

from __future__ import annotations

from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from agent.app.graphs.png_to_shader_v2_routing import (
    route_after_candidate_preparation,
    route_after_candidate_selection,
    route_after_compile,
    route_after_cross_selection,
    route_after_evaluation,
    route_after_hypothesis,
    route_after_initialize,
    route_after_intent_build,
    route_after_interpretation,
    route_after_materialization,
    route_after_measurement,
    route_after_render,
    route_after_seed,
    route_after_seed_planning,
    route_after_seed_proposal,
    route_after_strategy,
)
from agent.app.nodes.png_to_shader_v2 import (
    PNG_TO_SHADER_V2_NODE_IDS,
    PngToShaderV2NodeRuntime,
    build_png_to_shader_v2_node_callables,
)
from agent.app.states.png_to_shader_v2_state import PngToShaderV2State

PNG_TO_SHADER_V2_RECURSION_LIMIT = 512


# 图（V2.3 development-only；实际节点与条件 path map 均在下方逐字注册）：
#
# START -> initialize_run_v2 --fresh--> prepare_context_v2 -> ingest_target_v2
#                  | resume by phase/ref: analyze/build/dequeue/prepare/compile/
#                  | render/evaluate/materialize/select/cross/promote/end
#       -> measure_target_v2 --finalize------------------------------+
#                | interpret                                        |
#                v                                                  v
# analyze_visual_layers_v2 --finalize--------------------------> finalize_v2 -> END
#                | build_intents
#                v
# build_intent_variants_v2 --finalize--------------------------> finalize_v2
#                | dequeue_hypothesis
#                v
# dequeue_hypothesis_v2 --cross_select--> select_cross_hypothesis_best_v2
#      | plan_seeds                                  | promote / finalize
#      v                                             v
# plan_strategy_v2 -> propose_seed_plans_v2 -> expand_validate_seeds_v2
#      | finalize          | finalize                  | dequeue_seed / finalize
#      +-------------------+---------------------------+
#                                                      v
# dequeue_seed_v2 --prepare_candidate--> prepare_candidate_attempt_v2
#      | next_hypothesis / finalize                     | compile / finalize
#      v                                                v
# next_hypothesis_v2 -> dequeue_hypothesis_v2    compile_genome_v2
#                                                | render / next_seed /
#                                                | next_hypothesis / finalize
#                                                v
# render_candidate_v2 --render(self-loop)--> render_candidate_v2
#   | evaluate/next_seed/next_hyp/finalize   | materialize/next_seed/next_hyp/finalize
#   v
# evaluate_structure_and_basic_score_v2
#                                   v
# materialize_immutable_candidate_v2 -> select_hypothesis_best_v2
#   | select/next_seed/next_hyp/finalize       | next_seed/next_hyp/finalize
#                                              v
# next_seed_v2 -> dequeue_seed_v2
#
# select_cross_hypothesis_best_v2 -> promote_or_skip_v2 -> finalize_v2 -> END
#
# `measure_target_v2` 是 Graph 前 source->measurement 生产后的 Artifact 重放验证边界，
# 不把预先存在的 Measurements 谎称为本 Node 新计算结果。minimum_complexity seed 是
# 冻结 deterministic fallback；三个 seed 全失败才产生 no_valid_candidate。
def build_png_to_shader_v2_graph(
    runtime: PngToShaderV2NodeRuntime,
    *,
    checkpointer: Any = None,
    store: Any = None,
) -> Any:
    """装配 §12 正式节点序列；尚不注册为 product-active Graph。"""
    nodes = build_png_to_shader_v2_node_callables(runtime)
    if tuple(nodes) != PNG_TO_SHADER_V2_NODE_IDS:
        raise RuntimeError("V2 Graph nodes 与 production node registry 漂移。")

    graph = cast(Any, StateGraph(PngToShaderV2State))
    for node_id in PNG_TO_SHADER_V2_NODE_IDS:
        graph.add_node(node_id, nodes[node_id])

    graph.add_edge(START, "initialize_run_v2")
    graph.add_conditional_edges(
        "initialize_run_v2",
        route_after_initialize,
        {
            "prepare": "prepare_context_v2",
            "analyze": "analyze_visual_layers_v2",
            "build_intents": "build_intent_variants_v2",
            "dequeue_hypothesis": "dequeue_hypothesis_v2",
            "prepare_candidate": "prepare_candidate_attempt_v2",
            "compile": "compile_genome_v2",
            "render": "render_candidate_v2",
            "evaluate": "evaluate_structure_and_basic_score_v2",
            "materialize": "materialize_immutable_candidate_v2",
            "select_hypothesis": "select_hypothesis_best_v2",
            "cross_select": "select_cross_hypothesis_best_v2",
            "promote": "promote_or_skip_v2",
            "next_seed": "next_seed_v2",
            "next_hypothesis": "next_hypothesis_v2",
            "end": END,
        },
    )
    graph.add_edge("prepare_context_v2", "ingest_target_v2")
    graph.add_edge("ingest_target_v2", "measure_target_v2")
    graph.add_conditional_edges(
        "measure_target_v2",
        route_after_measurement,
        {"interpret": "analyze_visual_layers_v2", "finalize": "finalize_v2"},
    )
    graph.add_conditional_edges(
        "analyze_visual_layers_v2",
        route_after_interpretation,
        {"build_intents": "build_intent_variants_v2", "finalize": "finalize_v2"},
    )
    graph.add_conditional_edges(
        "build_intent_variants_v2",
        route_after_intent_build,
        {"dequeue_hypothesis": "dequeue_hypothesis_v2", "finalize": "finalize_v2"},
    )
    graph.add_conditional_edges(
        "dequeue_hypothesis_v2",
        route_after_hypothesis,
        {
            "plan_seeds": "plan_strategy_v2",
            "dequeue_seed": "dequeue_seed_v2",
            "cross_select": "select_cross_hypothesis_best_v2",
            "finalize": "finalize_v2",
        },
    )
    graph.add_conditional_edges(
        "plan_strategy_v2",
        route_after_strategy,
        {"propose_seeds": "propose_seed_plans_v2", "finalize": "finalize_v2"},
    )
    graph.add_conditional_edges(
        "propose_seed_plans_v2",
        route_after_seed_proposal,
        {"expand_seeds": "expand_validate_seeds_v2", "finalize": "finalize_v2"},
    )
    graph.add_conditional_edges(
        "expand_validate_seeds_v2",
        route_after_seed_planning,
        {"dequeue_seed": "dequeue_seed_v2", "finalize": "finalize_v2"},
    )
    graph.add_conditional_edges(
        "dequeue_seed_v2",
        route_after_seed,
        {
            "prepare_candidate": "prepare_candidate_attempt_v2",
            "next_hypothesis": "next_hypothesis_v2",
            "finalize": "finalize_v2",
        },
    )
    graph.add_conditional_edges(
        "prepare_candidate_attempt_v2",
        route_after_candidate_preparation,
        {"compile": "compile_genome_v2", "finalize": "finalize_v2"},
    )
    graph.add_conditional_edges(
        "compile_genome_v2",
        route_after_compile,
        {
            "render": "render_candidate_v2",
            "next_seed": "next_seed_v2",
            "next_hypothesis": "next_hypothesis_v2",
            "finalize": "finalize_v2",
        },
    )
    graph.add_conditional_edges(
        "render_candidate_v2",
        route_after_render,
        {
            "render": "render_candidate_v2",
            "evaluate": "evaluate_structure_and_basic_score_v2",
            "next_seed": "next_seed_v2",
            "next_hypothesis": "next_hypothesis_v2",
            "finalize": "finalize_v2",
        },
    )
    graph.add_conditional_edges(
        "evaluate_structure_and_basic_score_v2",
        route_after_evaluation,
        {
            "materialize": "materialize_immutable_candidate_v2",
            "next_seed": "next_seed_v2",
            "next_hypothesis": "next_hypothesis_v2",
            "finalize": "finalize_v2",
        },
    )
    graph.add_conditional_edges(
        "materialize_immutable_candidate_v2",
        route_after_materialization,
        {
            "select": "select_hypothesis_best_v2",
            "next_seed": "next_seed_v2",
            "next_hypothesis": "next_hypothesis_v2",
            "finalize": "finalize_v2",
        },
    )
    graph.add_conditional_edges(
        "select_hypothesis_best_v2",
        route_after_candidate_selection,
        {
            "next_seed": "next_seed_v2",
            "next_hypothesis": "next_hypothesis_v2",
            "finalize": "finalize_v2",
        },
    )
    graph.add_edge("next_seed_v2", "dequeue_seed_v2")
    graph.add_edge("next_hypothesis_v2", "dequeue_hypothesis_v2")
    graph.add_conditional_edges(
        "select_cross_hypothesis_best_v2",
        route_after_cross_selection,
        {"promote": "promote_or_skip_v2", "finalize": "finalize_v2"},
    )
    graph.add_edge("promote_or_skip_v2", "finalize_v2")
    graph.add_edge("finalize_v2", END)
    return graph.compile(
        checkpointer=checkpointer,
        store=store,
        name="PngToShaderV2Development",
    ).with_config({"recursion_limit": PNG_TO_SHADER_V2_RECURSION_LIMIT})


__all__ = [
    "PNG_TO_SHADER_V2_RECURSION_LIMIT",
    "build_png_to_shader_v2_graph",
]
