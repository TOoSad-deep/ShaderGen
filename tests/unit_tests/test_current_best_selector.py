from __future__ import annotations

from dataclasses import replace
from typing import Literal

import pytest

from shaderforge.contracts import AcceptancePolicy
from shaderforge.evaluation import (
    CandidateRecord,
    GeneratorAdmissionEvidence,
    MeasurementSeedAdmissionPolicy,
    ScoreBreakdownV1,
    TargetStructureFacts,
    build_generator_admission_evidence,
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


OFFLINE_POLICY = MeasurementSeedAdmissionPolicy(
    allowed_evidence_scopes=("offline_replay",)
)


def deterministic_candidate(
    candidate_id: str,
    value: ScoreBreakdownV1 | None,
    *,
    generator_version: str = "measurement_affine_seed_v1",
) -> CandidateRecord:
    return replace(
        candidate(candidate_id, value),
        origin="deterministic",
        generator_version=generator_version,
    )


def admission_evidence(
    value: CandidateRecord,
    target: TargetStructureFacts,
    *,
    evidence_scope: Literal["offline_replay", "runtime_verified"] = "offline_replay",
) -> GeneratorAdmissionEvidence:
    assert value.render_sha256 is not None
    return build_generator_admission_evidence(
        target,
        origin=value.origin,
        generator_version=value.generator_version,
        evidence_scope=evidence_scope,
        evidence_ref="diagnostics/report-capability-v2.json",
        evidence_sha256="c" * 64,
        target_source_sha256="d" * 64,
        normalized_reference_sha256="e" * 64,
        candidate_id=value.candidate_id,
        candidate_glsl_sha256=value.glsl_sha256,
        candidate_render_sha256=value.render_sha256,
    )


def solid_base_fill() -> TargetStructureFacts:
    return TargetStructureFacts(
        topology="solid",
        instance_count=1,
        hole_count=0,
        required_layers=("base_fill",),
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


def test_admission_is_opt_in_and_default_selector_semantics_stay_unchanged() -> None:
    best = candidate("best", score(0.30))
    seed = deterministic_candidate("seed", score(0.10))

    decision = select_current_best(best, seed, AcceptancePolicy())

    assert decision.accepted is True
    assert decision.reason == "improved"
    assert "admission_status" not in decision.to_dict()


def test_supported_deterministic_candidate_still_uses_existing_selector_rules() -> None:
    best = candidate("best", score(0.30))
    seed = deterministic_candidate("seed", score(0.10))
    evidence = admission_evidence(seed, solid_base_fill())

    decision = select_current_best(
        best,
        seed,
        AcceptancePolicy(),
        admission_policy=OFFLINE_POLICY,
        admission_evidence=evidence,
    )

    assert decision.accepted is True
    assert decision.reason == "improved"
    assert decision.admission_status == "admitted"
    assert decision.admission_reason_codes == (
        "labels_within_generator_capability",
    )


@pytest.mark.parametrize(
    "target",
    (
        TargetStructureFacts(
            topology="solid",
            instance_count=2,
            hole_count=0,
            required_layers=("base_fill",),
        ),
        TargetStructureFacts(
            topology="ring",
            instance_count=1,
            hole_count=1,
            required_layers=("base_fill",),
        ),
        TargetStructureFacts(
            topology="solid",
            instance_count=1,
            hole_count=0,
            required_layers=("base_fill", "highlight"),
        ),
    ),
)
def test_unsupported_structure_vetoes_large_score_improvement(
    target: TargetStructureFacts,
) -> None:
    best = candidate("best", score(0.90))
    seed = deterministic_candidate("seed", score(0.01))

    decision = select_current_best(
        best,
        seed,
        AcceptancePolicy(),
        admission_policy=OFFLINE_POLICY,
        admission_evidence=admission_evidence(seed, target),
    )

    assert decision.accepted is False
    assert decision.reason == "generator_capability_unsupported"
    assert decision.admission_status == "unsupported"
    assert decision.total_improvement is None


def test_missing_unknown_and_content_mismatched_admission_fail_closed() -> None:
    best = candidate("best", score(0.30))
    seed = deterministic_candidate("seed", score(0.10))
    unknown = deterministic_candidate(
        "future",
        score(0.10),
        generator_version="future_seed_v2",
    )
    evidence = admission_evidence(seed, solid_base_fill())
    mismatched_seed = replace(seed, glsl_sha256="f" * 64)

    missing_decision = select_current_best(
        best,
        seed,
        AcceptancePolicy(),
        admission_policy=OFFLINE_POLICY,
    )
    unknown_decision = select_current_best(
        best,
        unknown,
        AcceptancePolicy(),
        admission_policy=OFFLINE_POLICY,
        admission_evidence=admission_evidence(unknown, solid_base_fill()),
    )
    mismatch_decision = select_current_best(
        best,
        mismatched_seed,
        AcceptancePolicy(),
        admission_policy=OFFLINE_POLICY,
        admission_evidence=evidence,
    )

    assert missing_decision.reason == "generator_capability_unknown"
    assert missing_decision.admission_reason_codes == (
        "generator_admission_evidence_missing",
    )
    assert unknown_decision.reason == "generator_capability_unknown"
    assert unknown_decision.admission_reason_codes == (
        "unknown_deterministic_generator",
    )
    assert mismatch_decision.reason == "generator_capability_unknown"
    assert mismatch_decision.admission_reason_codes == (
        "generator_admission_identity_mismatch",
    )


def test_compile_and_score_facts_keep_priority_over_missing_admission() -> None:
    failed = replace(
        deterministic_candidate("failed", score(0.10)),
        hard_constraints_passed=False,
    )
    unscored = deterministic_candidate("unscored", None)

    failed_decision = select_current_best(
        None,
        failed,
        AcceptancePolicy(),
        admission_policy=OFFLINE_POLICY,
    )
    unscored_decision = select_current_best(
        None,
        unscored,
        AcceptancePolicy(),
        admission_policy=OFFLINE_POLICY,
    )

    assert failed_decision.reason == "hard_constraints_failed"
    assert failed_decision.admission_status is None
    assert unscored_decision.reason == "score_missing"
    assert unscored_decision.admission_status is None


def test_admission_evidence_without_policy_fails_fast() -> None:
    seed = deterministic_candidate("seed", score(0.10))

    with pytest.raises(ValueError, match="admission_policy"):
        select_current_best(
            None,
            seed,
            AcceptancePolicy(),
            admission_evidence=admission_evidence(seed, solid_base_fill()),
        )


def test_runtime_verified_scope_fails_closed_until_verifier_exists() -> None:
    best = candidate("best", score(0.30))
    seed = deterministic_candidate("seed", score(0.10))

    decision = select_current_best(
        best,
        seed,
        AcceptancePolicy(),
        admission_policy=MeasurementSeedAdmissionPolicy(),
        admission_evidence=admission_evidence(
            seed,
            solid_base_fill(),
            evidence_scope="runtime_verified",
        ),
    )

    assert decision.accepted is False
    assert decision.reason == "generator_capability_unknown"
    assert decision.admission_reason_codes == (
        "runtime_evidence_verifier_unavailable",
    )


@pytest.mark.parametrize(
    ("topology", "hole_count"),
    (("solid", 1), ("ring", 0), ("hollow", 0)),
)
def test_target_structure_rejects_topology_hole_contradictions(
    topology: Literal["solid", "ring", "hollow"],
    hole_count: int,
) -> None:
    with pytest.raises(ValueError, match="hole_count"):
        TargetStructureFacts(
            topology=topology,
            instance_count=1,
            hole_count=hole_count,
            required_layers=("base_fill",),
        )


def test_model_candidate_is_byte_for_byte_selection_compatible_with_opt_in_policy() -> None:
    best = candidate("best", score(0.30))
    model_candidate = candidate("model", score(0.10))

    baseline = select_current_best(best, model_candidate, AcceptancePolicy())
    opted_in = select_current_best(
        best,
        model_candidate,
        AcceptancePolicy(),
        admission_policy=OFFLINE_POLICY,
    )

    assert opted_in == baseline
    assert opted_in.to_dict() == baseline.to_dict()


def test_supported_admission_does_not_bypass_protected_region_gate() -> None:
    best = candidate("best", score(0.30, protected=(("center", 0.10),)))
    seed = deterministic_candidate(
        "seed",
        score(0.10, protected=(("center", 0.20),)),
    )

    decision = select_current_best(
        best,
        seed,
        AcceptancePolicy(),
        admission_policy=OFFLINE_POLICY,
        admission_evidence=admission_evidence(seed, solid_base_fill()),
    )

    assert decision.accepted is False
    assert decision.reason == "protected_region_regression"
    assert decision.admission_status == "admitted"
