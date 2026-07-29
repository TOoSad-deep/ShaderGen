"""Regression tests for source/session-scoped uniform optimization summaries."""

from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from types import SimpleNamespace
from typing import Any, cast

import pytest

from agent.app.contracts.layerplan_glsl_direct import AttemptLedger
from agent.app.nodes.layered_direct import uniform_optimization_nodes
from agent.app.nodes.layered_direct.lifecycle_nodes import (
    _uniform_session_trace_sha256,
)
from agent.app.nodes.layered_direct.uniform_optimization_nodes import (
    _summary,
    decide_uniform_optimization,
)
from shaderforge.program_spec import canonical_json
from shaderforge.uniform_optimization import (
    FlatTunableComponent,
    UniformOptimizationConfig,
    UniformOptimizationError,
    UniformOptimizationSummaryV2,
    start_coordinate_pattern_session,
)


def _session():
    component = FlatTunableComponent(
        layer_id="shape",
        path="u_gain",
        component_index=0,
        minimum=Decimal("0"),
        maximum=Decimal("1"),
        step=Decimal("0.1"),
        base_value=Decimal("0.5"),
    )
    return start_coordinate_pattern_session(
        base_program_spec_sha256="a" * 64,
        components=(component,),
        config=UniformOptimizationConfig(draw_budget=4, active_component_cap=1),
    )


def test_summary_uses_session_selected_metrics_not_structural_refine_gain() -> None:
    session = _session()
    state = {
        "uniform_search_session": session,
        "uniform_search_source_sha256": "b" * 64,
        "uniform_search_base_spec_sha256": "a" * 64,
        "uniform_search_selected_spec_sha256": "c" * 64,
        "uniform_search_initial_loss": 0.5,
        "uniform_search_initial_mae": 0.4,
        "uniform_search_selected_loss": 0.45,
        "uniform_search_selected_mae": 0.35,
        "uniform_search_initial_draw_count": 2,
        "current_best": SimpleNamespace(
            spec=SimpleNamespace(
                source_sha256="b" * 64,
                spec_sha256="d" * 64,
            ),
            loss=0.1,
            mae=0.1,
        ),
        "direct_ledger": AttemptLedger(uniform_tuning_draw_count=3),
    }

    summary = _summary(cast(Any, state), stop_reason="local_optimum")

    assert summary is not None
    assert summary.base_spec_sha256 == "a" * 64
    assert summary.selected_spec_sha256 == "c" * 64
    assert summary.final_loss == pytest.approx(0.45)
    assert summary.final_mae == pytest.approx(0.35)
    assert summary.loss_delta == pytest.approx(0.05)
    assert summary.mae_delta == pytest.approx(0.05)
    assert summary.active_component_count == 1
    assert summary.evaluated_count == 0
    assert summary.accepted_count == 0
    assert summary.draw_count == 1


def test_summary_rejects_a_session_from_another_source() -> None:
    state = {
        "uniform_search_session": _session(),
        "uniform_search_source_sha256": "b" * 64,
        "uniform_search_base_spec_sha256": "a" * 64,
        "uniform_search_selected_spec_sha256": "c" * 64,
        "uniform_search_initial_loss": 0.5,
        "uniform_search_initial_mae": 0.4,
        "uniform_search_selected_loss": 0.45,
        "uniform_search_selected_mae": 0.35,
        "uniform_search_initial_draw_count": 0,
        "current_best": SimpleNamespace(
            spec=SimpleNamespace(source_sha256="e" * 64),
        ),
        "direct_ledger": AttemptLedger(),
    }

    assert _summary(cast(Any, state), stop_reason="target_reached") is None


def test_new_source_manifest_error_clears_stale_summary_and_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_summary = UniformOptimizationSummaryV2(
        base_spec_sha256="a" * 64,
        selected_spec_sha256="c" * 64,
        config_fingerprint="f" * 64,
        active_component_count=1,
        evaluated_count=1,
        accepted_count=1,
        draw_count=1,
        draw_budget=4,
        initial_loss=0.5,
        initial_mae=0.4,
        final_loss=0.45,
        final_mae=0.35,
        loss_delta=0.05,
        mae_delta=0.05,
        stop_reason="local_optimum",
    )

    def fail_flatten(_layered: object, _program: object) -> tuple[()]:
        raise UniformOptimizationError("invalid_manifest", "invalid")

    monkeypatch.setattr(
        uniform_optimization_nodes,
        "flatten_tunable_components",
        fail_flatten,
    )
    state = {
        "current_best": SimpleNamespace(
            layered_spec=object(),
            spec=SimpleNamespace(
                source_sha256="e" * 64,
                spec_sha256="d" * 64,
            ),
            loss=0.4,
            mae=0.3,
        ),
        "optimization_policy": SimpleNamespace(target_mae=0.1, target_loss=0.1),
        "direct_ledger": AttemptLedger(),
        "refinement_blocked": False,
        "uniform_search_session": _session(),
        "uniform_search_source_sha256": "b" * 64,
        "uniform_optimized_source_sha256s": ("b" * 64,),
        "uniform_optimization_summary": stale_summary,
        "uniform_optimization_trace": [{"move_ordinal": 1}],
        "completed_nodes": (),
    }
    runtime = SimpleNamespace(
        context=SimpleNamespace(
            config=SimpleNamespace(
                draw_budget=8,
                uniform_tuning_draw_budget=4,
                uniform_tuning_active_component_cap=8,
                uniform_tuning_max_passes=1,
            )
        )
    )

    update = decide_uniform_optimization(
        cast(Any, state),
        cast(Any, runtime),
    )

    assert update["uniform_tuning_stop_reason"] == "no_feasible_components"
    assert update["uniform_optimization_summary"] is None
    assert update["uniform_search_session"] is None
    assert update["uniform_search_source_sha256"] is None
    assert update["uniform_search_base_spec_sha256"] is None
    assert update["uniform_search_selected_spec_sha256"] is None
    assert update["uniform_search_trace_start_index"] is None
    assert update["uniform_optimized_source_sha256s"] == ("b" * 64, "e" * 64)


def test_summary_trace_hash_uses_only_the_current_session_slice() -> None:
    trace = (
        {"move_ordinal": 1, "candidate_spec_sha256": "a" * 64},
        {"move_ordinal": 1, "candidate_spec_sha256": "b" * 64},
        {"move_ordinal": 2, "candidate_spec_sha256": "c" * 64},
    )

    assert _uniform_session_trace_sha256(trace, 1) == (
        sha256(canonical_json(list(trace[1:])).encode("utf-8")).hexdigest()
    )
    assert _uniform_session_trace_sha256(trace, len(trace)) is None
    assert _uniform_session_trace_sha256(trace, None) is None
