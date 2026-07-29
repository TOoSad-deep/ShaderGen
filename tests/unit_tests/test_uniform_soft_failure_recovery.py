"""Regression tests for bounded uniform candidate failure recovery."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

from agent.app.contracts.layerplan_glsl_direct import AttemptLedger
from agent.app.nodes.layered_direct.uniform_optimization_nodes import (
    decide_uniform_optimization,
    record_uniform_outcome,
)
from shaderforge.uniform_optimization import (
    FlatTunableComponent,
    UniformOptimizationConfig,
    next_coordinate_move,
    record_coordinate_failure,
    start_coordinate_pattern_session,
)


def _component(path: str) -> FlatTunableComponent:
    return FlatTunableComponent(
        layer_id="shape",
        path=path,
        component_index=0,
        minimum=Decimal("0"),
        maximum=Decimal("1"),
        step=Decimal("0.1"),
        base_value=Decimal("0.5"),
    )


def _session(*, component_count: int = 1, draw_budget: int = 4):
    return start_coordinate_pattern_session(
        base_program_spec_sha256="a" * 64,
        components=tuple(_component(f"u_value_{index}") for index in range(component_count)),
        config=UniformOptimizationConfig(
            draw_budget=draw_budget,
            active_component_cap=component_count,
        ),
    )


def _failure_state(*, error_code: str, ledger: AttemptLedger | None = None):
    session = _session()
    session, move = next_coordinate_move(session)
    assert move is not None
    current_ledger = ledger or AttemptLedger()
    patch = SimpleNamespace(
        base_layered_spec_sha256="b" * 64,
        base_program_spec_sha256="a" * 64,
        derivation=SimpleNamespace(component_identity_sha256="c" * 64),
    )
    return {
        "uniform_search_session": session,
        "uniform_pending_move": move,
        "current_best": SimpleNamespace(
            spec=SimpleNamespace(
                source_sha256="d" * 64,
                spec_sha256="e" * 64,
            ),
            mae=0.5,
            loss=0.5,
        ),
        "direct_ledger": current_ledger,
        "uniform_candidate_patch": patch,
        "pending_candidate": None,
        "events": [{"error_code": error_code}],
        "uniform_optimization_trace": [],
        "uniform_optimized_source_sha256s": (),
        "uniform_search_source_sha256": "d" * 64,
        "uniform_search_base_spec_sha256": "a" * 64,
        "uniform_search_selected_spec_sha256": "e" * 64,
        "uniform_search_initial_loss": 0.5,
        "uniform_search_initial_mae": 0.5,
        "uniform_search_selected_loss": 0.5,
        "uniform_search_selected_mae": 0.5,
        "uniform_search_initial_draw_count": (
            current_ledger.uniform_tuning_draw_count
        ),
        "uniform_search_trace_start_index": 0,
        "completed_nodes": (),
        "refinement_blocked": False,
    }


def test_failed_plus_probe_continues_with_paired_minus_without_evaluation() -> None:
    session = _session()
    session, plus = next_coordinate_move(session)
    assert plus is not None and plus.direction == 1 and plus.ordinal == 1

    session = record_coordinate_failure(session, plus)
    session, minus = next_coordinate_move(session)

    assert minus is not None
    assert minus.direction == -1
    assert minus.ordinal == 2
    assert session.failed_probe_count == 1
    assert session.evaluated_count == 0
    assert session.accepted_count == 0


def test_two_failed_directions_advance_to_next_component() -> None:
    session = _session(component_count=2, draw_budget=6)
    session, plus = next_coordinate_move(session)
    assert plus is not None
    first_path = plus.component.canonical_path
    session = record_coordinate_failure(session, plus)
    session, minus = next_coordinate_move(session)
    assert minus is not None and minus.component.canonical_path == first_path
    session = record_coordinate_failure(session, minus)

    session, next_plus = next_coordinate_move(session)

    assert next_plus is not None
    assert next_plus.direction == 1
    assert next_plus.component.canonical_path != first_path
    assert next_plus.ordinal == 3
    assert session.failed_probe_count == 2
    assert session.evaluated_count == 0


def test_failed_session_finishes_with_explicit_failure_reason() -> None:
    session = _session()
    session, plus = next_coordinate_move(session)
    assert plus is not None
    session = record_coordinate_failure(session, plus)
    session, minus = next_coordinate_move(session)
    assert minus is not None

    session = record_coordinate_failure(session, minus)
    session, move = next_coordinate_move(session)

    assert move is None
    assert session.stop_reason == "candidate_failures_exhausted"
    assert session.probe_count == 2
    assert session.evaluated_count == 0


def test_soft_graph_failure_advances_session_without_mutating_real_ledgers() -> None:
    ledger = AttemptLedger(
        draw_count=2,
        rejected_candidates=1,
        uniform_tuning_draw_count=1,
        uniform_tuning_evaluated_count=0,
        uniform_tuning_accepted_count=0,
    )
    state = _failure_state(error_code="static_validation_failed", ledger=ledger)

    update = record_uniform_outcome(
        cast(Any, state),
        cast(Any, None),
    )
    advanced = update["uniform_search_session"]
    advanced, next_move = next_coordinate_move(advanced)

    assert next_move is not None and next_move.direction == -1
    assert advanced.failed_probe_count == 1
    assert advanced.evaluated_count == 0
    assert "direct_ledger" not in update
    assert ledger.draw_count == 2
    assert ledger.uniform_tuning_draw_count == 1
    assert ledger.uniform_tuning_evaluated_count == 0
    assert ledger.uniform_tuning_accepted_count == 0
    assert update["uniform_optimization_trace"][-1]["failure_code"] == (
        "static_validation_failed"
    )
    assert "uniform_optimized_source_sha256s" not in update


def test_renderer_failure_remains_a_hard_terminal_stop() -> None:
    state = _failure_state(error_code="renderer_unavailable")

    update = record_uniform_outcome(
        cast(Any, state),
        cast(Any, None),
    )

    assert update["uniform_search_session"].stop_reason == "renderer_unavailable"
    assert update["uniform_tuning_stop_reason"] == "renderer_unavailable"
    assert update["refinement_blocked"] is True
    assert update["uniform_optimized_source_sha256s"] == ("d" * 64,)
    assert update["uniform_search_session"].failed_probe_count == 0
    assert update["uniform_search_session"].evaluated_count == 0


def test_global_budget_failure_remains_a_hard_terminal_stop() -> None:
    state = _failure_state(error_code="compile_budget_exhausted")

    update = record_uniform_outcome(
        cast(Any, state),
        cast(Any, None),
    )

    assert update["uniform_search_session"].stop_reason == (
        "global_compile_budget_exhausted"
    )
    assert update["uniform_tuning_stop_reason"] == "global_compile_budget_exhausted"
    assert update["refinement_blocked"] is True
    assert update["uniform_search_session"].failed_probe_count == 0

    decision = decide_uniform_optimization(
        cast(
            Any,
            {
                **state,
                **update,
                "optimization_policy": SimpleNamespace(
                    target_mae=0.1,
                    target_loss=0.1,
                ),
                "uniform_search_source_sha256": "d" * 64,
            },
        ),
        cast(
            Any,
            SimpleNamespace(
                context=SimpleNamespace(
                    config=SimpleNamespace(
                        draw_budget=10,
                        uniform_tuning_draw_budget=4,
                    )
                )
            ),
        ),
    )

    assert decision["uniform_tuning_stop_reason"] == (
        "global_compile_budget_exhausted"
    )
    assert decision["refinement_stop_reason"] == "hard_resource_block"


def test_decision_preserves_failure_exhaustion_over_draw_budget_fallback() -> None:
    session = _session(draw_budget=2)
    session, plus = next_coordinate_move(session)
    assert plus is not None
    session = record_coordinate_failure(session, plus)
    session, minus = next_coordinate_move(session)
    assert minus is not None
    session = record_coordinate_failure(session, minus)
    assert session.stop_reason == "candidate_failures_exhausted"
    ledger = AttemptLedger(
        draw_count=3,
        uniform_tuning_draw_count=2,
    )
    state = {
        "current_best": SimpleNamespace(
            spec=SimpleNamespace(
                source_sha256="d" * 64,
                spec_sha256="e" * 64,
            ),
            mae=0.5,
            loss=0.5,
        ),
        "optimization_policy": SimpleNamespace(target_mae=0.1, target_loss=0.1),
        "direct_ledger": ledger,
        "refinement_blocked": False,
        "uniform_tuning_stop_reason": "candidate_failures_exhausted",
        "uniform_search_session": session,
        "uniform_search_source_sha256": "d" * 64,
        "uniform_search_base_spec_sha256": "a" * 64,
        "uniform_search_selected_spec_sha256": "e" * 64,
        "uniform_search_initial_loss": 0.5,
        "uniform_search_initial_mae": 0.5,
        "uniform_search_selected_loss": 0.5,
        "uniform_search_selected_mae": 0.5,
        "uniform_search_initial_draw_count": 0,
        "uniform_optimized_source_sha256s": (),
        "completed_nodes": (),
    }
    runtime = SimpleNamespace(
        context=SimpleNamespace(
            config=SimpleNamespace(
                draw_budget=10,
                uniform_tuning_draw_budget=2,
            )
        )
    )

    update = decide_uniform_optimization(
        cast(Any, state),
        cast(Any, runtime),
    )

    assert update["uniform_tuning_stop_reason"] == "candidate_failures_exhausted"
    assert update["uniform_optimization_summary"].stop_reason == (
        "candidate_failures_exhausted"
    )
