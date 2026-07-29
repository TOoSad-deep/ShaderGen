"""Compilation, rendering, verification, and selection nodes."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import Any

import numpy as np
from langgraph.runtime import Runtime

from agent.app.contracts.layerplan_glsl_direct import (
    DIRECT_HIGH_LEVEL_CANDIDATE_PROVENANCE,
    DIRECT_UNIFORM_CANDIDATE_PROVENANCE,
    MAX_REFINE_STATIC_VIOLATIONS,
    REFINE_FEEDBACK_METRICS,
    RENDERER_DEFERRED_SAFETY_CODES,
    DirectCandidate,
    RefineFeedback,
    RefineStaticViolation,
    candidate_excess_dominates,
    program_cache_key,
    safe_compile_diagnostics,
)
from agent.app.nodes.layered_direct.workflow_support import (
    candidate_failure_route,
    refine_failure_update,
    reject_candidate,
    render_failure_route,
    trace,
)
from agent.app.states.layerplan_glsl_direct import (
    DirectGraphContext,
    LayerPlanGlslDirectState,
    NodeRoute,
)
from shaderforge.evaluation import (
    dominant_metric_component,
    evaluate_min_scene,
    summarize_spatial_residual,
)
from shaderforge.layered_spec import LayeredSpecError, compile_layered_shader
from shaderforge.program_spec import (
    AttestationError,
    ShaderProgramSpecV1,
    is_executable,
    issue_attestation,
)
from shaderforge.rendering import RendererUnavailableError, ShaderPreparationError
from shaderforge.validation import validate_program_spec_safety


def compile_candidate(
    state: LayerPlanGlslDirectState,
    runtime: Runtime[DirectGraphContext],
) -> dict[str, Any]:
    """Compile LayeredShaderSpec semantics into ShaderProgramSpec."""
    del runtime
    layered_spec = state["candidate_layered_spec"]
    assert layered_spec is not None
    try:
        compiled = compile_layered_shader(layered_spec)
    except LayeredSpecError as exc:
        error_code = (
            "static_validation_failed"
            if state["candidate_role"] == "initial"
            else "author_output_invalid"
        )
        ledger, events = reject_candidate(state, error_code, detail=exc.code)
        return {
            "candidate_compiled_spec": None,
            "direct_ledger": ledger,
            "events": events,
            "failure_code": (
                error_code
                if state["candidate_role"] == "initial"
                else state.get("failure_code")
            ),
            **refine_failure_update(
                state,
                outcome="static_failed",
                failure_codes=(exc.code,),
            ),
            "completed_nodes": trace(state, "compile_candidate"),
        }
    return {
        "candidate_compiled_spec": compiled,
        "completed_nodes": trace(state, "compile_candidate"),
    }


def route_after_compile(state: LayerPlanGlslDirectState) -> NodeRoute:
    """Skip candidate execution when deterministic compilation failed."""
    return candidate_failure_route(state, "validate_candidate")


def validate_candidate(
    state: LayerPlanGlslDirectState,
    runtime: Runtime[DirectGraphContext],
) -> dict[str, Any]:
    """Apply canonical static safety validation."""
    del runtime
    compiled_spec = state["candidate_compiled_spec"]
    assert compiled_spec is not None
    static_result = validate_program_spec_safety(compiled_spec)
    blocking = tuple(
        item
        for item in static_result.violations
        if item.code not in RENDERER_DEFERRED_SAFETY_CODES
    )
    if any(item.severity == "error" for item in blocking):
        errors = tuple(item for item in blocking if item.severity == "error")
        ledger, events = reject_candidate(
            state,
            "static_validation_failed",
            violations=[item.code for item in blocking],
        )
        return {
            "candidate_compiled_spec": None,
            "direct_ledger": ledger,
            "events": events,
            "failure_code": (
                "static_validation_failed"
                if state["candidate_role"] == "initial"
                else state.get("failure_code")
            ),
            **refine_failure_update(
                state,
                outcome="static_failed",
                failure_codes=tuple(item.code for item in errors),
                static_violations=tuple(
                    RefineStaticViolation(code=item.code, line=item.line)
                    for item in errors[:MAX_REFINE_STATIC_VIOLATIONS]
                ),
            ),
            "completed_nodes": trace(state, "validate_candidate"),
        }
    return {
        "completed_nodes": trace(state, "validate_candidate"),
    }


def route_after_validation(state: LayerPlanGlslDirectState) -> NodeRoute:
    """Route only statically accepted candidates to WebGL preparation."""
    return candidate_failure_route(state, "prepare_program")


async def prepare_program(
    state: LayerPlanGlslDirectState,
    runtime: Runtime[DirectGraphContext],
) -> dict[str, Any]:
    """Compile/link the WebGL program or reuse an attempt-local cache entry."""
    context = runtime.context
    config = context.config
    ledger = replace(state["direct_ledger"])
    compiled_spec = state["candidate_compiled_spec"]
    assert compiled_spec is not None
    cache_key = program_cache_key(compiled_spec)
    prepared = context.program_cache.get(cache_key)
    if prepared is not None:
        ledger.cache_hits += 1
        return {
            "prepared_cache_key": cache_key,
            "candidate_cache_hit": True,
            "direct_ledger": ledger,
            "completed_nodes": trace(state, "prepare_program"),
        }
    if ledger.compile_count >= config.compile_budget:
        ledger, events = reject_candidate(state, "compile_budget_exhausted")
        return {
            "prepared_cache_key": None,
            "candidate_cache_hit": False,
            "direct_ledger": ledger,
            "events": events,
            "failure_code": (
                "compile_budget_exhausted"
                if state["candidate_role"] == "initial"
                else state.get("failure_code")
            ),
            "refinement_blocked": state["candidate_role"] == "refine",
            **refine_failure_update(
                state,
                outcome="compile_failed",
                failure_codes=("compile_budget_exhausted",),
            ),
            "completed_nodes": trace(state, "prepare_program"),
        }
    ledger.compile_count += 1
    uniform_schema = {item.name: item.type for item in compiled_spec.uniform_schema}
    started = context.clock()
    try:
        prepared = await context.renderer.prepare(
            compiled_spec.fragment_source,
            compiled_spec.canvas.width,
            compiled_spec.canvas.height,
            uniform_schema,
        )
    except ShaderPreparationError as exc:
        ledger.wall_clock_ms += (context.clock() - started) * 1000.0
        ledger, events = reject_candidate(
            state,
            "compile_or_link_failed",
            ledger=ledger,
            diagnostics=safe_compile_diagnostics(exc.compile_result),
        )
        static_errors = tuple(
            item
            for item in exc.compile_result.static_validation.violations
            if item.severity == "error"
        )
        return {
            "prepared_cache_key": None,
            "candidate_cache_hit": False,
            "direct_ledger": ledger,
            "events": events,
            "failure_code": (
                "compile_or_link_failed"
                if state["candidate_role"] == "initial"
                else state.get("failure_code")
            ),
            **refine_failure_update(
                state,
                outcome="compile_failed",
                failure_codes=(
                    "compile_or_link_failed",
                    *tuple(item.code for item in static_errors),
                ),
                static_violations=tuple(
                    RefineStaticViolation(code=item.code, line=item.line)
                    for item in static_errors[:MAX_REFINE_STATIC_VIOLATIONS]
                ),
            ),
            "completed_nodes": trace(state, "prepare_program"),
        }
    except (RendererUnavailableError, ValueError, OSError) as exc:
        ledger.wall_clock_ms += (context.clock() - started) * 1000.0
        ledger, events = reject_candidate(
            state,
            "renderer_unavailable",
            ledger=ledger,
            detail=type(exc).__name__,
        )
        return {
            "prepared_cache_key": None,
            "candidate_cache_hit": False,
            "direct_ledger": ledger,
            "events": events,
            "failure_code": (
                "renderer_unavailable"
                if state["candidate_role"] == "initial"
                else state.get("failure_code")
            ),
            "refinement_blocked": state["candidate_role"] == "refine",
            **refine_failure_update(
                state,
                outcome="compile_failed",
                failure_codes=("renderer_unavailable",),
            ),
            "completed_nodes": trace(state, "prepare_program"),
        }
    ledger.wall_clock_ms += (context.clock() - started) * 1000.0
    context.program_cache[cache_key] = prepared
    return {
        "prepared_cache_key": cache_key,
        "candidate_cache_hit": False,
        "direct_ledger": ledger,
        "completed_nodes": trace(state, "prepare_program"),
    }


def route_after_prepare(state: LayerPlanGlslDirectState) -> NodeRoute:
    """Route successfully prepared programs to drawing."""
    return render_failure_route(state, "render_program", "prepared_cache_key")


async def render_program(
    state: LayerPlanGlslDirectState,
    runtime: Runtime[DirectGraphContext],
) -> dict[str, Any]:
    """Draw the candidate and capture RGB/PNG output plus its receipt."""
    context = runtime.context
    config = context.config
    ledger = replace(state["direct_ledger"])
    cache_key = state["prepared_cache_key"]
    compiled_spec = state["candidate_compiled_spec"]
    assert cache_key is not None and compiled_spec is not None
    prepared = context.program_cache[cache_key]
    if ledger.draw_count >= config.draw_budget:
        ledger, events = reject_candidate(state, "draw_budget_exhausted")
        return {
            "draw_result": None,
            "direct_ledger": ledger,
            "events": events,
            "failure_code": (
                "draw_budget_exhausted"
                if state["candidate_role"] == "initial"
                else state.get("failure_code")
            ),
            "refinement_blocked": state["candidate_role"] == "refine",
            **refine_failure_update(
                state,
                outcome="draw_failed",
                failure_codes=("draw_budget_exhausted",),
            ),
            "completed_nodes": trace(state, "render_program"),
        }
    ledger.draw_count += 1
    if state["candidate_role"] == "uniform_optimize":
        ledger.uniform_tuning_draw_count += 1
    started = context.clock()
    try:
        draw = await prepared.render_uniforms(
            dict(compiled_spec.uniform_values),
            capture_png=True,
            receipt_spec_sha256=compiled_spec.spec_sha256,
        )
    except (RendererUnavailableError, ValueError, OSError) as exc:
        ledger.wall_clock_ms += (context.clock() - started) * 1000.0
        ledger, events = reject_candidate(
            state,
            "renderer_unavailable",
            ledger=ledger,
            detail=type(exc).__name__,
        )
        return {
            "draw_result": None,
            "direct_ledger": ledger,
            "events": events,
            "failure_code": (
                "renderer_unavailable"
                if state["candidate_role"] == "initial"
                else state.get("failure_code")
            ),
            "refinement_blocked": state["candidate_role"] == "refine",
            **refine_failure_update(
                state,
                outcome="draw_failed",
                failure_codes=("renderer_unavailable",),
            ),
            "completed_nodes": trace(state, "render_program"),
        }
    ledger.wall_clock_ms += (context.clock() - started) * 1000.0
    if not draw.success or draw.rgb_bytes is None or draw.image_bytes is None:
        ledger, events = reject_candidate(
            state,
            "draw_failed",
            ledger=ledger,
            draw_error=draw.draw_error,
        )
        return {
            "draw_result": None,
            "direct_ledger": ledger,
            "events": events,
            "failure_code": (
                "draw_failed"
                if state["candidate_role"] == "initial"
                else state.get("failure_code")
            ),
            **refine_failure_update(
                state,
                outcome="draw_failed",
                failure_codes=("draw_failed",),
            ),
            "completed_nodes": trace(state, "render_program"),
        }
    return {
        "draw_result": draw,
        "direct_ledger": ledger,
        "completed_nodes": trace(state, "render_program"),
    }


def route_after_render(state: LayerPlanGlslDirectState) -> NodeRoute:
    """Route successful draws to receipt verification."""
    return render_failure_route(state, "verify_receipt", "draw_result")


def verify_receipt(
    state: LayerPlanGlslDirectState,
    runtime: Runtime[DirectGraphContext],
) -> dict[str, Any]:
    """Verify the draw receipt and its bound pixel hashes."""
    del runtime
    draw = state["draw_result"]
    assert draw is not None
    assert draw.rgb_bytes is not None and draw.image_bytes is not None
    receipt = draw.execution_receipt
    detail: str | None = None
    if receipt is None:
        detail = "receipt_missing"
    else:
        required_runtime = ("browser_version", "gl_version", "glsl_version")
        if (
            sha256(draw.rgb_bytes).hexdigest() != receipt.rgb_sha256
            or receipt.png_sha256 is None
            or sha256(draw.image_bytes).hexdigest() != receipt.png_sha256
            or any(not receipt.runtime_metadata.get(key) for key in required_runtime)
        ):
            detail = "receipt_pixel_mismatch"
    if detail is not None or receipt is None:
        ledger, events = reject_candidate(
            state,
            "static_validation_failed",
            detail=detail,
        )
        return {
            "verified_receipt": None,
            "direct_ledger": ledger,
            "events": events,
            "failure_code": (
                "static_validation_failed"
                if state["candidate_role"] == "initial"
                else state.get("failure_code")
            ),
            **refine_failure_update(
                state,
                outcome="receipt_failed",
                failure_codes=(detail or "receipt_missing",),
            ),
            "completed_nodes": trace(state, "verify_receipt"),
        }
    return {
        "verified_receipt": receipt,
        "completed_nodes": trace(state, "verify_receipt"),
    }


def route_after_receipt(state: LayerPlanGlslDirectState) -> NodeRoute:
    """Route verified receipts to trusted attestation."""
    return render_failure_route(state, "attest_candidate", "verified_receipt")


def attest_candidate(
    state: LayerPlanGlslDirectState,
    runtime: Runtime[DirectGraphContext],
) -> dict[str, Any]:
    """Issue and verify the executable validation attestation."""
    receipt = state["verified_receipt"]
    compiled_spec = state["candidate_compiled_spec"]
    assert receipt is not None and compiled_spec is not None
    detail: str | None = None
    attested: ShaderProgramSpecV1 | None = None
    try:
        attested = compiled_spec.with_attestation(
            issue_attestation(
                compiled_spec,
                receipt=receipt,
                static_ok=True,
                issuer=runtime.context.receipt_issuer,
            )
        )
    except AttestationError as exc:
        detail = exc.code
    if attested is not None and not is_executable(
        attested,
        issuer=runtime.context.receipt_issuer,
    ):
        attested = None
        detail = "attestation_mismatch"
    if attested is None:
        ledger, events = reject_candidate(
            state,
            "static_validation_failed",
            detail=detail,
        )
        return {
            "candidate_attested_spec": None,
            "direct_ledger": ledger,
            "events": events,
            "failure_code": (
                "static_validation_failed"
                if state["candidate_role"] == "initial"
                else state.get("failure_code")
            ),
            **refine_failure_update(
                state,
                outcome="attestation_failed",
                failure_codes=(detail or "attestation_mismatch",),
            ),
            "completed_nodes": trace(state, "attest_candidate"),
        }
    return {
        "candidate_attested_spec": attested,
        "completed_nodes": trace(state, "attest_candidate"),
    }


def route_after_attestation(state: LayerPlanGlslDirectState) -> NodeRoute:
    """Route attested candidates to deterministic metric evaluation."""
    return render_failure_route(
        state,
        "evaluate_candidate",
        "candidate_attested_spec",
    )


def evaluate_candidate(
    state: LayerPlanGlslDirectState,
    runtime: Runtime[DirectGraphContext],
) -> dict[str, Any]:
    """Evaluate the verified render and assemble an immutable candidate."""
    del runtime
    draw = state["draw_result"]
    attested = state["candidate_attested_spec"]
    layered_spec = state["candidate_layered_spec"]
    assert draw is not None and attested is not None and layered_spec is not None
    assert draw.rgb_bytes is not None and draw.image_bytes is not None
    rendered = (
        np.frombuffer(draw.rgb_bytes, dtype=np.uint8)
        .reshape(attested.canvas.height, attested.canvas.width, 3)
        .astype(np.float32)
        / 255.0
    )
    metric = evaluate_min_scene(
        state["target_rgb"],
        rendered,
        state["background"],
    )
    validation_attestation = attested.validation_attestation
    assert validation_attestation is not None
    residual = summarize_spatial_residual(state["target_rgb"], rendered)
    residual["dominant_metric_component"] = dominant_metric_component(metric)
    events = [
        *state["events"],
        {
            "sequence": state["candidate_sequence"],
            "kind": state["candidate_role"],
            "ok": True,
            "layered_spec_sha256": layered_spec.layered_spec_sha256,
            "spec_sha256": attested.spec_sha256,
            "patched_layer_id": state.get("candidate_patched_layer_id"),
            "loss": metric.total_loss,
            "mae": metric.global_mae,
            "validator_version": validation_attestation.validator_version,
            "cache_hit": state["candidate_cache_hit"],
        },
    ]
    candidate = DirectCandidate(
        layered_spec=layered_spec,
        spec=attested,
        role=state["candidate_role"],
        sequence=state["candidate_sequence"],
        rgb_bytes=draw.rgb_bytes,
        png_bytes=draw.image_bytes,
        mae=metric.global_mae,
        loss=metric.total_loss,
        metrics=metric.to_dict(),
        residual_summary=residual,
        parent_layered_spec_sha256=state.get("candidate_parent_sha256"),
        patched_layer_id=state.get("candidate_patched_layer_id"),
        provenance=(
            DIRECT_UNIFORM_CANDIDATE_PROVENANCE
            if state["candidate_role"] == "uniform_optimize"
            else DIRECT_HIGH_LEVEL_CANDIDATE_PROVENANCE
        ),
    )
    return {
        "pending_candidate": candidate,
        "events": events,
        "completed_nodes": trace(state, "evaluate_candidate"),
    }


def select_candidate(
    state: LayerPlanGlslDirectState,
    runtime: Runtime[DirectGraphContext],
) -> dict[str, Any]:
    """Apply the target-relative dual-objective ``current_best`` boundary."""
    del runtime
    candidate = state["pending_candidate"]
    assert candidate is not None
    ledger = replace(state["direct_ledger"])
    candidates = [*state["candidates"], candidate]
    current_best = state.get("current_best")
    policy = state["optimization_policy"]
    candidate_selected = current_best is None or candidate_excess_dominates(
        candidate_mae=candidate.mae,
        candidate_loss=candidate.loss,
        incumbent_mae=current_best.mae,
        incumbent_loss=current_best.loss,
        target_mae=policy.target_mae,
        target_loss=policy.target_loss,
    )
    loss_delta: float | None = None
    mae_delta: float | None = None
    material_improvement = False
    consecutive_non_improving = state["consecutive_non_improving"]
    feedback = state.get("previous_refine_feedback")
    if state["candidate_role"] != "initial" and current_best is not None:
        loss_delta = current_best.loss - candidate.loss
        mae_delta = current_best.mae - candidate.mae
        material_improvement = candidate_selected and (
            loss_delta >= policy.min_delta_loss
            or mae_delta >= policy.min_delta_mae
        )
        if state["candidate_role"] == "refine":
            if material_improvement:
                consecutive_non_improving = 0
                feedback = None
            else:
                consecutive_non_improving += 1
                metric_deltas = {
                    name: float(current_best.metrics[name])
                    - float(candidate.metrics[name])
                    for name in REFINE_FEEDBACK_METRICS
                    if isinstance(current_best.metrics.get(name), (int, float))
                    and not isinstance(current_best.metrics.get(name), bool)
                    and isinstance(candidate.metrics.get(name), (int, float))
                    and not isinstance(candidate.metrics.get(name), bool)
                }
                feedback = RefineFeedback(
                    outcome=(
                        "minor_improvement" if candidate_selected else "not_improved"
                    ),
                    target_layer_id=candidate.patched_layer_id,
                    candidate_loss=candidate.loss,
                    candidate_mae=candidate.mae,
                    loss_delta=loss_delta,
                    mae_delta=mae_delta,
                    metric_deltas=metric_deltas,
                )
    if candidate_selected:
        current_best = candidate
        ledger.accepted_candidates += 1
    else:
        ledger.rejected_candidates += 1
    return {
        "candidates": candidates,
        "current_best": current_best,
        "direct_ledger": ledger,
        "candidate_selected": candidate_selected,
        "candidate_loss_delta": loss_delta,
        "candidate_mae_delta": mae_delta,
        "candidate_material_improvement": material_improvement,
        "consecutive_non_improving": consecutive_non_improving,
        "previous_refine_feedback": feedback,
        "failure_code": None,
        "completed_nodes": trace(state, "select_candidate"),
    }


__all__ = [
    "attest_candidate",
    "compile_candidate",
    "evaluate_candidate",
    "prepare_program",
    "render_program",
    "route_after_compile",
    "route_after_prepare",
    "route_after_render",
    "route_after_validation",
    "route_after_attestation",
    "route_after_receipt",
    "select_candidate",
    "validate_candidate",
    "verify_receipt",
]
