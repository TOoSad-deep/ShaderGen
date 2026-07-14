from __future__ import annotations

from dataclasses import replace

import pytest

from shaderforge.contracts import AcceptancePolicy
from shaderforge.evaluation import (
    CandidateRecord,
    ScoreBreakdownV1,
    select_current_best,
)


def score(
    total_loss: float,
    *,
    protected: tuple[tuple[str, float], ...] = (("center", 0.10),),
) -> ScoreBreakdownV1:
    return ScoreBreakdownV1(
        metric_version="test_metric_v1",
        total_loss=total_loss,
        global_rmse=total_loss,
        global_mae=total_loss,
        edge_loss=total_loss,
        geometry_loss=total_loss,
        representative_pixel_loss=total_loss,
        roi_losses=(("subject", total_loss),),
        protected_region_losses=protected,
        effective_weights=(("global_rmse", 1.0),),
        diagnostics=(),
    )


def candidate(candidate_id: str, value: ScoreBreakdownV1 | None) -> CandidateRecord:
    return CandidateRecord(
        candidate_id=candidate_id,
        parent_candidate_id=None,
        glsl_sha256="a" * 64,
        glsl_ref=f"candidates/{candidate_id}/shader.frag",
        author_ref=f"candidates/{candidate_id}/author.json",
        provenance_ref=f"candidates/{candidate_id}/provenance.json",
        compile_ref=f"candidates/{candidate_id}/compile.json",
        render_ref=f"candidates/{candidate_id}/render.png",
        render_sha256="b" * 64,
        metrics_ref=f"candidates/{candidate_id}/metrics.json",
        review_ref=None,
        iteration=0,
        changed_problem_domain="initial_build",
        prompt_version="shader_author_initial_v1",
        model_ref="fake:quality",
        score_summary=value,
        hard_constraints_passed=True,
    )


def test_first_valid_candidate_becomes_current_best() -> None:
    decision = select_current_best(
        None, candidate("first", score(0.30)), AcceptancePolicy()
    )

    assert decision.accepted is True
    assert decision.reason == "first_valid_candidate"


def test_candidate_requires_minimum_total_improvement() -> None:
    best = candidate("best", score(0.30))
    too_small = candidate("small", score(0.296))
    exact = candidate("exact", score(0.295))

    rejected = select_current_best(best, too_small, AcceptancePolicy())
    accepted = select_current_best(best, exact, AcceptancePolicy())

    assert rejected.accepted is False
    assert rejected.reason == "insufficient_total_improvement"
    assert accepted.accepted is True
    assert accepted.reason == "improved"


def test_protected_regression_vetoes_better_total_score() -> None:
    best = candidate("best", score(0.30, protected=(("center", 0.10),)))
    regressed = candidate(
        "regressed",
        score(0.20, protected=(("center", 0.121),)),
    )

    decision = select_current_best(best, regressed, AcceptancePolicy())

    assert decision.accepted is False
    assert decision.reason == "protected_region_regression"
    assert decision.max_protected_regression == pytest.approx(0.021)


def test_missing_protected_evidence_is_not_treated_as_zero_regression() -> None:
    best = candidate("best", score(0.30, protected=(("center", 0.10),)))
    missing = candidate("missing", score(0.20, protected=()))

    decision = select_current_best(best, missing, AcceptancePolicy())

    assert decision.accepted is False
    assert decision.reason == "protected_evidence_missing"


def test_failed_hard_constraints_and_round_trip_remain_rejected() -> None:
    failed = replace(candidate("failed", score(0.10)), hard_constraints_passed=False)
    restored = CandidateRecord.from_dict(failed.to_dict())

    decision = select_current_best(None, restored, AcceptancePolicy())

    assert restored == failed
    assert decision.accepted is False
    assert decision.reason == "hard_constraints_failed"
