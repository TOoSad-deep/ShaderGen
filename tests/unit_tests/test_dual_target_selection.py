"""Regression tests for target-relative dual-objective candidate selection."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from agent.app.contracts.layerplan_glsl_direct import (
    DIRECT_OPTIMIZATION_POLICY_SCHEMA_VERSION,
    AttemptLedger,
    DirectOptimizationPolicy,
    candidate_excess_dominates,
)
from agent.app.nodes.layered_direct.candidate_nodes import select_candidate
from agent.app.states.layerplan_glsl_direct import LayerPlanGlslDirectState


@pytest.mark.parametrize(
    (
        "candidate_mae",
        "candidate_loss",
        "incumbent_mae",
        "incumbent_loss",
        "expected",
    ),
    [
        (0.09, 0.10, 0.08, 0.12, False),
        (0.08, 0.13, 0.10, 0.12, False),
        (0.07, 0.079, 0.08, 0.070, True),
        (0.039, 0.09, 0.030, 0.11, True),
        (0.08, 0.10, 0.09, 0.12, True),
        (0.05, 0.07, 0.04, 0.06, False),
    ],
)
def test_candidate_excess_dominance(
    candidate_mae: float,
    candidate_loss: float,
    incumbent_mae: float,
    incumbent_loss: float,
    expected: bool,
) -> None:
    assert (
        candidate_excess_dominates(
            candidate_mae=candidate_mae,
            candidate_loss=candidate_loss,
            incumbent_mae=incumbent_mae,
            incumbent_loss=incumbent_loss,
            target_mae=0.06,
            target_loss=0.08,
        )
        is expected
    )


def _candidate(
    *,
    mae: float,
    loss: float,
    role: str = "refine",
) -> SimpleNamespace:
    return SimpleNamespace(
        mae=mae,
        loss=loss,
        metrics={
            "global_mae": mae,
            "foreground_mae": mae,
            "background_mae": mae,
            "geometry_mask_loss": loss,
            "edge_loss": loss,
            "worst_tile_mae": mae,
        },
        patched_layer_id="subject",
        role=role,
    )


def _selection_state(
    *,
    incumbent: SimpleNamespace | None,
    candidate: SimpleNamespace,
    role: str = "refine",
    policy: DirectOptimizationPolicy | None = None,
    consecutive_non_improving: int = 1,
) -> LayerPlanGlslDirectState:
    return cast(
        LayerPlanGlslDirectState,
        {
            "pending_candidate": candidate,
            "candidate_role": role,
            "direct_ledger": AttemptLedger(),
            "candidates": [] if incumbent is None else [incumbent],
            "current_best": incumbent,
            "optimization_policy": policy or DirectOptimizationPolicy(),
            "consecutive_non_improving": consecutive_non_improving,
            "previous_refine_feedback": None,
            "completed_nodes": (),
        },
    )


def test_initial_candidate_is_accepted_without_an_incumbent() -> None:
    candidate = _candidate(mae=0.5, loss=0.5, role="initial")

    update = select_candidate(
        _selection_state(incumbent=None, candidate=candidate, role="initial"),
        cast(Any, None),
    )

    assert update["candidate_selected"] is True
    assert update["current_best"] is candidate
    assert update["candidate_material_improvement"] is False
    assert update["direct_ledger"].accepted_candidates == 1


def test_lower_loss_with_worse_mae_excess_is_rejected_and_increments_patience() -> None:
    incumbent = _candidate(mae=0.08, loss=0.12)
    candidate = _candidate(mae=0.09, loss=0.10)

    update = select_candidate(
        _selection_state(incumbent=incumbent, candidate=candidate),
        cast(Any, None),
    )

    assert update["candidate_selected"] is False
    assert update["current_best"] is incumbent
    assert update["candidate_material_improvement"] is False
    assert update["consecutive_non_improving"] == 2
    assert update["previous_refine_feedback"].outcome == "not_improved"


def test_mae_only_material_improvement_resets_patience() -> None:
    policy = DirectOptimizationPolicy(min_delta_loss=0.01, min_delta_mae=0.005)
    incumbent = _candidate(mae=0.08, loss=0.07)
    candidate = _candidate(mae=0.07, loss=0.079)

    update = select_candidate(
        _selection_state(
            incumbent=incumbent,
            candidate=candidate,
            policy=policy,
            consecutive_non_improving=2,
        ),
        cast(Any, None),
    )

    assert update["candidate_selected"] is True
    assert update["candidate_loss_delta"] == pytest.approx(-0.009)
    assert update["candidate_mae_delta"] == pytest.approx(0.01)
    assert update["candidate_material_improvement"] is True
    assert update["consecutive_non_improving"] == 0
    assert update["previous_refine_feedback"] is None


def test_selected_minor_improvement_increments_patience_and_feedback() -> None:
    policy = DirectOptimizationPolicy(min_delta_loss=0.01, min_delta_mae=0.01)
    incumbent = _candidate(mae=0.08, loss=0.10)
    candidate = _candidate(mae=0.075, loss=0.095)

    update = select_candidate(
        _selection_state(
            incumbent=incumbent,
            candidate=candidate,
            policy=policy,
            consecutive_non_improving=0,
        ),
        cast(Any, None),
    )

    assert update["candidate_selected"] is True
    assert update["candidate_material_improvement"] is False
    assert update["consecutive_non_improving"] == 1
    assert update["previous_refine_feedback"].outcome == "minor_improvement"


def test_uniform_candidate_uses_same_selection_without_mutating_refine_patience() -> None:
    incumbent = _candidate(mae=0.08, loss=0.12)
    candidate = _candidate(mae=0.09, loss=0.10, role="uniform_optimize")

    update = select_candidate(
        _selection_state(
            incumbent=incumbent,
            candidate=candidate,
            role="uniform_optimize",
        ),
        cast(Any, None),
    )

    assert update["candidate_selected"] is False
    assert update["candidate_material_improvement"] is False
    assert update["consecutive_non_improving"] == 1


def test_policy_v2_projects_min_delta_mae_and_changes_fingerprint() -> None:
    policy = DirectOptimizationPolicy()
    changed = DirectOptimizationPolicy(min_delta_mae=0.002)

    assert policy.schema_version == DIRECT_OPTIMIZATION_POLICY_SCHEMA_VERSION
    assert policy.to_dict()["schema_version"] == "direct_optimization_policy_v2"
    assert policy.to_dict()["min_delta_mae"] == pytest.approx(0.001)
    assert policy.fingerprint() != changed.fingerprint()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_delta_mae": float("nan")},
        {"min_delta_mae": float("inf")},
        {"min_delta_mae": -0.001},
        {"min_delta_mae": 1.001},
        {"min_delta_mae": True},
        {"schema_version": "direct_optimization_policy_v1"},
    ],
)
def test_policy_v2_rejects_invalid_mae_controls(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        DirectOptimizationPolicy(**kwargs)  # type: ignore[arg-type]
