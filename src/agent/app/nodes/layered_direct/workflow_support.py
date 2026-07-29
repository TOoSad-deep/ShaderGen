"""Shared state-update helpers for LayerPlan Direct nodes."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from agent.app.contracts.layerplan_glsl_direct import AttemptLedger
from agent.app.states.layerplan_glsl_direct import LayerPlanGlslDirectState, NodeRoute


def trace(
    state: LayerPlanGlslDirectState,
    node_name: str,
) -> tuple[str, ...]:
    """Append a node name to the immutable execution trace."""
    return (*state.get("completed_nodes", ()), node_name)


def candidate_failure_route(
    state: LayerPlanGlslDirectState,
    success_route: NodeRoute,
) -> NodeRoute:
    """Route a failed candidate according to its initial/refine role."""
    if state.get("candidate_compiled_spec") is not None:
        return success_route
    if state.get("candidate_role") == "refine":
        return "decide_refinement"
    return "release_resources"


def render_failure_route(
    state: LayerPlanGlslDirectState,
    success_route: NodeRoute,
    success_key: str,
) -> NodeRoute:
    """Route a render-stage failure according to its candidate role."""
    if state.get(success_key) is not None:
        return success_route
    if state.get("candidate_role") == "refine":
        return "decide_refinement"
    return "release_resources"


def reject_candidate(
    state: LayerPlanGlslDirectState,
    error_code: str,
    *,
    ledger: AttemptLedger | None = None,
    **extra: Any,
) -> tuple[AttemptLedger, list[dict[str, Any]]]:
    """Record one candidate rejection without leaking private source text."""
    next_ledger = replace(ledger or state["direct_ledger"])
    next_ledger.rejected_candidates += 1
    layered_spec = state.get("candidate_layered_spec")
    compiled_spec = state.get("candidate_compiled_spec")
    events = [
        *state["events"],
        {
            "sequence": state["candidate_sequence"],
            "kind": state["candidate_role"],
            "ok": False,
            "error_code": error_code,
            "layered_spec_sha256": (
                layered_spec.layered_spec_sha256 if layered_spec is not None else None
            ),
            "spec_sha256": (
                compiled_spec.spec_sha256 if compiled_spec is not None else None
            ),
            **extra,
        },
    ]
    return next_ledger, events


__all__ = [
    "candidate_failure_route",
    "reject_candidate",
    "render_failure_route",
    "trace",
]
