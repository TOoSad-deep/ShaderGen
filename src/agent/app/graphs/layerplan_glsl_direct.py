"""LangGraph wiring for one LayerPlan-driven direct GLSL attempt.

Flow::

    START -> prepare_reference -> author_layer_plan
      -> author_initial -> compile_candidate -> validate_candidate
      -> prepare_program -> render_program -> verify_receipt
      -> attest_candidate -> evaluate_candidate -> select_candidate
      -> decide_uniform_optimization
           | tune -> propose_uniform_candidate -> apply_uniform_candidate
           |         -> compile_candidate ... -> select_candidate
           |         -> record_uniform_outcome -> decide_uniform_optimization
           ` local optimum -> decide_refinement
                    | refine -> author_refinement -> apply_refinement
                    |           -> compile_candidate
                    ` done/hard block -> release_resources
      -> finalize_attempt -> END
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from inspect import isawaitable, iscoroutinefunction
from time import perf_counter
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langsmith import tracing_context

from agent.app.nodes.layered_direct.candidate_nodes import (
    attest_candidate,
    compile_candidate,
    evaluate_candidate,
    prepare_program,
    render_program,
    route_after_attestation,
    route_after_compile,
    route_after_prepare,
    route_after_receipt,
    route_after_render,
    route_after_validation,
    select_candidate,
    validate_candidate,
    verify_receipt,
)
from agent.app.nodes.layered_direct.lifecycle_nodes import (
    decide_refinement,
    finalize_attempt,
    release_resources,
    route_refinement,
)
from agent.app.nodes.layered_direct.progress_projection import (
    public_uniform_progress_update,
)
from agent.app.nodes.layered_direct.uniform_optimization_nodes import (
    apply_uniform_candidate,
    decide_uniform_optimization,
    propose_uniform_candidate,
    record_uniform_outcome,
    route_after_candidate_selection,
    route_after_uniform_apply,
    route_uniform_decision,
    route_uniform_proposal,
)
from agent.app.nodes.layered_direct.workflow_author_nodes import (
    apply_refinement,
    author_initial,
    author_layer_plan,
    author_refinement,
    prepare_reference,
    route_after_authored_candidate,
    route_after_layer_plan,
    route_after_refinement_author,
)
from agent.app.states.layerplan_glsl_direct import (
    DIRECT_GRAPH_NODE_NAMES,
    DirectGraphContext,
    LayerPlanGlslDirectInput,
    LayerPlanGlslDirectOutput,
    LayerPlanGlslDirectState,
)

GraphNode = Callable[
    [LayerPlanGlslDirectState, Runtime[DirectGraphContext]],
    dict[str, Any] | Awaitable[dict[str, Any]],
]
ObservedGraphNode = Callable[
    [LayerPlanGlslDirectState, Runtime[DirectGraphContext]],
    Coroutine[Any, Any, dict[str, Any]],
]


def _with_safe_progress(node_name: str, node: GraphNode) -> ObservedGraphNode:
    """Wrap one graph node with best-effort, state-free lifecycle progress."""
    node_is_async = iscoroutinefunction(node)

    async def observed(
        state: LayerPlanGlslDirectState,
        runtime: Runtime[DirectGraphContext],
    ) -> dict[str, Any]:
        started_at = perf_counter()
        runtime.context.publish_node_progress(node_name, "running")
        try:
            result = (
                node(state, runtime)
                if node_is_async
                else await asyncio.to_thread(node, state, runtime)
            )
            if isawaitable(result):
                result = await result
        except BaseException:
            runtime.context.publish_node_progress(
                node_name,
                "failed",
                (perf_counter() - started_at) * 1000,
            )
            raise
        progress_update = public_uniform_progress_update(
            node_name,
            state,
            result,
            runtime.context,
        )
        runtime.context.publish_node_progress(
            node_name,
            "completed",
            (perf_counter() - started_at) * 1000,
            progress_update,
        )
        return result

    return observed


_NODE_IMPLEMENTATIONS: dict[str, GraphNode] = {
    "prepare_reference": prepare_reference,
    "author_layer_plan": author_layer_plan,
    "author_initial": author_initial,
    "compile_candidate": compile_candidate,
    "validate_candidate": validate_candidate,
    "prepare_program": prepare_program,
    "render_program": render_program,
    "verify_receipt": verify_receipt,
    "attest_candidate": attest_candidate,
    "evaluate_candidate": evaluate_candidate,
    "select_candidate": select_candidate,
    "decide_uniform_optimization": decide_uniform_optimization,
    "propose_uniform_candidate": propose_uniform_candidate,
    "apply_uniform_candidate": apply_uniform_candidate,
    "record_uniform_outcome": record_uniform_outcome,
    "decide_refinement": decide_refinement,
    "author_refinement": author_refinement,
    "apply_refinement": apply_refinement,
    "release_resources": release_resources,
    "finalize_attempt": finalize_attempt,
}
if tuple(_NODE_IMPLEMENTATIONS) != DIRECT_GRAPH_NODE_NAMES:
    raise RuntimeError("Direct graph node catalog is out of sync")


def build_layerplan_glsl_direct_graph() -> CompiledStateGraph[
    LayerPlanGlslDirectState,
    DirectGraphContext,
    LayerPlanGlslDirectInput,
    LayerPlanGlslDirectOutput,
]:
    """Build the compiled LayerPlan Direct workflow."""
    builder = StateGraph(
        LayerPlanGlslDirectState,
        context_schema=DirectGraphContext,
        input_schema=LayerPlanGlslDirectInput,
        output_schema=LayerPlanGlslDirectOutput,
    )
    for node_name, node in _NODE_IMPLEMENTATIONS.items():
        # LangGraph's overloads do not model a generic wrapper over mixed
        # synchronous/asynchronous runtime-aware nodes.
        builder.add_node(  # type: ignore[call-overload]
            node_name,
            _with_safe_progress(node_name, node),
        )

    builder.add_edge(START, "prepare_reference")
    builder.add_edge("prepare_reference", "author_layer_plan")
    builder.add_conditional_edges(
        "author_layer_plan",
        route_after_layer_plan,
        {
            "author_initial": "author_initial",
            "release_resources": "release_resources",
        },
    )
    builder.add_conditional_edges(
        "author_initial",
        route_after_authored_candidate,
        {
            "compile_candidate": "compile_candidate",
            "release_resources": "release_resources",
        },
    )
    builder.add_conditional_edges(
        "compile_candidate",
        route_after_compile,
        {
            "validate_candidate": "validate_candidate",
            "decide_refinement": "decide_refinement",
            "record_uniform_outcome": "record_uniform_outcome",
            "release_resources": "release_resources",
        },
    )
    builder.add_conditional_edges(
        "validate_candidate",
        route_after_validation,
        {
            "prepare_program": "prepare_program",
            "decide_refinement": "decide_refinement",
            "record_uniform_outcome": "record_uniform_outcome",
            "release_resources": "release_resources",
        },
    )
    builder.add_conditional_edges(
        "prepare_program",
        route_after_prepare,
        {
            "render_program": "render_program",
            "decide_refinement": "decide_refinement",
            "record_uniform_outcome": "record_uniform_outcome",
            "release_resources": "release_resources",
        },
    )
    builder.add_conditional_edges(
        "render_program",
        route_after_render,
        {
            "verify_receipt": "verify_receipt",
            "decide_refinement": "decide_refinement",
            "record_uniform_outcome": "record_uniform_outcome",
            "release_resources": "release_resources",
        },
    )
    builder.add_conditional_edges(
        "verify_receipt",
        route_after_receipt,
        {
            "attest_candidate": "attest_candidate",
            "decide_refinement": "decide_refinement",
            "record_uniform_outcome": "record_uniform_outcome",
            "release_resources": "release_resources",
        },
    )
    builder.add_conditional_edges(
        "attest_candidate",
        route_after_attestation,
        {
            "evaluate_candidate": "evaluate_candidate",
            "decide_refinement": "decide_refinement",
            "record_uniform_outcome": "record_uniform_outcome",
            "release_resources": "release_resources",
        },
    )
    builder.add_edge("evaluate_candidate", "select_candidate")
    builder.add_conditional_edges(
        "select_candidate",
        route_after_candidate_selection,
        {
            "record_uniform_outcome": "record_uniform_outcome",
            "decide_uniform_optimization": "decide_uniform_optimization",
        },
    )
    builder.add_conditional_edges(
        "decide_uniform_optimization",
        route_uniform_decision,
        {
            "propose_uniform_candidate": "propose_uniform_candidate",
            "decide_refinement": "decide_refinement",
            "release_resources": "release_resources",
        },
    )
    builder.add_conditional_edges(
        "propose_uniform_candidate",
        route_uniform_proposal,
        {
            "apply_uniform_candidate": "apply_uniform_candidate",
            "decide_refinement": "decide_refinement",
        },
    )
    builder.add_conditional_edges(
        "apply_uniform_candidate",
        route_after_uniform_apply,
        {
            "compile_candidate": "compile_candidate",
            "record_uniform_outcome": "record_uniform_outcome",
        },
    )
    builder.add_edge("record_uniform_outcome", "decide_uniform_optimization")
    builder.add_conditional_edges(
        "decide_refinement",
        route_refinement,
        {
            "author_refinement": "author_refinement",
            "release_resources": "release_resources",
        },
    )
    builder.add_conditional_edges(
        "author_refinement",
        route_after_refinement_author,
        {
            "apply_refinement": "apply_refinement",
            "decide_refinement": "decide_refinement",
        },
    )
    builder.add_conditional_edges(
        "apply_refinement",
        route_after_authored_candidate,
        {
            "compile_candidate": "compile_candidate",
            "decide_refinement": "decide_refinement",
        },
    )
    builder.add_edge("release_resources", "finalize_attempt")
    builder.add_edge("finalize_attempt", END)
    return builder.compile(name="layerplan_glsl_direct")


_attempt_graph = build_layerplan_glsl_direct_graph()


async def run_layerplan_glsl_direct_graph(
    *,
    reference_image: bytes,
    content_type: str,
    instruction: str,
    context: DirectGraphContext,
) -> LayerPlanGlslDirectOutput:
    """Invoke the compiled graph and guarantee resource release on exceptions."""
    graph_input: LayerPlanGlslDirectInput = {
        "reference_image": reference_image,
        "content_type": content_type,
        "instruction": instruction,
    }
    with tracing_context(enabled=False, parent=False):
        try:
            output = await _attempt_graph.ainvoke(
                graph_input,
                context=context,
            )
            return LayerPlanGlslDirectOutput(
                result=output["result"],
                completed_nodes=output["completed_nodes"],
            )
        finally:
            await context.release_programs()


__all__ = [
    "DirectGraphContext",
    "LayerPlanGlslDirectInput",
    "LayerPlanGlslDirectOutput",
    "LayerPlanGlslDirectState",
    "build_layerplan_glsl_direct_graph",
    "run_layerplan_glsl_direct_graph",
]
