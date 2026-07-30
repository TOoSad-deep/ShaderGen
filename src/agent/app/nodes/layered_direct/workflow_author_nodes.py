"""Reference preparation and model-authoring nodes."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import Any

import numpy as np
from langgraph.runtime import Runtime
from PIL import Image

from agent.app.contracts.layerplan_glsl_direct import (
    TERMINAL_REFINEMENT_FAILURE_CODES,
    AttemptLedger,
    DirectCandidate,
    PlanLedger,
    accumulate_token_usage,
    border_background,
    decode_reference,
    derive_canvas,
    normalize_author_failure,
    program_cache_key,
)
from agent.app.nodes.layered_direct.authors import (
    ValidatedLayeredIncumbent,
    run_initial_layered_glsl_author,
    run_refine_layered_glsl_author,
)
from agent.app.nodes.layered_direct.layer_plan_author import run_visual_analysis_author
from agent.app.nodes.layered_direct.workflow_support import (
    refine_failure_update,
    trace,
)
from agent.app.states.layerplan_glsl_direct import (
    DirectGraphContext,
    LayerPlanGlslDirectState,
    NodeRoute,
)
from shaderforge.evaluation import ROLE_ALPHA_MASK_PASSES, decode_role_alpha_masks
from shaderforge.layered_spec import LayeredSpecError, apply_layer_patch
from shaderforge.program_spec import canonical_json
from shaderforge.program_spec.models import LayerRole
from shaderforge.rendering import RendererUnavailableError
from shaderforge.uniform_optimization import (
    UniformOptimizationFocusV1,
    UniformOptimizationSummaryV2,
)

_FOCUS_SCHEMA_VERSION = "uniform_optimization_focus_v1"


def _optimization_focus_from_payload(
    payload: Any,
) -> UniformOptimizationFocusV1 | None:
    """Promote a syntactically safe model sidecar into a trusted value object."""
    if payload is None:
        return None
    try:
        return UniformOptimizationFocusV1.from_dict(
            {"schema_version": _FOCUS_SCHEMA_VERSION, **dict(payload)}
        )
    except (TypeError, ValueError):
        return None


def _uniform_summary_for_refine(
    current_best: DirectCandidate,
    summary: UniformOptimizationSummaryV2 | None,
) -> UniformOptimizationSummaryV2 | None:
    """Expose only an optimizer summary bound to the exact incumbent Spec."""
    if summary is None or summary.selected_spec_sha256 != current_best.spec.spec_sha256:
        return None
    return summary


async def _render_role_alpha_masks(
    state: LayerPlanGlslDirectState,
    context: DirectGraphContext,
    current_best: DirectCandidate,
    ledger: AttemptLedger,
) -> tuple[dict[LayerRole, bytes], AttemptLedger, list[dict[str, Any]]]:
    """Best-effort packed role-mask draws from the incumbent prepared program."""
    updated_ledger = replace(ledger)
    events = list(state["events"])
    prepared = context.program_cache.get(program_cache_key(current_best.spec))
    layer_plan = state.get("layer_plan")
    if prepared is None or layer_plan is None:
        return {}, updated_ledger, events
    planned_roles = {layer.role for layer in layer_plan.layers}
    remaining = min(
        2,
        context.config.role_mask_draw_budget - updated_ledger.role_mask_draw_count,
        context.config.draw_budget - updated_ledger.draw_count,
    )
    if remaining <= 0:
        return {}, updated_ledger, events

    masks: dict[LayerRole, bytes] = {}
    for diagnostic_mode, roles in ROLE_ALPHA_MASK_PASSES.items():
        if remaining <= 0:
            break
        if not planned_roles.intersection(roles):
            continue
        updated_ledger.draw_count += 1
        updated_ledger.role_mask_draw_count += 1
        remaining -= 1
        started = context.clock()
        try:
            draw = await prepared.render_uniforms(
                dict(current_best.spec.uniform_values),
                capture_png=False,
                diagnostic_mode=float(diagnostic_mode),
            )
            if not draw.success or draw.rgb_bytes is None:
                raise ValueError("role mask diagnostic draw failed")
            decoded = decode_role_alpha_masks(
                draw.rgb_bytes,
                draw.width,
                draw.height,
                roles,
            )
        except RendererUnavailableError as exc:
            updated_ledger.wall_clock_ms += (context.clock() - started) * 1000.0
            # A renderer worker reset invalidates every prepared handle, not only
            # the mask program that observed the failure. Clear the graph cache so
            # a same-source Refine must prepare a fresh program.
            await context.release_programs()
            events.append(
                {
                    "sequence": current_best.sequence,
                    "kind": "role_alpha_mask",
                    "ok": True,
                    "available": False,
                    "diagnostic_mode": diagnostic_mode,
                    "error_type": type(exc).__name__,
                }
            )
            break
        except (ValueError, OSError) as exc:
            updated_ledger.wall_clock_ms += (context.clock() - started) * 1000.0
            events.append(
                {
                    "sequence": current_best.sequence,
                    "kind": "role_alpha_mask",
                    "ok": True,
                    "available": False,
                    "diagnostic_mode": diagnostic_mode,
                    "error_type": type(exc).__name__,
                }
            )
            continue
        updated_ledger.wall_clock_ms += (context.clock() - started) * 1000.0
        selected = {
            role: mask for role, mask in decoded.items() if role in planned_roles
        }
        masks.update({role: mask.png_bytes for role, mask in selected.items()})
        events.append(
            {
                "sequence": current_best.sequence,
                "kind": "role_alpha_mask",
                "ok": True,
                "diagnostic_mode": diagnostic_mode,
                "masks": [mask.to_dict() for mask in selected.values()],
            }
        )
    return masks, updated_ledger, events


def prepare_reference(
    state: LayerPlanGlslDirectState,
    runtime: Runtime[DirectGraphContext],
) -> dict[str, Any]:
    """Decode and normalize the reference image for the working canvas."""
    image = decode_reference(state["reference_image"])
    config = runtime.context.config
    canvas_width, canvas_height = (
        (config.canvas_width, config.canvas_height)
        if config.canvas_width is not None and config.canvas_height is not None
        else derive_canvas(image)
    )
    assert canvas_width is not None and canvas_height is not None
    if (canvas_width, canvas_height) != image.size:
        image = image.resize(
            (canvas_width, canvas_height),
            Image.Resampling.LANCZOS,
        )
    target_rgb = np.asarray(image, dtype=np.float32) / 255.0
    return {
        "target_rgb": target_rgb,
        "background": border_background(target_rgb),
        "canvas_width": canvas_width,
        "canvas_height": canvas_height,
        "next_sequence": 1,
        "plan_ledger": PlanLedger(),
        "direct_ledger": AttemptLedger(),
        "events": [],
        "candidates": [],
        "current_best": None,
        "candidate_optimization_focus": None,
        "refinement_count": 0,
        "refinement_blocked": False,
        "optimization_policy": runtime.context.optimization_policy,
        "consecutive_non_improving": 0,
        "previous_refine_feedback": None,
        "attempted_patch_fingerprints": (),
        "duplicate_patch_detected": False,
        "duplicate_patch_count": 0,
        "refinement_stop_reason": None,
        "candidate_selected": False,
        "candidate_loss_delta": None,
        "candidate_mae_delta": None,
        "candidate_material_improvement": False,
        "should_uniform_optimize": False,
        "uniform_release_requested": False,
        "uniform_search_session": None,
        "uniform_pending_move": None,
        "uniform_candidate_patch": None,
        "uniform_optimized_source_sha256s": (),
        "uniform_search_source_sha256": None,
        "uniform_search_base_spec_sha256": None,
        "uniform_search_selected_spec_sha256": None,
        "uniform_search_initial_loss": None,
        "uniform_search_initial_mae": None,
        "uniform_search_selected_loss": None,
        "uniform_search_selected_mae": None,
        "uniform_search_initial_draw_count": None,
        "uniform_search_trace_start_index": None,
        "uniform_tuning_stop_reason": None,
        "uniform_candidate_failed": False,
        "uniform_optimization_summary": None,
        "uniform_optimization_trace": [],
        "failure_code": None,
        "completed_nodes": trace(state, "prepare_reference"),
    }


async def author_layer_plan(
    state: LayerPlanGlslDirectState,
    runtime: Runtime[DirectGraphContext],
) -> dict[str, Any]:
    """Generate the canonical advisory LayerPlan."""
    context = runtime.context
    config = context.config
    ledger = replace(state["plan_ledger"])
    sequence = state["next_sequence"]
    started = context.clock()
    plan_result = await run_visual_analysis_author(
        gateway=context.gateway,
        reference_image=state["reference_image"],
        content_type=state["content_type"],
        user_instruction=state["instruction"],
        remaining_calls=config.plan_llm_budget - ledger.llm_call_count,
    )
    ledger.wall_clock_ms += (context.clock() - started) * 1000.0
    ledger.llm_call_count += plan_result.call_count
    ledger.total_tokens = accumulate_token_usage(
        ledger.total_tokens,
        plan_result.total_tokens,
        call_count=plan_result.call_count,
    )
    ledger.repair_count += 1 if plan_result.repaired else 0
    layer_plan = plan_result.plan
    events = [
        *state["events"],
        {
            "sequence": sequence,
            "kind": "visual_analysis",
            "ok": layer_plan is not None,
            "error_code": plan_result.error_code,
            "repaired": plan_result.repaired,
            "call_count": plan_result.call_count,
        },
    ]
    return {
        "layer_plan": layer_plan,
        "plan_ledger": ledger,
        "events": events,
        "next_sequence": sequence + 1,
        "failure_code": (
            None if layer_plan is not None else "layer_plan_generation_failed"
        ),
        "completed_nodes": trace(state, "author_layer_plan"),
    }


def route_after_layer_plan(state: LayerPlanGlslDirectState) -> NodeRoute:
    """Continue only when the LayerPlan author produced a valid plan."""
    if state.get("layer_plan") is None:
        return "release_resources"
    return "author_initial"


async def author_initial(
    state: LayerPlanGlslDirectState,
    runtime: Runtime[DirectGraphContext],
) -> dict[str, Any]:
    """Create the initial model-authored LayeredShaderSpec candidate."""
    context = runtime.context
    config = context.config
    ledger = replace(state["direct_ledger"])
    sequence = state["next_sequence"]
    layer_plan = state["layer_plan"]
    assert layer_plan is not None
    started = context.clock()
    initial = await run_initial_layered_glsl_author(
        gateway=context.gateway,
        reference_image=state["reference_image"],
        content_type=state["content_type"],
        user_instruction=state["instruction"],
        layer_plan=layer_plan,
        canvas_width=state["canvas_width"],
        canvas_height=state["canvas_height"],
        remaining_calls=config.direct_author_llm_budget - ledger.llm_call_count,
    )
    ledger.wall_clock_ms += (context.clock() - started) * 1000.0
    ledger.llm_call_count += initial.call_count
    ledger.total_tokens = accumulate_token_usage(
        ledger.total_tokens,
        initial.total_tokens,
        call_count=initial.call_count,
    )
    ledger.repair_count += 1 if initial.repaired else 0
    events = state["events"]
    failure_code: str | None = None
    if initial.layered_spec is None:
        ledger.rejected_candidates += 1
        failure_code = normalize_author_failure(initial.error_code)
        events = [
            *events,
            {
                "sequence": sequence,
                "kind": "initial",
                "ok": False,
                "error_code": failure_code,
                "detail": initial.error_code,
                "repaired": initial.repaired,
                "call_count": initial.call_count,
            },
        ]
    return {
        "candidate_role": "initial",
        "candidate_sequence": sequence,
        "candidate_layered_spec": initial.layered_spec,
        "candidate_compiled_spec": None,
        "candidate_attested_spec": None,
        "candidate_parent_sha256": None,
        "candidate_patched_layer_id": None,
        "candidate_optimization_focus": _optimization_focus_from_payload(
            initial.optimization_focus_payload
        ),
        "pending_candidate": None,
        "prepared_cache_key": None,
        "candidate_cache_hit": False,
        "draw_result": None,
        "verified_receipt": None,
        "direct_ledger": ledger,
        "events": events,
        "next_sequence": sequence + 1,
        "failure_code": failure_code,
        "completed_nodes": trace(state, "author_initial"),
    }


def route_after_authored_candidate(state: LayerPlanGlslDirectState) -> NodeRoute:
    """Route valid authored semantics into deterministic compilation."""
    if state.get("candidate_layered_spec") is not None:
        return "compile_candidate"
    if state.get("candidate_role") == "refine":
        return "decide_refinement"
    return "release_resources"


async def author_refinement(
    state: LayerPlanGlslDirectState,
    runtime: Runtime[DirectGraphContext],
) -> dict[str, Any]:
    """Create one guarded LayerPatch for the incumbent."""
    context = runtime.context
    config = context.config
    ledger = replace(state["direct_ledger"])
    current_best = state["current_best"]
    layer_plan = state["layer_plan"]
    assert current_best is not None and layer_plan is not None
    uniform_summary = _uniform_summary_for_refine(
        current_best,
        state.get("uniform_optimization_summary"),
    )
    role_alpha_masks, ledger, events = await _render_role_alpha_masks(
        state,
        context,
        current_best,
        ledger,
    )
    sequence = state["next_sequence"]
    started = context.clock()
    refine = await run_refine_layered_glsl_author(
        gateway=context.gateway,
        reference_image=state["reference_image"],
        current_render=current_best.png_bytes,
        content_type=state["content_type"],
        user_instruction=state["instruction"],
        incumbent=ValidatedLayeredIncumbent(
            layered_spec=current_best.layered_spec,
            compiled_program_spec=current_best.spec,
            mae=current_best.mae,
            loss=current_best.loss,
            metrics=dict(current_best.metrics),
            residual_summary=dict(current_best.residual_summary),
            optimization_focus=current_best.optimization_focus,
            focused_region_metrics=current_best.focused_region_metrics,
            role_alpha_masks=role_alpha_masks,
        ),
        layer_plan=layer_plan,
        refinement_index=state["refinement_count"] + 1,
        remaining_refine_budget=(config.refine_budget - state["refinement_count"]),
        previous_refine_feedback=state.get("previous_refine_feedback"),
        uniform_optimization_summary=uniform_summary,
        remaining_calls=config.direct_author_llm_budget - ledger.llm_call_count,
    )
    ledger.wall_clock_ms += (context.clock() - started) * 1000.0
    ledger.llm_call_count += refine.call_count
    ledger.total_tokens = accumulate_token_usage(
        ledger.total_tokens,
        refine.total_tokens,
        call_count=refine.call_count,
    )
    ledger.repair_count += 1 if refine.repaired else 0
    if refine.patch is None or refine.author_identity is None:
        ledger.rejected_candidates += 1
        events = [
            *events,
            {
                "sequence": sequence,
                "kind": "refine",
                "ok": False,
                "error_code": normalize_author_failure(refine.error_code),
                "detail": refine.error_code,
                "repaired": refine.repaired,
                "call_count": refine.call_count,
            },
        ]
    update: dict[str, Any] = {
        "candidate_role": "refine",
        "candidate_sequence": sequence,
        "candidate_layered_spec": None,
        "candidate_compiled_spec": None,
        "candidate_attested_spec": None,
        "candidate_parent_sha256": current_best.layered_spec.layered_spec_sha256,
        "candidate_patched_layer_id": (
            refine.patch.target_layer_id if refine.patch is not None else None
        ),
        "candidate_optimization_focus": _optimization_focus_from_payload(
            refine.optimization_focus_payload
        ),
        "prepared_cache_key": None,
        "candidate_cache_hit": False,
        "draw_result": None,
        "verified_receipt": None,
        "pending_candidate": None,
        "refine_patch": refine.patch,
        "refine_author_identity": refine.author_identity,
        "direct_ledger": ledger,
        "events": events,
        "next_sequence": sequence + 1,
        "refinement_count": state["refinement_count"] + 1,
        "refinement_blocked": (
            refine.patch is None
            and normalize_author_failure(refine.error_code)
            in TERMINAL_REFINEMENT_FAILURE_CODES
        ),
        "completed_nodes": trace(state, "author_refinement"),
    }
    if refine.patch is None or refine.author_identity is None:
        update.update(
            refine_failure_update(
                state,
                outcome="author_failed",
                failure_codes=(normalize_author_failure(refine.error_code),),
                target_layer_id=(
                    refine.patch.target_layer_id if refine.patch is not None else None
                ),
                inherit_candidate_target=False,
                force=True,
            )
        )
    return update


def route_after_refinement_author(state: LayerPlanGlslDirectState) -> NodeRoute:
    """Apply only a complete patch plus trusted author identity."""
    if state.get("refine_patch") is None or state.get("refine_author_identity") is None:
        return "decide_refinement"
    return "apply_refinement"


def apply_refinement(
    state: LayerPlanGlslDirectState,
    runtime: Runtime[DirectGraphContext],
) -> dict[str, Any]:
    """Apply the guarded LayerPatch to the current incumbent."""
    del runtime
    current_best = state["current_best"]
    patch = state["refine_patch"]
    author_identity = state["refine_author_identity"]
    assert current_best is not None
    assert patch is not None and author_identity is not None
    fingerprint = sha256(
        canonical_json(
            {
                "base_layered_spec_sha256": patch.base_layered_spec_sha256,
                "target_layer_id": patch.target_layer_id,
                "replacement": patch.replacement.semantic_dict(),
            }
        ).encode("utf-8")
    ).hexdigest()
    previous_layer = next(
        (
            layer
            for layer in current_best.layered_spec.layers
            if layer.layer_id == patch.target_layer_id
        ),
        None,
    )
    duplicate = (
        fingerprint in state["attempted_patch_fingerprints"]
        if state["optimization_policy"].detect_duplicate_patch
        else False
    )
    no_op = (
        previous_layer is not None
        and previous_layer.semantic_dict() == patch.replacement.semantic_dict()
    )
    attempted = (*state["attempted_patch_fingerprints"], fingerprint)
    if duplicate or no_op:
        ledger = replace(state["direct_ledger"])
        ledger.rejected_candidates += 1
        error_code = "duplicate_patch" if duplicate else "no_op_patch"
        events = [
            *state["events"],
            {
                "sequence": state["candidate_sequence"],
                "kind": "refine",
                "ok": False,
                "error_code": error_code,
            },
        ]
        return {
            "candidate_layered_spec": None,
            "attempted_patch_fingerprints": attempted,
            "duplicate_patch_detected": True,
            "duplicate_patch_count": state["duplicate_patch_count"] + 1,
            "direct_ledger": ledger,
            "events": events,
            "previous_refine_feedback": refine_failure_update(
                state,
                outcome="patch_invalid",
                failure_codes=(error_code,),
                target_layer_id=patch.target_layer_id,
            )["previous_refine_feedback"],
            "completed_nodes": trace(state, "apply_refinement"),
        }
    try:
        refined = apply_layer_patch(
            current_best.layered_spec,
            patch,
            author_identity,
        )
    except LayeredSpecError as exc:
        ledger = replace(state["direct_ledger"])
        ledger.rejected_candidates += 1
        events = [
            *state["events"],
            {
                "sequence": state["candidate_sequence"],
                "kind": "refine",
                "ok": False,
                "error_code": "author_output_invalid",
                "detail": exc.code,
            },
        ]
        return {
            "candidate_layered_spec": None,
            "attempted_patch_fingerprints": attempted,
            "direct_ledger": ledger,
            "events": events,
            **refine_failure_update(
                state,
                outcome="patch_invalid",
                failure_codes=(exc.code,),
                target_layer_id=patch.target_layer_id,
            ),
            "completed_nodes": trace(state, "apply_refinement"),
        }
    return {
        "candidate_layered_spec": refined,
        "attempted_patch_fingerprints": attempted,
        "completed_nodes": trace(state, "apply_refinement"),
    }


__all__ = [
    "apply_refinement",
    "author_initial",
    "author_layer_plan",
    "author_refinement",
    "prepare_reference",
    "route_after_authored_candidate",
    "route_after_layer_plan",
    "route_after_refinement_author",
]
