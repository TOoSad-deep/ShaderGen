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
from shaderforge.benchmark.v2_2_compiler_gate import (
    V2_2CompilerCaseOutcome,
    V2_2CompilerGateReport,
    evaluate_v2_2_compiler_gate,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "benchmarks/png_to_shader_v2/dataset_manifest.v1.json"
CONFIG_SHA256 = "c" * 64
INPUT_OUTCOMES_SHA256 = "d" * 64


def _dataset_gate() -> tuple[LoadedV2Dataset, V2DatasetStageGate]:
    dataset = load_v2_dataset_manifest(
        MANIFEST,
        benchmark_root=ROOT / "benchmarks",
        gate_stage="v2_2_genome_compiler",
    )
    gate = evaluate_v2_dataset_stage_gate(dataset, stage="v2_2_genome_compiler")
    return dataset, gate


def _perfect_outcomes(
    dataset: LoadedV2Dataset,
    gate: V2DatasetStageGate,
    *,
    webgl: bool = False,
) -> tuple[V2_2CompilerCaseOutcome, ...]:
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
    return tuple(
        V2_2CompilerCaseOutcome(
            manifest_id=gate.manifest_id,
            dataset_version=gate.dataset_version,
            manifest_sha256=gate.manifest_sha256,
            taxonomy_sha256=gate.taxonomy_sha256,
            config_sha256=CONFIG_SHA256,
            input_intent_outcomes_sha256=INPUT_OUTCOMES_SHA256,
            split=split,
            case_id=sample.case_id,
            success=True,
            genome_count=3,
            semantic_genome_hashes=(
                f"{index + 1:064x}",
                f"{index + 1001:064x}",
                f"{index + 2001:064x}",
            ),
            distinct_structural_signatures=3,
            diversity_gate_passed=True,
            deterministic_compile_success_count=3,
            static_validation_success_count=3,
            webgl_requested=webgl,
            webgl_success_count=3 if webgl else None,
        )
        for split, samples in selected
        for index, sample in enumerate(samples)
    )


def _evaluate(
    dataset: LoadedV2Dataset,
    gate: V2DatasetStageGate,
    outcomes: tuple[V2_2CompilerCaseOutcome, ...],
    *,
    webgl: bool = False,
) -> V2_2CompilerGateReport:
    return evaluate_v2_2_compiler_gate(
        dataset,
        gate,
        outcomes,
        config_sha256=CONFIG_SHA256,
        input_intent_outcomes_sha256=INPUT_OUTCOMES_SHA256,
        webgl_requested=webgl,
    )


def test_v2_2_gate_requires_all_51_intents_and_153_compiles() -> None:
    dataset, gate = _dataset_gate()

    report = _evaluate(dataset, gate, _perfect_outcomes(dataset, gate))

    assert report.ready
    assert report.blockers == ()
    assert report.cases_passed.numerator == 51
    assert report.cases_passed.denominator == 51
    assert report.legal_genomes.numerator == 153
    assert report.unique_semantic_hash_cases.numerator == 51
    assert report.structurally_diverse_cases.numerator == 51
    assert report.deterministic_compiles.numerator == 153
    assert report.static_validations.numerator == 153
    assert report.webgl_requested is False
    assert report.webgl_compiles_and_draws is None


def test_v2_2_gate_reports_webgl_only_when_explicitly_executed() -> None:
    dataset, gate = _dataset_gate()

    report = _evaluate(
        dataset,
        gate,
        _perfect_outcomes(dataset, gate, webgl=True),
        webgl=True,
    )

    assert report.ready
    assert report.webgl_compiles_and_draws is not None
    assert report.webgl_compiles_and_draws.numerator == 153
    assert report.webgl_compiles_and_draws.denominator == 153


def test_failure_keeps_case_and_genome_denominators() -> None:
    dataset, gate = _dataset_gate()
    outcomes = list(_perfect_outcomes(dataset, gate))
    outcomes[0] = outcomes[0].model_copy(
        update={
            "success": False,
            "deterministic_compile_success_count": 2,
            "static_validation_success_count": 2,
            "failure_code": "deterministic_compile_failed",
        }
    )

    report = _evaluate(dataset, gate, tuple(outcomes))

    assert not report.ready
    assert report.cases_passed.numerator == 50
    assert report.cases_passed.denominator == 51
    assert report.deterministic_compiles.numerator == 152
    assert report.deterministic_compiles.denominator == 153
    assert "case_failures:50/51" in report.blockers
    assert "deterministic_compile_failures:152/153" in report.blockers


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


def test_gate_rejects_hash_webgl_or_stage_mismatch() -> None:
    dataset, gate = _dataset_gate()
    outcomes = list(_perfect_outcomes(dataset, gate))
    outcomes[0] = outcomes[0].model_copy(update={"config_sha256": "f" * 64})
    with pytest.raises(ValueError, match="身份/hash"):
        _evaluate(dataset, gate, tuple(outcomes))

    with pytest.raises(ValueError, match="WebGL"):
        _evaluate(dataset, gate, _perfect_outcomes(dataset, gate), webgl=True)

    wrong_stage = replace(dataset, gate_stage="v2_1_intent")
    with pytest.raises(ValueError, match="V2.2 gate_stage"):
        _evaluate(wrong_stage, gate, _perfect_outcomes(dataset, gate))


def test_outcome_rejects_fake_success_and_fake_unexecuted_webgl() -> None:
    _dataset, gate = _dataset_gate()
    common = {
        "manifest_id": gate.manifest_id,
        "dataset_version": gate.dataset_version,
        "manifest_sha256": gate.manifest_sha256,
        "taxonomy_sha256": gate.taxonomy_sha256,
        "config_sha256": CONFIG_SHA256,
        "input_intent_outcomes_sha256": INPUT_OUTCOMES_SHA256,
        "split": "validation",
        "case_id": "case",
        "genome_count": 3,
        "semantic_genome_hashes": ("1" * 64, "2" * 64, "3" * 64),
        "distinct_structural_signatures": 2,
        "diversity_gate_passed": True,
        "deterministic_compile_success_count": 2,
        "static_validation_success_count": 2,
    }
    with pytest.raises(ValueError, match="success"):
        V2_2CompilerCaseOutcome(
            **common,
            success=True,
            webgl_requested=False,
            failure_code=None,
        )
    with pytest.raises(ValueError, match="WebGL"):
        V2_2CompilerCaseOutcome(
            **common,
            success=False,
            webgl_requested=False,
            webgl_success_count=2,
            failure_code="deterministic_compile_failed",
        )
