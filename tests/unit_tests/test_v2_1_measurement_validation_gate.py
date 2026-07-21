from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from shaderforge.analysis import TargetHypothesis, measure_target_v2
from shaderforge.benchmark.v2_dataset import (
    V2DatasetSample,
    evaluate_v2_dataset_stage_gate,
    load_v2_dataset_manifest,
)
from shaderforge.contracts.taxonomy import REQUIRED_LAYER_ORDER
from shaderforge.intent.builder import build_intent_variants
from shaderforge.intent.constraints_builder import build_request_constraint_set
from shaderforge.intent.ir import (
    IntentBuildContext,
    LayerHypothesis,
    PrimitiveCandidate,
    RequiredLayerAssessment,
    StrategyHypothesis,
    VisualInterpretationV2,
)
from shaderforge.intent.models import Constraint, ContractConstraintValue
from shaderforge.store import (
    ArtifactRefV2,
    LocalArtifactCatalog,
    LocalArtifactStore,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = REPOSITORY_ROOT / "benchmarks"
MANIFEST = BENCHMARK_ROOT / "png_to_shader_v2/dataset_manifest.v1.json"


def _fraction(numerator: int, denominator: int) -> dict[str, int | float]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "ratio": numerator / denominator if denominator else 0.0,
    }


def _interpretation_evidence(ref: ArtifactRefV2) -> VisualInterpretationV2:
    return VisualInterpretationV2(
        summary="仅声明所有结构假设共有的 base fill。",
        layer_hypotheses=(
            LayerHypothesis(
                layer_id="base",
                role="base_fill",
                order=0,
                confidence=1.0,
                region_description="deterministic subject mask",
                primitive_candidates=("solid_fill",),
                evidence_refs=(ref,),
            ),
        ),
        required_layer_assessments=tuple(
            RequiredLayerAssessment(
                layer=layer,
                status="required" if layer == "base_fill" else "not_required",
                confidence=1.0,
                rationale="确定性测试闭集。",
                evidence_refs=(ref,),
            )
            for layer in REQUIRED_LAYER_ORDER
        ),
        primitive_candidates=(
            PrimitiveCandidate(
                candidate_id="base-solid",
                primitive_id="solid_fill",
                layer_id="base",
                confidence=1.0,
                evidence_refs=(ref,),
            ),
        ),
        strategy_hypotheses=(
            StrategyHypothesis(
                strategy_id="base-strategy",
                template_ids=("base-template",),
                required_layer_ids=("base",),
                complexity="low",
                confidence=1.0,
                evidence_refs=(ref,),
            ),
        ),
        evidence_refs=(ref,),
    )


def _intent_context(ref: ArtifactRefV2) -> IntentBuildContext:
    return IntentBuildContext(
        contract_id="webgl1_static_no_texture_v1",
        primitive_catalog_version="png_to_shader_expected_primitives_v1",
        primitive_catalog_sha256="a" * 64,
        template_catalog_version="png_to_shader_expected_primitives_v1",
        template_catalog_sha256="b" * 64,
        allowed_primitive_ids=("solid_fill",),
        allowed_template_ids=("base-template",),
        allowed_interpretation_evidence_refs=(ref,),
    )


def test_v2_1_current_ten_measurements_reach_exact_intent_branch(
    tmp_path: Path,
) -> None:
    dataset = load_v2_dataset_manifest(
        MANIFEST,
        benchmark_root=BENCHMARK_ROOT,
        gate_stage="v2_1_intent",
    )
    development = dataset.manifest.split("development")
    artifact_store = LocalArtifactStore(tmp_path / "current-ten-artifacts")
    exact_measurements = 0
    exact_intents = 0
    for sample in development.samples:
        run_id = f"v2-1-current-ten-{sample.case_id}"
        run = artifact_store.start_run("v2-1-current-ten", run_id)
        catalog = LocalArtifactCatalog(run, run_id=run_id)
        bundle = measure_target_v2(
            dataset.resolve_image(sample).read_bytes(),
            catalog=catalog,
            run_id=run_id,
        )
        exact_hypotheses = tuple(
            hypothesis
            for hypothesis in bundle.measurements.target_hypotheses
            if hypothesis.fill_topology == sample.topology
            and hypothesis.instance_count == sample.instance_count
            and hypothesis.hole_count == sample.hole_count
        )
        exact_measurements += bool(exact_hypotheses)

        contract = Constraint(
            constraint_id="normalized-by-builder",
            kind="contract",
            strength="hard",
            scope="global",
            value=ContractConstraintValue(
                contract_id="webgl1_static_no_texture_v1"
            ),
            source="render_contract",
            source_revision=0,
            confidence=1.0,
            verification_status="verified",
        )
        constraints = build_request_constraint_set(
            constraint_set_id=f"constraints-{sample.case_id}",
            target_sha256=bundle.measurements.target_sha256,
            request_revision=0,
            constraints=(contract,),
        )
        interpretation = _interpretation_evidence(bundle.evidence_index_ref)
        result = build_intent_variants(
            bundle.measurements,
            interpretation,
            constraints,
            _intent_context(bundle.evidence_index_ref),
        )
        variant_hypothesis_ids = {
            variant.target_hypothesis_id for variant in result.variants
        }
        assert variant_hypothesis_ids == {
            hypothesis.hypothesis_id
            for hypothesis in bundle.measurements.target_hypotheses
        }
        exact_intents += any(
            hypothesis.hypothesis_id in variant_hypothesis_ids
            for hypothesis in exact_hypotheses
        )

    assert exact_measurements == len(development.samples)
    assert exact_intents == len(development.samples)


def test_v2_1_measurement_producer_visible_validation_gate(
    tmp_path: Path,
    record_property: Callable[[str, object], None],
) -> None:
    """只读可见 validation，报告真实分母；不读取 sealed release 素材。"""
    dataset = load_v2_dataset_manifest(
        MANIFEST,
        benchmark_root=BENCHMARK_ROOT,
        gate_stage="v2_1_intent",
    )
    stage_gate = evaluate_v2_dataset_stage_gate(dataset, stage="v2_1_intent")
    assert stage_gate.ready
    assert stage_gate.required_splits == ("validation",)
    release = dataset.manifest.split("release-held-out")
    assert release.status == "not_populated"
    assert not release.samples

    validation = dataset.manifest.split("validation")
    artifact_store = LocalArtifactStore(tmp_path / "measurement-artifacts")
    outcomes: list[tuple[V2DatasetSample, tuple[TargetHypothesis, ...]]] = []
    failures: list[dict[str, str]] = []
    for sample in validation.samples:
        run_id = f"v2-1-measurement-{sample.case_id}"
        try:
            run = artifact_store.start_run("v2-1-validation", run_id)
            catalog = LocalArtifactCatalog(run, run_id=run_id)
            bundle = measure_target_v2(
                dataset.resolve_image(sample).read_bytes(),
                catalog=catalog,
                run_id=run_id,
            )
        except (OSError, ValueError) as exc:
            failures.append(
                {
                    "case_id": sample.case_id,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
            continue
        outcomes.append((sample, tuple(bundle.measurements.target_hypotheses)))

    sample_count = len(validation.samples)
    producer_successes = len(outcomes)
    instance_exact = sum(
        any(
            hypothesis.instance_count == sample.instance_count
            for hypothesis in hypotheses
        )
        for sample, hypotheses in outcomes
    )
    full_structure_exact = sum(
        any(
            hypothesis.fill_topology == sample.topology
            and hypothesis.instance_count == sample.instance_count
            and hypothesis.hole_count == sample.hole_count
            for hypothesis in hypotheses
        )
        for sample, hypotheses in outcomes
    )
    multi_instance_rows = [
        (sample, hypotheses)
        for sample, hypotheses in outcomes
        if sample.instance_count > 1
    ]
    multi_instance_detected = sum(
        any(hypothesis.instance_count > 1 for hypothesis in hypotheses)
        for _sample, hypotheses in multi_instance_rows
    )
    hole_rows = [
        (sample, hypotheses)
        for sample, hypotheses in outcomes
        if sample.hole_count > 0
    ]
    hole_recalled = sum(
        any(hypothesis.hole_count > 0 for hypothesis in hypotheses)
        for _sample, hypotheses in hole_rows
    )

    report: dict[str, object] = {
        "schema_version": "v2_1_measurement_validation_report_v1",
        "stage": stage_gate.stage,
        "dataset_version": stage_gate.dataset_version,
        "manifest_sha256": stage_gate.manifest_sha256,
        "producer_success": _fraction(producer_successes, sample_count),
        "instance_exact": _fraction(instance_exact, sample_count),
        "full_structure_exact": _fraction(full_structure_exact, sample_count),
        "multi_instance_detect_recall": _fraction(
            multi_instance_detected,
            len(multi_instance_rows),
        ),
        "hole_positive_recall": _fraction(hole_recalled, len(hole_rows)),
        "failures": failures,
    }
    for topology in ("ring", "hollow"):
        positives = sum(
            sample.topology == topology for sample, _hypotheses in outcomes
        )
        predictions = sum(
            any(hypothesis.fill_topology == topology for hypothesis in hypotheses)
            for _sample, hypotheses in outcomes
        )
        true_positives = sum(
            sample.topology == topology
            and any(
                hypothesis.fill_topology == topology
                for hypothesis in hypotheses
            )
            for sample, hypotheses in outcomes
        )
        false_positives = predictions - true_positives
        false_negatives = positives - true_positives
        report[f"{topology}_recall"] = _fraction(true_positives, positives)
        report[f"{topology}_precision"] = _fraction(
            true_positives,
            predictions,
        )
        report[f"{topology}_f1"] = _fraction(
            2 * true_positives,
            2 * true_positives + false_positives + false_negatives,
        )

    record_property(
        "v2_1_measurement_validation_report",
        json.dumps(report, ensure_ascii=False, sort_keys=True),
    )
    failure_context = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert producer_successes / sample_count >= 0.80, failure_context
    assert instance_exact == sample_count, failure_context
    assert full_structure_exact == sample_count, failure_context
    assert multi_instance_detected / len(multi_instance_rows) >= 0.90, failure_context
    assert hole_recalled / len(hole_rows) >= 0.90, failure_context
    for topology in ("ring", "hollow"):
        recall = report[f"{topology}_recall"]
        f1 = report[f"{topology}_f1"]
        assert isinstance(recall, dict)
        assert isinstance(f1, dict)
        assert float(recall["ratio"]) >= 0.90, failure_context
        assert float(f1["ratio"]) >= 0.90, failure_context
