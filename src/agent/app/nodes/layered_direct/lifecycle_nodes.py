"""Refinement routing, cleanup, and finalization nodes."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import Any, Literal

from langgraph.runtime import Runtime

from agent.app.contracts.layerplan_glsl_direct import (
    DirectAttemptResult,
    DirectEngineIdentity,
    DirectLedger,
    DirectPlanLedger,
    RefinementStopReason,
    private_diagnostic_events,
    safe_failure_codes,
)
from agent.app.nodes.layered_direct.workflow_support import trace
from agent.app.states.layerplan_glsl_direct import (
    DirectGraphContext,
    LayerPlanGlslDirectState,
    NodeRoute,
)
from shaderforge.program_spec import canonical_json


def _uniform_session_trace_sha256(
    uniform_trace: tuple[dict[str, Any], ...],
    trace_start: int | None,
) -> str | None:
    """Hash only the trace slice owned by the summary's current session."""
    if (
        not isinstance(trace_start, int)
        or trace_start < 0
        or trace_start > len(uniform_trace)
    ):
        return None
    summary_trace = uniform_trace[trace_start:]
    if not summary_trace:
        return None
    return sha256(canonical_json(list(summary_trace)).encode("utf-8")).hexdigest()


def decide_refinement(
    state: LayerPlanGlslDirectState,
    runtime: Runtime[DirectGraphContext],
) -> dict[str, Any]:
    """Decide whether the bounded single-layer refinement loop continues."""
    current_best = state.get("current_best")
    policy = state["optimization_policy"]
    config = runtime.context.config
    ledger = state["direct_ledger"]
    stop_reason: RefinementStopReason | None = None
    if current_best is None:
        stop_reason = "no_valid_candidate"
    elif (
        current_best.mae <= policy.target_mae
        and current_best.loss <= policy.target_loss
    ):
        stop_reason = "target_reached"
    elif state.get("refinement_blocked", False):
        stop_reason = "hard_resource_block"
    elif state.get("duplicate_patch_detected", False):
        stop_reason = "duplicate_patch"
    elif state["refinement_count"] >= config.refine_budget:
        stop_reason = "refine_budget_exhausted"
    elif state["consecutive_non_improving"] > policy.refinement_patience:
        stop_reason = "patience_exhausted"
    elif (
        ledger.llm_call_count >= config.direct_author_llm_budget
        or ledger.draw_count >= config.draw_budget
    ):
        stop_reason = "hard_resource_block"
    should_refine = stop_reason is None
    return {
        "should_refine": should_refine,
        "refinement_stop_reason": stop_reason,
        "completed_nodes": trace(state, "decide_refinement"),
    }


def route_refinement(state: LayerPlanGlslDirectState) -> NodeRoute:
    """Route to the next refinement attempt or resource release."""
    if state["should_refine"]:
        return "author_refinement"
    return "release_resources"


async def release_resources(
    state: LayerPlanGlslDirectState,
    runtime: Runtime[DirectGraphContext],
) -> dict[str, Any]:
    """Release all attempt-local prepared WebGL programs."""
    await runtime.context.release_programs()
    return {"completed_nodes": trace(state, "release_resources")}


def finalize_attempt(
    state: LayerPlanGlslDirectState,
    runtime: Runtime[DirectGraphContext],
) -> dict[str, Any]:
    """Freeze private graph state into the stable DirectAttemptResult contract."""
    config = runtime.context.config
    current_best = state.get("current_best")
    failure_code = state.get("failure_code")
    status: Literal["ok", "inconclusive"] = (
        "ok" if current_best is not None else "inconclusive"
    )
    if status == "inconclusive" and failure_code is None:
        failure_code = "no_valid_candidate"
    completed_nodes = trace(state, "finalize_attempt")
    uniform_trace = tuple(state["uniform_optimization_trace"])
    uniform_summary = state.get("uniform_optimization_summary")
    if uniform_summary is not None:
        trace_sha256 = _uniform_session_trace_sha256(
            uniform_trace,
            state.get("uniform_search_trace_start_index"),
        )
        if trace_sha256 is not None:
            uniform_summary = replace(
                uniform_summary,
                private_trace_sha256=trace_sha256,
            )
    result = DirectAttemptResult(
        status=status,
        failure_code=failure_code,
        safety_failure_codes=safe_failure_codes(state["events"], failure_code),
        identity=DirectEngineIdentity(
            implementation_identity_sha256=config.implementation_identity_sha256
        ),
        config=config,
        config_fingerprint=config.fingerprint(),
        reference_sha256=sha256(state["reference_image"]).hexdigest(),
        reference_content_type=state["content_type"],
        instruction_sha256=sha256(state["instruction"].encode("utf-8")).hexdigest(),
        canvas_width=state["canvas_width"],
        canvas_height=state["canvas_height"],
        layer_plan=state.get("layer_plan"),
        current_best=current_best,
        candidates=tuple(state["candidates"]),
        plan_ledger=DirectPlanLedger.from_mutable(state["plan_ledger"]),
        direct_ledger=DirectLedger.from_mutable(state["direct_ledger"]),
        optimization_policy=state["optimization_policy"],
        optimization_policy_fingerprint=state["optimization_policy"].fingerprint(),
        refinement_stop_reason=state.get("refinement_stop_reason")
        or (
            "no_valid_candidate" if current_best is None else "refine_budget_exhausted"
        ),
        non_improving_count=state["consecutive_non_improving"],
        duplicate_patch_count=state["duplicate_patch_count"],
        uniform_optimization_summary=uniform_summary,
        uniform_optimization_trace=uniform_trace,
        private_diagnostics=private_diagnostic_events(state["events"]),
    )
    return {
        "result": result,
        "completed_nodes": completed_nodes,
    }


__all__ = [
    "_uniform_session_trace_sha256",
    "decide_refinement",
    "finalize_attempt",
    "release_resources",
    "route_refinement",
]
