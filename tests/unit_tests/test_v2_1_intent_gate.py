from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from shaderforge.benchmark import (
    LoadedV2Dataset,
    V2_1IntentCaseOutcome,
    V2DatasetStageGate,
    evaluate_v2_1_intent_gate,
    evaluate_v2_dataset_stage_gate,
    load_v2_dataset_manifest,
)
from shaderforge.benchmark.v2_dataset import (
    DatasetSplitName,
    FillTopology,
    RequiredLayer,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "benchmarks/png_to_shader_v2/dataset_manifest.v1.json"


def _dataset_gate() -> tuple[LoadedV2Dataset, V2DatasetStageGate]:
    dataset = load_v2_dataset_manifest(
        MANIFEST,
        benchmark_root=ROOT / "benchmarks",
        gate_stage="v2_1_intent",
    )
    gate = evaluate_v2_dataset_stage_gate(dataset, stage="v2_1_intent")
    return dataset, gate


def _outcome(
    gate: V2DatasetStageGate,
    *,
    split: DatasetSplitName,
    case_id: str,
    topology: FillTopology,
    instance_count: int,
    required_layers: tuple[RequiredLayer, ...],
) -> V2_1IntentCaseOutcome:
    return V2_1IntentCaseOutcome(
        manifest_id=gate.manifest_id,
        dataset_version=gate.dataset_version,
        manifest_sha256=gate.manifest_sha256,
        taxonomy_sha256=gate.taxonomy_sha256,
        split=split,
        case_id=case_id,
        intent_valid=True,
        predicted_topology=topology,
        predicted_instance_count=instance_count,
        predicted_required_layers=required_layers,
    )


def _perfect_outcomes(
    dataset: LoadedV2Dataset,
    gate: V2DatasetStageGate,
) -> tuple[V2_1IntentCaseOutcome, ...]:
    return tuple(
        _outcome(
            gate,
            split=split.name,
            case_id=sample.case_id,
            topology=sample.topology,
            instance_count=sample.instance_count,
            required_layers=sample.required_layers,
        )
        for split in (
            dataset.manifest.split("development"),
            dataset.manifest.split("validation"),
        )
        for sample in split.samples
    )


def test_v2_1_intent_gate_reports_current_10_and_validation_metrics() -> None:
    dataset, gate = _dataset_gate()
    outcomes = _perfect_outcomes(dataset, gate)

    report = evaluate_v2_1_intent_gate(dataset, gate, outcomes)

    assert report.ready
    assert report.blockers == ()
    assert report.current_10_intent_legal.numerator == 10
    assert report.current_10_intent_legal.denominator == 10
    assert report.current_10_intent_legal.value == 1.0
    assert 0.72 < report.current_10_intent_legal.ci95.lower < 0.73
    assert report.validation_intent_legal.numerator == 41
    assert report.validation_intent_legal.denominator == 41
    assert report.validation_instance_count_exact.value == 1.0
    assert {
        item.class_id: item.recall.denominator for item in report.critical_class_metrics
    } == {
        "multi_instance": 11,
        "ring": 20,
        "hollow": 10,
        "required_highlight": 16,
        "required_rim": 26,
        "required_outline": 36,
    }
    assert all(item.recall.value == 1.0 for item in report.critical_class_metrics)
    assert all(item.f1 == 1.0 for item in report.critical_class_metrics)
    assert report.macro_recall == 1.0
    assert report.macro_f1 == 1.0


def test_gate_is_order_independent_but_binds_outcome_content() -> None:
    dataset, gate = _dataset_gate()
    outcomes = _perfect_outcomes(dataset, gate)

    forward = evaluate_v2_1_intent_gate(dataset, gate, outcomes)
    reverse = evaluate_v2_1_intent_gate(dataset, gate, tuple(reversed(outcomes)))

    assert reverse == forward
    assert reverse.outcomes_sha256 == forward.outcomes_sha256


def test_validation_legal_rate_below_80_percent_blocks_without_dropping_cases() -> None:
    dataset, gate = _dataset_gate()
    outcomes = list(_perfect_outcomes(dataset, gate))
    validation_indexes = [
        index for index, outcome in enumerate(outcomes) if outcome.split == "validation"
    ]
    for index in validation_indexes[:9]:
        original = outcomes[index]
        outcomes[index] = V2_1IntentCaseOutcome(
            manifest_id=original.manifest_id,
            dataset_version=original.dataset_version,
            manifest_sha256=original.manifest_sha256,
            taxonomy_sha256=original.taxonomy_sha256,
            split=original.split,
            case_id=original.case_id,
            intent_valid=False,
            predicted_topology=None,
            predicted_instance_count=None,
            predicted_required_layers=(),
            failure_code="intent_validation_failed",
        )

    report = evaluate_v2_1_intent_gate(dataset, gate, tuple(outcomes))

    assert report.validation_intent_legal.numerator == 32
    assert report.validation_intent_legal.denominator == 41
    assert not report.ready
    assert any(
        blocker.startswith("validation_intent_legal_below_80_percent:32/41")
        for blocker in report.blockers
    )


def test_critical_class_recall_below_90_percent_blocks_and_reports_f1() -> None:
    dataset, gate = _dataset_gate()
    outcomes = list(_perfect_outcomes(dataset, gate))
    changed = 0
    for index, outcome in enumerate(outcomes):
        if outcome.split != "validation" or outcome.predicted_topology != "ring":
            continue
        outcomes[index] = outcome.model_copy(update={"predicted_topology": "solid"})
        changed += 1
        if changed == 3:
            break

    report = evaluate_v2_1_intent_gate(dataset, gate, tuple(outcomes))
    ring = next(
        item for item in report.critical_class_metrics if item.class_id == "ring"
    )

    assert ring.true_positive == 17
    assert ring.false_negative == 3
    assert ring.recall.numerator == 17
    assert ring.recall.denominator == 20
    assert ring.recall.value == 0.85
    assert ring.f1_numerator == 34
    assert ring.f1_denominator == 37
    assert ring.f1 == pytest.approx(34 / 37)
    assert ring.recall.ci95.lower < ring.recall.value < ring.recall.ci95.upper
    assert not report.ready
    assert "critical_class_recall_below_90_percent:ring:17/20" in report.blockers


@pytest.mark.parametrize("failure", ["missing", "duplicate", "extra", "release"])
def test_gate_rejects_non_closed_or_release_case_sets(failure: str) -> None:
    dataset, gate = _dataset_gate()
    outcomes = list(_perfect_outcomes(dataset, gate))
    if failure == "missing":
        outcomes.pop()
    elif failure == "duplicate":
        outcomes.append(outcomes[0])
    elif failure == "extra":
        outcomes.append(outcomes[0].model_copy(update={"case_id": "unknown-case"}))
    else:
        outcomes.append(
            outcomes[0].model_copy(
                update={"split": "release-held-out", "case_id": "sealed-case"}
            )
        )

    with pytest.raises(ValueError):
        evaluate_v2_1_intent_gate(dataset, gate, tuple(outcomes))


def test_gate_rejects_stage_or_hash_mismatch() -> None:
    dataset, gate = _dataset_gate()
    outcomes = list(_perfect_outcomes(dataset, gate))
    wrong_stage = list(outcomes)
    wrong_stage[0] = wrong_stage[0].model_copy(
        update={"gate_stage": "v2_2_genome_compiler"}
    )
    with pytest.raises(ValueError, match="gate stage"):
        evaluate_v2_1_intent_gate(dataset, gate, tuple(wrong_stage))

    outcomes[0] = outcomes[0].model_copy(update={"manifest_sha256": "f" * 64})
    with pytest.raises(ValueError, match="dataset/hash"):
        evaluate_v2_1_intent_gate(dataset, gate, tuple(outcomes))

    wrong_stage_dataset = replace(dataset, gate_stage="v2_2_genome_compiler")
    with pytest.raises(ValueError, match="gate_stage='v2_1_intent'"):
        evaluate_v2_1_intent_gate(
            wrong_stage_dataset,
            gate,
            _perfect_outcomes(dataset, gate),
        )


def test_typed_outcome_rejects_partial_or_fake_predictions() -> None:
    _dataset, gate = _dataset_gate()
    with pytest.raises(ValueError, match="完整结构预测"):
        V2_1IntentCaseOutcome(
            manifest_id=gate.manifest_id,
            dataset_version=gate.dataset_version,
            manifest_sha256=gate.manifest_sha256,
            taxonomy_sha256=gate.taxonomy_sha256,
            split="validation",
            case_id="case",
            intent_valid=True,
            predicted_topology=None,
            predicted_instance_count=None,
        )
    with pytest.raises(ValueError, match="不得伪造预测"):
        V2_1IntentCaseOutcome(
            manifest_id=gate.manifest_id,
            dataset_version=gate.dataset_version,
            manifest_sha256=gate.manifest_sha256,
            taxonomy_sha256=gate.taxonomy_sha256,
            split="validation",
            case_id="case",
            intent_valid=False,
            predicted_topology="solid",
            predicted_instance_count=1,
            failure_code="failed",
        )
