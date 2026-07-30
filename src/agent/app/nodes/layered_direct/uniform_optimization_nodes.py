"""Deterministic tunable-manifest optimization nodes for the Direct graph."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

from langgraph.runtime import Runtime

from agent.app.nodes.layered_direct.workflow_support import trace
from agent.app.states.layerplan_glsl_direct import (
    DirectGraphContext,
    LayerPlanGlslDirectState,
    NodeRoute,
)
from shaderforge.uniform_optimization import (
    UniformOptimizationConfig,
    UniformOptimizationError,
    UniformOptimizationProvenanceV1,
    UniformOptimizationSummaryV2,
    UniformPatchV1,
    active_components_sha256,
    apply_uniform_patch,
    component_identity_sha256,
    flatten_tunable_components,
    next_coordinate_move,
    record_coordinate_failure,
    record_coordinate_outcome,
    start_coordinate_pattern_session,
    validate_uniform_optimization_focus,
)

_GEOMETRY_METRICS = frozenset({"geometry_mask_loss", "edge_loss"})
_GEOMETRY_ROLES = frozenset({"subject", "detail", "highlight", "shadow"})


def _merged_state(
    state: LayerPlanGlslDirectState,
    update: dict[str, Any],
) -> LayerPlanGlslDirectState:
    """Return a detached state view for pure summary projection."""
    merged = dict(state)
    merged.update(update)
    return cast(LayerPlanGlslDirectState, merged)


def _target_reached(state: LayerPlanGlslDirectState) -> bool:
    best = state.get("current_best")
    policy = state["optimization_policy"]
    return (
        best is not None
        and best.mae <= policy.target_mae
        and best.loss <= policy.target_loss
    )


def _overlap_score(
    region: Any,
    tile: dict[str, Any] | None,
    confidence: float,
) -> float:
    if tile is None:
        return confidence
    bbox = tile.get("uv_bbox")
    if not isinstance(bbox, dict):
        return confidence
    try:
        x0 = max(float(region.x), float(bbox["x"]))
        y0 = max(float(region.y), float(bbox["y"]))
        x1 = min(
            float(region.x + region.width),
            float(bbox["x"]) + float(bbox["width"]),
        )
        y1 = min(
            float(region.y + region.height),
            float(bbox["y"]) + float(bbox["height"]),
        )
        tile_area = max(1e-12, float(bbox["width"]) * float(bbox["height"]))
        overlap = max(0.0, x1 - x0) * max(0.0, y1 - y0) / tile_area
        specificity = 2.0 - min(1.0, float(region.width * region.height))
        return overlap * confidence * specificity
    except (KeyError, TypeError, ValueError):
        return confidence


def _select_target_components(
    state: LayerPlanGlslDirectState,
    components: tuple[Any, ...],
) -> tuple[Any, ...]:
    plan = state.get("layer_plan")
    best = state.get("current_best")
    if plan is None or best is None or not components:
        return ()
    focus = getattr(best, "optimization_focus", None)
    if focus is not None:
        validation = validate_uniform_optimization_focus(
            focus,
            best.layered_spec,
            best.spec,
        )
        if validation.is_valid:
            return validation.components
    feasible = {component.layer_id for component in components}
    planned = [
        (index, layer)
        for index, layer in enumerate(plan.layers)
        if layer.layer_id in feasible
    ]
    if not planned:
        return ()
    dominant = best.residual_summary.get("dominant_metric_component")
    non_background = [item for item in planned if item[1].role != "background"]
    if dominant != "background_mae" and non_background:
        planned = non_background
    worst_tiles = best.residual_summary.get("worst_tiles")
    tile = (
        worst_tiles[0]
        if isinstance(worst_tiles, list)
        and worst_tiles
        and isinstance(worst_tiles[0], dict)
        else None
    )

    def rank(item: tuple[int, Any]) -> tuple[int, float, int]:
        index, layer = item
        if dominant == "background_mae":
            role_priority = 0 if layer.role == "background" else 1
        elif dominant in _GEOMETRY_METRICS:
            role_priority = 0 if layer.role in _GEOMETRY_ROLES else 1
        else:
            role_priority = 0
        return (
            role_priority,
            -_overlap_score(layer.region, tile, float(layer.confidence)),
            index,
        )

    _index, selected = min(planned, key=rank)
    return tuple(item for item in components if item.layer_id == selected.layer_id)


def _optimizer_config(context: DirectGraphContext) -> UniformOptimizationConfig:
    config = context.config
    return UniformOptimizationConfig(
        draw_budget=config.uniform_tuning_draw_budget,
        active_component_cap=config.uniform_tuning_active_component_cap,
        max_passes=config.uniform_tuning_max_passes,
    )


def _completed_sources(
    state: LayerPlanGlslDirectState,
    source_sha256: str,
) -> tuple[str, ...]:
    values = state["uniform_optimized_source_sha256s"]
    return values if source_sha256 in values else (*values, source_sha256)


def _summary(
    state: LayerPlanGlslDirectState,
    *,
    stop_reason: str,
) -> UniformOptimizationSummaryV2 | None:
    session = state.get("uniform_search_session")
    best = state.get("current_best")
    source_sha256 = state.get("uniform_search_source_sha256")
    base_spec = state.get("uniform_search_base_spec_sha256")
    selected_spec = state.get("uniform_search_selected_spec_sha256")
    initial_loss = state.get("uniform_search_initial_loss")
    initial_mae = state.get("uniform_search_initial_mae")
    selected_loss = state.get("uniform_search_selected_loss")
    selected_mae = state.get("uniform_search_selected_mae")
    initial_draw_count = state.get("uniform_search_initial_draw_count")
    if (
        session is None
        or best is None
        or source_sha256 != best.spec.source_sha256
        or base_spec is None
        or session.base_program_spec_sha256 != base_spec
        or selected_spec is None
        or initial_loss is None
        or initial_mae is None
        or selected_loss is None
        or selected_mae is None
        or initial_draw_count is None
    ):
        return None
    ledger = state["direct_ledger"]
    return UniformOptimizationSummaryV2(
        base_spec_sha256=base_spec,
        selected_spec_sha256=selected_spec,
        config_fingerprint=session.config.fingerprint(),
        active_component_count=len(session.components),
        evaluated_count=session.evaluated_count,
        accepted_count=session.accepted_count,
        draw_count=ledger.uniform_tuning_draw_count - initial_draw_count,
        draw_budget=session.config.draw_budget,
        initial_loss=initial_loss,
        initial_mae=initial_mae,
        final_loss=selected_loss,
        final_mae=selected_mae,
        loss_delta=initial_loss - selected_loss,
        mae_delta=initial_mae - selected_mae,
        stop_reason=stop_reason,
        algorithm_id=session.config.algorithm_id,
        algorithm_version=session.config.algorithm_version,
    )


def decide_uniform_optimization(
    state: LayerPlanGlslDirectState,
    runtime: Runtime[DirectGraphContext],
) -> dict[str, Any]:
    """Start, continue, or finish one source-scoped uniform search session."""
    context = runtime.context
    config = context.config
    ledger = replace(state["direct_ledger"])
    best = state.get("current_best")
    update: dict[str, Any] = {
        "should_uniform_optimize": False,
        "uniform_release_requested": False,
        "completed_nodes": trace(state, "decide_uniform_optimization"),
    }
    if best is None:
        return {
            **update,
            "uniform_release_requested": True,
            "uniform_tuning_stop_reason": "no_tunables",
        }
    source_sha256 = best.spec.source_sha256
    if _target_reached(state):
        return {
            **update,
            "uniform_release_requested": True,
            "uniform_tuning_stop_reason": "target_reached",
            "refinement_stop_reason": "target_reached",
            "uniform_optimization_summary": _summary(
                state, stop_reason="target_reached"
            ),
            "uniform_optimized_source_sha256s": _completed_sources(
                state, source_sha256
            ),
        }
    if (
        state.get("refinement_blocked", False)
        or ledger.draw_count >= config.draw_budget
    ):
        previous_reason = state.get("uniform_tuning_stop_reason")
        blocked_reason = (
            previous_reason
            if previous_reason
            in {
                "renderer_unavailable",
                "global_compile_budget_exhausted",
                "global_draw_budget_exhausted",
            }
            else "global_draw_budget_exhausted"
        )
        return {
            **update,
            "uniform_release_requested": True,
            "uniform_tuning_stop_reason": blocked_reason,
            "refinement_stop_reason": "hard_resource_block",
            "uniform_optimization_summary": _summary(state, stop_reason=blocked_reason),
            "uniform_optimized_source_sha256s": _completed_sources(
                state, source_sha256
            ),
        }
    session = state.get("uniform_search_session")
    if (
        session is not None
        and state.get("uniform_search_source_sha256") == source_sha256
        and session.stop_reason is not None
    ):
        return {
            **update,
            "uniform_tuning_stop_reason": session.stop_reason,
            "uniform_optimization_summary": _summary(
                state, stop_reason=session.stop_reason
            ),
            "uniform_optimized_source_sha256s": _completed_sources(
                state, source_sha256
            ),
        }
    if ledger.uniform_tuning_draw_count >= config.uniform_tuning_draw_budget:
        return {
            **update,
            "uniform_tuning_stop_reason": "uniform_tuning_budget_exhausted",
            "uniform_optimization_summary": _summary(
                state, stop_reason="uniform_tuning_budget_exhausted"
            ),
            "uniform_optimized_source_sha256s": _completed_sources(
                state, source_sha256
            ),
        }
    if (
        session is not None
        and state.get("uniform_search_source_sha256") == source_sha256
        and session.stop_reason is None
    ):
        return {
            **update,
            "should_uniform_optimize": True,
            "uniform_tuning_stop_reason": None,
        }
    if source_sha256 in state["uniform_optimized_source_sha256s"]:
        return update
    try:
        all_components = flatten_tunable_components(
            best.layered_spec,
            best.spec,
        )
        selected_components = _select_target_components(state, all_components)
        optimizer_config = _optimizer_config(context)
        session = start_coordinate_pattern_session(
            base_program_spec_sha256=best.spec.spec_sha256,
            components=selected_components,
            config=optimizer_config,
        )
    except UniformOptimizationError:
        return {
            **update,
            "uniform_search_session": None,
            "uniform_search_source_sha256": None,
            "uniform_search_base_spec_sha256": None,
            "uniform_search_selected_spec_sha256": None,
            "uniform_search_initial_loss": None,
            "uniform_search_initial_mae": None,
            "uniform_search_selected_loss": None,
            "uniform_search_selected_mae": None,
            "uniform_search_initial_draw_count": None,
            "uniform_search_trace_start_index": None,
            "uniform_tuning_stop_reason": "no_feasible_components",
            "uniform_optimization_summary": None,
            "uniform_optimized_source_sha256s": _completed_sources(
                state, source_sha256
            ),
        }
    stop_reason = session.stop_reason
    if not all_components:
        stop_reason = "no_tunables"
    elif not selected_components:
        stop_reason = "no_feasible_components"
    ledger.uniform_tuning_session_count += 1
    ledger.uniform_tuning_active_component_count += len(session.components)
    session_update: dict[str, Any] = {
        **update,
        "direct_ledger": ledger,
        "uniform_search_session": session,
        "uniform_search_source_sha256": source_sha256,
        "uniform_search_base_spec_sha256": best.spec.spec_sha256,
        "uniform_search_selected_spec_sha256": best.spec.spec_sha256,
        "uniform_search_initial_loss": best.loss,
        "uniform_search_initial_mae": best.mae,
        "uniform_search_selected_loss": best.loss,
        "uniform_search_selected_mae": best.mae,
        "uniform_search_initial_draw_count": ledger.uniform_tuning_draw_count,
        "uniform_search_trace_start_index": len(state["uniform_optimization_trace"]),
        "uniform_tuning_stop_reason": stop_reason,
        "uniform_optimization_summary": None,
    }
    if stop_reason is not None:
        session_update["uniform_optimized_source_sha256s"] = _completed_sources(
            state, source_sha256
        )
        session_update["uniform_optimization_summary"] = _summary(
            _merged_state(state, session_update), stop_reason=stop_reason
        )
        return session_update
    session_update["should_uniform_optimize"] = True
    return session_update


def route_uniform_decision(state: LayerPlanGlslDirectState) -> NodeRoute:
    """Route a convergence decision to numeric search, Refine, or release."""
    if state["uniform_release_requested"]:
        return "release_resources"
    if state["should_uniform_optimize"]:
        return "propose_uniform_candidate"
    return "decide_refinement"


def route_after_candidate_selection(state: LayerPlanGlslDirectState) -> NodeRoute:
    """Record optimizer outcomes; otherwise offer cheap tuning before Refine."""
    if state["candidate_role"] == "uniform_optimize":
        return "record_uniform_outcome"
    return "decide_uniform_optimization"


def propose_uniform_candidate(
    state: LayerPlanGlslDirectState,
    runtime: Runtime[DirectGraphContext],
) -> dict[str, Any]:
    """Propose the next deterministic lattice move without rendering it."""
    session = state["uniform_search_session"]
    best = state["current_best"]
    assert session is not None and best is not None
    session, move = next_coordinate_move(session)
    if move is None:
        reason = session.stop_reason or "local_optimum"
        return {
            "uniform_search_session": session,
            "uniform_pending_move": None,
            "uniform_candidate_patch": None,
            "uniform_tuning_stop_reason": reason,
            "uniform_optimized_source_sha256s": _completed_sources(
                state, best.spec.source_sha256
            ),
            "uniform_optimization_summary": _summary(
                _merged_state(state, {"uniform_search_session": session}),
                stop_reason=reason,
            ),
            "completed_nodes": trace(state, "propose_uniform_candidate"),
        }
    provenance = UniformOptimizationProvenanceV1(
        parent_layered_spec_sha256=best.layered_spec.layered_spec_sha256,
        parent_program_spec_sha256=best.spec.spec_sha256,
        optimizer_config_fingerprint=session.config.fingerprint(),
        active_components_sha256=active_components_sha256(session.components),
        component_identity_sha256=component_identity_sha256(move.component),
        move_ordinal=move.ordinal,
        tick=move.tick,
        direction=move.direction,
        algorithm_id=session.config.algorithm_id,
        algorithm_version=session.config.algorithm_version,
    )
    patch = UniformPatchV1(
        base_layered_spec_sha256=best.layered_spec.layered_spec_sha256,
        base_program_spec_sha256=best.spec.spec_sha256,
        target_layer_id=move.component.layer_id,
        path=move.component.path,
        component_index=move.component.component_index,
        lattice_base_value=move.component.base_value,
        expected_value=move.expected_value,
        replacement_value=move.replacement_value,
        tick=move.tick,
        derivation=provenance,
    )
    return {
        "uniform_search_session": session,
        "uniform_pending_move": move,
        "uniform_candidate_patch": patch,
        "uniform_candidate_failed": False,
        "completed_nodes": trace(state, "propose_uniform_candidate"),
    }


def route_uniform_proposal(state: LayerPlanGlslDirectState) -> NodeRoute:
    """Apply a proposed move or return to structural Refine after convergence."""
    if state.get("uniform_candidate_patch") is not None:
        return "apply_uniform_candidate"
    return "decide_refinement"


def apply_uniform_candidate(
    state: LayerPlanGlslDirectState,
    runtime: Runtime[DirectGraphContext],
) -> dict[str, Any]:
    """Apply one trusted patch and expose the derived Layered candidate."""
    del runtime
    best = state["current_best"]
    patch = state["uniform_candidate_patch"]
    assert best is not None and patch is not None
    sequence = state["next_sequence"]
    try:
        applied = apply_uniform_patch(best.layered_spec, best.spec, patch)
    except UniformOptimizationError as exc:
        ledger = replace(state["direct_ledger"])
        ledger.rejected_candidates += 1
        return {
            "candidate_role": "uniform_optimize",
            "candidate_sequence": sequence,
            "candidate_layered_spec": None,
            "candidate_compiled_spec": None,
            "pending_candidate": None,
            "uniform_candidate_failed": True,
            "direct_ledger": ledger,
            "events": [
                *state["events"],
                {
                    "sequence": sequence,
                    "kind": "uniform_optimize",
                    "ok": False,
                    "error_code": exc.code,
                },
            ],
            "next_sequence": sequence + 1,
            "completed_nodes": trace(state, "apply_uniform_candidate"),
        }
    candidate_binding_identity = (
        applied.program_spec.source_sha256,
        applied.program_spec.binding_sha256,
    )
    duplicate = any(
        (candidate.spec.source_sha256, candidate.spec.binding_sha256)
        == candidate_binding_identity
        for candidate in state["candidates"]
    )
    if duplicate:
        ledger = replace(state["direct_ledger"])
        ledger.rejected_candidates += 1
        ledger.uniform_tuning_duplicate_count += 1
        return {
            "candidate_role": "uniform_optimize",
            "candidate_sequence": sequence,
            "candidate_layered_spec": None,
            "candidate_compiled_spec": None,
            "pending_candidate": None,
            "uniform_candidate_failed": True,
            "direct_ledger": ledger,
            "events": [
                *state["events"],
                {
                    "sequence": sequence,
                    "kind": "uniform_optimize",
                    "ok": False,
                    "error_code": "duplicate_uniform_candidate",
                },
            ],
            "next_sequence": sequence + 1,
            "completed_nodes": trace(state, "apply_uniform_candidate"),
        }
    return {
        "candidate_role": "uniform_optimize",
        "candidate_sequence": sequence,
        "candidate_layered_spec": applied.layered_spec,
        "candidate_compiled_spec": None,
        "candidate_attested_spec": None,
        "candidate_parent_sha256": best.layered_spec.layered_spec_sha256,
        "candidate_patched_layer_id": patch.target_layer_id,
        "candidate_optimization_focus": getattr(best, "optimization_focus", None),
        "pending_candidate": None,
        "prepared_cache_key": None,
        "candidate_cache_hit": False,
        "draw_result": None,
        "verified_receipt": None,
        "uniform_candidate_failed": False,
        "next_sequence": sequence + 1,
        "completed_nodes": trace(state, "apply_uniform_candidate"),
    }


def route_after_uniform_apply(state: LayerPlanGlslDirectState) -> NodeRoute:
    """Compile a trusted derivation or record its fail-closed rejection."""
    if state.get("candidate_layered_spec") is not None:
        return "compile_candidate"
    return "record_uniform_outcome"


def _hard_failure_stop_reason(
    state: LayerPlanGlslDirectState,
) -> str | None:
    event = state["events"][-1] if state["events"] else {}
    code = event.get("error_code")
    if code == "renderer_unavailable":
        return "renderer_unavailable"
    if code == "compile_budget_exhausted":
        return "global_compile_budget_exhausted"
    if code == "draw_budget_exhausted":
        return "global_draw_budget_exhausted"
    if state.get("refinement_blocked", False):
        return "global_draw_budget_exhausted"
    return None


def record_uniform_outcome(
    state: LayerPlanGlslDirectState,
    runtime: Runtime[DirectGraphContext],
) -> dict[str, Any]:
    """Advance the pure search state from one real candidate outcome."""
    del runtime
    session = state["uniform_search_session"]
    move = state["uniform_pending_move"]
    best = state["current_best"]
    assert session is not None and move is not None and best is not None
    ledger = replace(state["direct_ledger"])
    patch = state["uniform_candidate_patch"]
    assert patch is not None
    candidate = state.get("pending_candidate")
    event = state["events"][-1] if state["events"] else {}
    trace_item: dict[str, Any] = {
        "parent_layered_spec_sha256": patch.base_layered_spec_sha256,
        "parent_program_spec_sha256": patch.base_program_spec_sha256,
        "component_identity_sha256": patch.derivation.component_identity_sha256,
        "move_ordinal": move.ordinal,
        "tick": move.tick,
        "direction": move.direction,
        "candidate_spec_sha256": (
            candidate.spec.spec_sha256 if candidate is not None else None
        ),
        "loss": candidate.loss if candidate is not None else None,
        "mae": candidate.mae if candidate is not None else None,
        "selected": bool(state.get("candidate_selected", False))
        if candidate is not None
        else False,
        "material_improvement": bool(state.get("candidate_material_improvement", False))
        if candidate is not None
        else False,
        "failure_code": (
            event.get("error_code")
            if candidate is None and isinstance(event.get("error_code"), str)
            else None
        ),
    }
    private_trace = [*state["uniform_optimization_trace"], trace_item]
    if candidate is None:
        hard_reason = _hard_failure_stop_reason(state)
        if hard_reason is not None:
            stopped = replace(session, stop_reason=hard_reason)
            return {
                "uniform_search_session": stopped,
                "uniform_tuning_stop_reason": hard_reason,
                "uniform_optimized_source_sha256s": _completed_sources(
                    state, best.spec.source_sha256
                ),
                "uniform_optimization_summary": _summary(
                    _merged_state(state, {"uniform_search_session": stopped}),
                    stop_reason=hard_reason,
                ),
                "refinement_blocked": True,
                "uniform_candidate_patch": None,
                "uniform_pending_move": None,
                "uniform_candidate_failed": False,
                "uniform_optimization_trace": private_trace,
                "completed_nodes": trace(state, "record_uniform_outcome"),
            }
        session = record_coordinate_failure(session, move)
        failure_update: dict[str, Any] = {
            "uniform_search_session": session,
            "uniform_candidate_patch": None,
            "uniform_pending_move": None,
            "uniform_candidate_failed": False,
            "uniform_optimization_trace": private_trace,
            "completed_nodes": trace(state, "record_uniform_outcome"),
        }
        if session.stop_reason is not None:
            failure_update["uniform_tuning_stop_reason"] = session.stop_reason
            failure_update["uniform_optimized_source_sha256s"] = _completed_sources(
                state, best.spec.source_sha256
            )
            failure_update["uniform_optimization_summary"] = _summary(
                _merged_state(state, failure_update),
                stop_reason=session.stop_reason,
            )
        return failure_update
    selected = state["candidate_selected"]
    material = state["candidate_material_improvement"]
    session = record_coordinate_outcome(
        session,
        move,
        selected=selected,
        material_improvement=material,
    )
    ledger.uniform_tuning_evaluated_count += 1
    ledger.uniform_tuning_accepted_count += int(selected)
    update: dict[str, Any] = {
        "uniform_search_session": session,
        "uniform_candidate_patch": None,
        "uniform_pending_move": None,
        "uniform_candidate_failed": False,
        "direct_ledger": ledger,
        "uniform_optimization_trace": private_trace,
        "completed_nodes": trace(state, "record_uniform_outcome"),
    }
    if selected:
        update.update(
            {
                "uniform_search_selected_spec_sha256": candidate.spec.spec_sha256,
                "uniform_search_selected_loss": candidate.loss,
                "uniform_search_selected_mae": candidate.mae,
            }
        )
    if session.stop_reason is not None:
        update["uniform_tuning_stop_reason"] = session.stop_reason
        update["uniform_optimized_source_sha256s"] = _completed_sources(
            state, best.spec.source_sha256
        )
        update["uniform_optimization_summary"] = _summary(
            _merged_state(state, update),
            stop_reason=session.stop_reason,
        )
    return update


__all__ = [
    "apply_uniform_candidate",
    "decide_uniform_optimization",
    "propose_uniform_candidate",
    "record_uniform_outcome",
    "route_after_candidate_selection",
    "route_after_uniform_apply",
    "route_uniform_decision",
    "route_uniform_proposal",
]
