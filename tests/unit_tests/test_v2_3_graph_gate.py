from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from shaderforge.benchmark import (
    LoadedV2Dataset,
    V2DatasetStageGate,
    evaluate_v2_dataset_stage_gate,
    load_v2_dataset_manifest,
)
from shaderforge.benchmark.v2_3_graph_gate import (
    V2_3_RESTART_PHASES,
    V2_3GraphCaseOutcome,
    V2_3GraphGateReport,
    V2_3RestartPhaseOutcome,
    evaluate_v2_3_graph_gate,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "benchmarks/png_to_shader_v2/dataset_manifest.v1.json"
CONFIG_SHA256 = "c" * 64
INTENT_OUTCOMES_SHA256 = "d" * 64
COMPILER_OUTCOMES_SHA256 = "e" * 64


def _dataset_gate() -> tuple[LoadedV2Dataset, V2DatasetStageGate]:
    dataset = load_v2_dataset_manifest(
        MANIFEST,
        benchmark_root=ROOT / "benchmarks",
        gate_stage="v2_3_graph_conformance",
    )
    gate = evaluate_v2_dataset_stage_gate(dataset, stage="v2_3_graph_conformance")
    return dataset, gate


def _perfect_outcomes(
    dataset: LoadedV2Dataset,
    gate: V2DatasetStageGate,
) -> tuple[V2_3GraphCaseOutcome, ...]:
    selected = (
        (
            "development",
            tuple(
                sample
                for sample in dataset.manifest.split("development").samples
                if sample.dataset_role == "regression"
                and sample.source_suite_id == "png_to_shader_v1_m0"
            ),
        ),
        ("validation", dataset.manifest.split("validation").samples),
    )
    outcomes: list[V2_3GraphCaseOutcome] = []
    for split, samples in selected:
        for index, sample in enumerate(samples, start=1):
            state_hash = f"{index + (100 if split == 'validation' else 0):064x}"
            supported = (
                sample.topology == "solid"
                and sample.instance_count == 1
                and sample.hole_count == 0
            )
            outcomes.append(
                V2_3GraphCaseOutcome(
                    manifest_id=gate.manifest_id,
                    dataset_version=gate.dataset_version,
                    manifest_sha256=gate.manifest_sha256,
                    taxonomy_sha256=gate.taxonomy_sha256,
                    config_sha256=CONFIG_SHA256,
                    input_intent_outcomes_sha256=INTENT_OUTCOMES_SHA256,
                    input_compiler_outcomes_sha256=COMPILER_OUTCOMES_SHA256,
                    split=split,
                    case_id=sample.case_id,
                    success=True,
                    expected_terminal_class=(
                        "objective_best"
                        if supported
                        else "unsupported_no_valid_candidate"
                    ),
                    supported_hypothesis_count=1 if supported else 0,
                    unsupported_hypothesis_count=0 if supported else 1,
                    hypothesis_capability_evidence_sha256="9" * 64,
                    terminal_phase="finalized",
                    stop_reason=(
                        "completed_with_objective_best"
                        if supported
                        else "no_valid_candidate"
                    ),
                    final_state_sha256=state_hash,
                    replay_final_state_sha256=state_hash,
                    expected_seed_attempt_count=3,
                    seed_attempt_count=3,
                    attempt_artifact_closure_count=3,
                    successful_candidate_count=3 if supported else 0,
                    branch_best_count=1 if supported else 0,
                    unsupported_attempt_count=0 if supported else 3,
                    unsupported_reason_codes=(
                        () if supported else ("typed_topology_receipt_unsupported",)
                    ),
                    unsupported_classification_verified=not supported,
                    artifact_manifest_sha256="a" * 64,
                    hypothesis_count=1,
                    hypothesis_ids=(f"hypothesis-{sample.case_id}",),
                    hypothesis_hashes=("b" * 64,),
                    hypothesis_identity_propagated=True,
                    restart_phase_results=(
                        tuple(
                            V2_3RestartPhaseOutcome(
                                phase=phase,
                                verified=True,
                                crash_state_projection_sha256=f"{500 + phase_index:064x}",
                                uninterrupted_final_state_sha256=state_hash,
                                resumed_final_state_sha256=state_hash,
                                side_effect_counts_match=True,
                                budget_match=True,
                                artifact_closure_match=True,
                                cursor_match=True,
                                evaluation_revision_match=True,
                            )
                            for phase_index, phase in enumerate(
                                V2_3_RESTART_PHASES
                            )
                        )
                        if index == 1
                        else ()
                    ),
                    deterministic_replay_verified=True,
                    cas_stale_write_rejected=True,
                    production_admission_enabled=False,
                    model_calls=0,
                )
            )
    return tuple(outcomes)


def _evaluate(
    dataset: LoadedV2Dataset,
    gate: V2DatasetStageGate,
    outcomes: tuple[V2_3GraphCaseOutcome, ...],
) -> V2_3GraphGateReport:
    return evaluate_v2_3_graph_gate(
        dataset,
        gate,
        outcomes,
        config_sha256=CONFIG_SHA256,
        input_intent_outcomes_sha256=INTENT_OUTCOMES_SHA256,
        input_compiler_outcomes_sha256=COMPILER_OUTCOMES_SHA256,
    )


def test_v2_3_graph_gate_requires_full_split_and_restart_evidence() -> None:
    dataset, gate = _dataset_gate()

    report = _evaluate(dataset, gate, _perfect_outcomes(dataset, gate))

    assert report.ready
    assert report.blockers == ()
    assert report.cases_passed.model_dump() == {"numerator": 51, "denominator": 51}
    assert report.seed_attempts.model_dump() == {
        "numerator": 153,
        "denominator": 153,
    }
    assert report.attempt_artifact_closures.numerator == 153
    assert report.successful_candidates.numerator == 54
    assert report.hypothesis_branch_bests.model_dump() == {
        "numerator": 18,
        "denominator": 18,
    }
    assert report.objective_best_cases.model_dump() == {
        "numerator": 18,
        "denominator": 18,
    }
    assert report.expected_unsupported_no_candidate_cases.model_dump() == {
        "numerator": 33,
        "denominator": 33,
    }
    assert report.development.cases_passed.denominator == 10
    assert report.development.seed_attempts.denominator == 30
    assert report.validation.cases_passed.denominator == 41
    assert report.validation.seed_attempts.denominator == 123
    assert report.model_calls == 0
    assert report.production_admission_enabled is False
    assert all(
        item.recoveries.model_dump() == {"numerator": 2, "denominator": 2}
        for item in report.restart_phase_recoveries
    )
    assert all(
        item.recoveries.model_dump() == {"numerator": 1, "denominator": 1}
        for item in report.development.restart_phase_recoveries
    )


def test_failure_keeps_case_and_seed_denominators() -> None:
    dataset, gate = _dataset_gate()
    outcomes = list(_perfect_outcomes(dataset, gate))
    outcomes[0] = outcomes[0].model_copy(
        update={
            "success": False,
            "seed_attempt_count": 2,
            "failure_code": "seed_attempt_count_mismatch",
        }
    )

    report = _evaluate(dataset, gate, tuple(outcomes))

    assert not report.ready
    assert report.cases_passed.model_dump() == {"numerator": 50, "denominator": 51}
    assert report.seed_attempts.model_dump() == {
        "numerator": 152,
        "denominator": 153,
    }
    assert report.development.cases_passed.denominator == 10
    assert "case_failures:50/51" in report.blockers
    assert "seed_attempts:152/153" in report.blockers


@pytest.mark.parametrize("failure", ["missing", "duplicate", "extra", "release"])
def test_gate_rejects_non_closed_or_release_case_sets(failure: str) -> None:
    dataset, gate = _dataset_gate()
    outcomes = list(_perfect_outcomes(dataset, gate))
    if failure == "missing":
        outcomes.pop()
    elif failure == "duplicate":
        outcomes.append(outcomes[0])
    elif failure == "extra":
        outcomes.append(outcomes[0].model_copy(update={"case_id": "unknown"}))
    else:
        outcomes.append(
            outcomes[0].model_copy(
                update={"split": "release-held-out", "case_id": "sealed"}
            )
        )

    with pytest.raises(ValueError):
        _evaluate(dataset, gate, tuple(outcomes))


def test_gate_rejects_hash_or_stage_mismatch() -> None:
    dataset, gate = _dataset_gate()
    outcomes = list(_perfect_outcomes(dataset, gate))
    outcomes[0] = outcomes[0].model_copy(update={"config_sha256": "f" * 64})
    with pytest.raises(ValueError, match="身份/hash"):
        _evaluate(dataset, gate, tuple(outcomes))

    wrong_stage = replace(dataset, gate_stage="v2_2_genome_compiler")
    with pytest.raises(ValueError, match="V2.3 graph conformance"):
        _evaluate(wrong_stage, gate, _perfect_outcomes(dataset, gate))


def test_outcome_rejects_fake_success_and_fake_replay() -> None:
    dataset, gate = _dataset_gate()
    perfect = _perfect_outcomes(dataset, gate)[0]

    with pytest.raises(ValueError, match="success"):
        V2_3GraphCaseOutcome.model_validate(
            perfect.model_copy(update={"model_calls": 1}).model_dump(), strict=True
        )
    with pytest.raises(ValueError, match="State hash"):
        V2_3GraphCaseOutcome.model_validate(
            perfect.model_copy(
                update={"replay_final_state_sha256": "f" * 64}
            ).model_dump(),
            strict=True,
        )


def test_restart_matrix_missing_or_failed_phase_blocks_gate() -> None:
    dataset, gate = _dataset_gate()
    outcomes = list(_perfect_outcomes(dataset, gate))
    development = next(
        index
        for index, outcome in enumerate(outcomes)
        if outcome.split == "development" and outcome.restart_phase_results
    )
    outcomes[development] = outcomes[development].model_copy(
        update={
            "restart_phase_results": outcomes[
                development
            ].restart_phase_results[:-1]
        }
    )
    report = _evaluate(dataset, gate, tuple(outcomes))
    assert not report.ready
    assert "restart_phase_selected:0/0" in report.development.blockers

    outcomes = list(_perfect_outcomes(dataset, gate))
    first = outcomes[development]
    failed = first.restart_phase_results[0].model_copy(
        update={
            "verified": False,
            "resumed_final_state_sha256": "f" * 64,
        }
    )
    outcomes[development] = first.model_copy(
        update={"restart_phase_results": (failed, *first.restart_phase_results[1:])}
    )
    report = _evaluate(dataset, gate, tuple(outcomes))
    assert not report.ready
    assert "restart_phase_measured:0/1" in report.development.blockers
