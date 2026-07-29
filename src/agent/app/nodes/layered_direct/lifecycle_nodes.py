"""Refinement routing, cleanup, and finalization nodes."""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Literal

from langgraph.runtime import Runtime

from agent.app.contracts.layerplan_glsl_direct import (
    DirectAttemptResult,
    DirectEngineIdentity,
    DirectLedger,
    DirectPlanLedger,
    private_diagnostic_events,
    safe_failure_codes,
)
from agent.app.nodes.layered_direct.workflow_support import trace
from agent.app.states.layerplan_glsl_direct import (
    DirectGraphContext,
    LayerPlanGlslDirectState,
    NodeRoute,
)


def decide_refinement(
    state: LayerPlanGlslDirectState,
    runtime: Runtime[DirectGraphContext],
) -> dict[str, Any]:
    """Decide whether the bounded single-layer refinement loop continues."""
    should_refine = (
        state.get("current_best") is not None
        and state["refinement_count"] < runtime.context.config.refine_budget
        and not state.get("refinement_blocked", False)
    )
    return {
        "should_refine": should_refine,
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
        private_diagnostics=private_diagnostic_events(state["events"]),
    )
    return {
        "result": result,
        "completed_nodes": completed_nodes,
    }


__all__ = [
    "decide_refinement",
    "finalize_attempt",
    "release_resources",
    "route_refinement",
]
