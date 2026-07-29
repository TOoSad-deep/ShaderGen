"""Focused safety and state-semantics tests for structural Refine feedback."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

from agent.app.contracts.layerplan_glsl_direct import (
    AttemptLedger,
    RefineFeedback,
    RefineStaticViolation,
)
from agent.app.nodes.layered_direct import candidate_nodes
from agent.app.nodes.layered_direct.candidate_nodes import validate_candidate
from agent.app.nodes.layered_direct.workflow_author_nodes import (
    _uniform_summary_for_refine,
)
from agent.app.nodes.layered_direct.workflow_support import refine_failure_update
from agent.app.states.layerplan_glsl_direct import LayerPlanGlslDirectState
from shaderforge.uniform_optimization import UniformOptimizationSummaryV2
from shaderforge.validation import ValidationResult, ValidationViolation


def test_author_failure_can_explicitly_clear_a_stale_candidate_target() -> None:
    state = cast(
        LayerPlanGlslDirectState,
        {
            "candidate_role": "refine",
            "candidate_patched_layer_id": "old_subject",
            "consecutive_non_improving": 1,
        },
    )

    update = refine_failure_update(
        state,
        outcome="author_failed",
        failure_codes=("author_output_invalid",),
        inherit_candidate_target=False,
        force=True,
    )

    feedback = update["previous_refine_feedback"]
    assert feedback.target_layer_id is None
    assert update["consecutive_non_improving"] == 2


def test_refine_feedback_serializes_only_stable_static_code_and_line() -> None:
    feedback = RefineFeedback(
        outcome="static_failed",
        target_layer_id="subject",
        failure_codes=("forbidden_loop",),
        static_violations=(RefineStaticViolation(code="forbidden_loop", line=7),),
    )

    assert feedback.to_dict()["static_violations"] == [
        {"code": "forbidden_loop", "line": 7}
    ]
    with pytest.raises(ValueError, match="positive integer"):
        RefineStaticViolation(code="forbidden_loop", line=0)


def test_validation_failure_forwards_stable_location_without_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = ValidationResult(
        valid=False,
        violations=(
            ValidationViolation(
                code="forbidden_loop",
                message="provider-private source detail",
                severity="error",
                line=11,
            ),
        ),
        source_chars=42,
        contract_id="test_contract",
    )
    monkeypatch.setattr(
        candidate_nodes,
        "validate_program_spec_safety",
        lambda _spec: result,
    )
    state = cast(
        LayerPlanGlslDirectState,
        {
            "candidate_compiled_spec": SimpleNamespace(spec_sha256="a" * 64),
            "candidate_layered_spec": None,
            "candidate_role": "refine",
            "candidate_sequence": 2,
            "candidate_patched_layer_id": "subject",
            "direct_ledger": AttemptLedger(),
            "events": [],
            "failure_code": None,
            "consecutive_non_improving": 0,
        },
    )

    update = validate_candidate(state, cast(Any, None))

    feedback = update["previous_refine_feedback"]
    payload = feedback.to_dict()
    assert payload["static_violations"] == [{"code": "forbidden_loop", "line": 11}]
    assert "provider-private source detail" not in json.dumps(payload)


def test_refine_drops_a_uniform_summary_from_a_previous_incumbent() -> None:
    current_best = SimpleNamespace(spec=SimpleNamespace(spec_sha256="b" * 64))
    stale = UniformOptimizationSummaryV2(
        base_spec_sha256="a" * 64,
        selected_spec_sha256="c" * 64,
        config_fingerprint="d" * 64,
        active_component_count=1,
        evaluated_count=1,
        accepted_count=0,
        draw_count=1,
        draw_budget=1,
        initial_loss=0.4,
        initial_mae=0.3,
        final_loss=0.4,
        final_mae=0.3,
        loss_delta=0.0,
        mae_delta=0.0,
        stop_reason="local_optimum",
    )

    assert (
        _uniform_summary_for_refine(
            cast(Any, current_best),
            stale,
        )
        is None
    )
