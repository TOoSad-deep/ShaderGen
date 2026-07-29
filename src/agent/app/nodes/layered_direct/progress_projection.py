"""Public-safe progress projections for the Direct graph.

This module deliberately receives the private graph state but returns only the
small, documented values that may leave the Agent process.  It must never
forward a patch, uniform path/value, source, diagnostic, or graph state object.
"""

from __future__ import annotations

from typing import Any

from agent.app.states.layerplan_glsl_direct import (
    DirectGraphContext,
    LayerPlanGlslDirectState,
)

_UNIFORM_PROGRESS_NODES = frozenset(
    {"decide_uniform_optimization", "record_uniform_outcome"}
)
_SAFE_REASON_CODES = frozenset(
    {
        "target_reached",
        "global_draw_budget_exhausted",
        "global_compile_budget_exhausted",
        "uniform_tuning_budget_exhausted",
        "no_tunables",
        "no_feasible_components",
        "local_optimum",
        "dimension_cap_reached_local_optimum",
        "candidate_failures_exhausted",
        "renderer_unavailable",
        "uniform_tuning_active",
        "uniform_candidate_accepted",
        "uniform_candidate_rejected",
        "uniform_candidate_failed",
    }
)
_SAFE_REFINEMENT_REASONS = frozenset(
    {
        "target_reached",
        "refine_budget_exhausted",
        "patience_exhausted",
        "duplicate_patch",
        "hard_resource_block",
        "no_valid_candidate",
    }
)


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _safe_reason(value: Any, allowed: frozenset[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def public_uniform_progress_update(
    node_name: str,
    state: LayerPlanGlslDirectState,
    update: dict[str, Any],
    context: DirectGraphContext,
) -> dict[str, Any] | None:
    """Project a uniform decision/outcome into a safe incremental event."""
    if node_name not in _UNIFORM_PROGRESS_NODES:
        return None
    merged = {**state, **update}
    ledger = merged.get("direct_ledger")
    if ledger is None:
        return None
    draw_count = _non_negative_int(getattr(ledger, "uniform_tuning_draw_count", None))
    evaluated_count = _non_negative_int(
        getattr(ledger, "uniform_tuning_evaluated_count", None)
    )
    accepted_count = _non_negative_int(
        getattr(ledger, "uniform_tuning_accepted_count", None)
    )
    draw_budget = _non_negative_int(context.config.uniform_tuning_draw_budget)
    if None in {draw_count, evaluated_count, accepted_count, draw_budget}:
        return None

    uniform_stop_reason = _safe_reason(
        merged.get("uniform_tuning_stop_reason"), _SAFE_REASON_CODES
    )
    refinement_stop_reason = _safe_reason(
        merged.get("refinement_stop_reason"), _SAFE_REFINEMENT_REASONS
    )
    candidate_outcome: str | None = None
    if node_name == "record_uniform_outcome":
        if state.get("pending_candidate") is None:
            candidate_outcome = "failed"
        elif state.get("candidate_selected"):
            candidate_outcome = "accepted"
        else:
            candidate_outcome = "rejected"

    reason_code = (
        uniform_stop_reason
        or refinement_stop_reason
        or (
            f"uniform_candidate_{candidate_outcome}"
            if candidate_outcome is not None
            else "uniform_tuning_active"
        )
    )
    uniform: dict[str, Any] = {
        "draw_count": draw_count,
        "draw_budget": draw_budget,
        "evaluated_count": evaluated_count,
        "accepted_count": accepted_count,
    }
    if uniform_stop_reason is not None:
        uniform["stop_reason"] = uniform_stop_reason
    if candidate_outcome is not None:
        uniform["candidate_outcome"] = candidate_outcome
    return {
        "reason_code": reason_code,
        "refinement_stop_reason": refinement_stop_reason,
        "uniform_optimization": uniform,
    }


__all__ = ["public_uniform_progress_update"]
