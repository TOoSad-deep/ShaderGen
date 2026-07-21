from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from shaderforge.benchmark.v2_3_rendered_structure_gate import (
    V2_3RenderedGraphCaseOutcome,
    V2_3RenderedGraphGateReport,
    V2_3RenderedLayerPrediction,
    V2_3VerifiedRenderedCaseCapability,
    _evaluate_v2_3_rendered_structure_statistics,
    build_v2_3_rendered_threshold_policy,
    compute_v2_3_actual_replay_receipts_root,
    compute_v2_3_rendered_case_outcome_hash,
    compute_v2_3_rendered_gate_report_hash,
    compute_v2_3_rendered_split_report_hash,
    compute_v2_3_rendered_threshold_policy_hash,
    evaluate_v2_3_rendered_structure_gate,
)
from shaderforge.benchmark.v2_dataset import (
    LoadedV2Dataset,
    V2DatasetSample,
    V2DatasetStageGate,
    evaluate_v2_dataset_stage_gate,
    load_v2_dataset_manifest,
)
from shaderforge.contracts.taxonomy import REQUIRED_LAYER_ORDER
from shaderforge.store import ArtifactRefV2

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "benchmarks/png_to_shader_v2/dataset_manifest.v1.json"
CONFIG_SHA = "c" * 64
INTENT_SHA = "d" * 64
COMPILER_SHA = "e" * 64
ENVIRONMENT_SHA = "f" * 64


def _dataset_gate() -> tuple[LoadedV2Dataset, V2DatasetStageGate]:
    dataset = load_v2_dataset_manifest(
        MANIFEST,
        benchmark_root=ROOT / "benchmarks",
        gate_stage="v2_3_graph_conformance",
    )
    return dataset, evaluate_v2_dataset_stage_gate(
        dataset, stage="v2_3_graph_conformance"
    )


def _ref(identity: str, kind: str, schema_version: str) -> ArtifactRefV2:
    return ArtifactRefV2(
        artifact_id=f"artifact-{sha256(identity.encode()).hexdigest()[:24]}",
        sha256=sha256(f"bytes:{identity}".encode()).hexdigest(),
        kind=kind,
        schema_version=schema_version,
        content_type="application/json",
        size_bytes=128,
    )


def _layer_rows(sample: V2DatasetSample) -> tuple[V2_3RenderedLayerPrediction, ...]:
    return tuple(
        V2_3RenderedLayerPrediction(
            layer=layer,
            enabled=layer in sample.required_layers,
            prediction_available=True,
            visible=layer in sample.required_layers,
            diagnostic_render_ref=(
                _ref(
                    f"{sample.case_id}:layer:{layer}",
                    "diagnostic_render_png",
                    "diagnostic_render_png_v3",
                )
                if layer in sample.required_layers
                else None
            ),
        )
        for layer in REQUIRED_LAYER_ORDER
    )


def _outcome_payload(
    dataset: LoadedV2Dataset,
    gate: V2DatasetStageGate,
    sample: V2DatasetSample,
    split: str,
) -> dict[str, Any]:
    policy = build_v2_3_rendered_threshold_policy()
    attempts = 3
    diagnostic_count = attempts * (
        sample.instance_count + len(sample.required_layers)
    )
    nominal = attempts * 5 + diagnostic_count
    candidate_refs = tuple(
        _ref(
            f"{split}:{sample.case_id}:candidate:{index}",
            "candidate_record",
            "candidate_record_v3",
        )
        for index in range(attempts)
    )
    receipt_hashes = tuple(
        sha256(f"{split}:{sample.case_id}:actual-replay:{index}".encode()).hexdigest()
        for index in range(attempts)
    )
    payload: dict[str, Any] = {
        "manifest_id": gate.manifest_id,
        "dataset_version": gate.dataset_version,
        "manifest_sha256": gate.manifest_sha256,
        "taxonomy_sha256": gate.taxonomy_sha256,
        "config_sha256": CONFIG_SHA,
        "threshold_policy_hash": policy.policy_hash,
        "input_intent_outcomes_sha256": INTENT_SHA,
        "input_compiler_outcomes_sha256": COMPILER_SHA,
        "split": split,
        "case_id": sample.case_id,
        "source_image_sha256": sample.sha256,
        "success": True,
        "terminal_phase": "finalized",
        "stop_reason": "completed_with_objective_best",
        "final_state_sha256": sha256(f"state:{split}:{sample.case_id}".encode()).hexdigest(),
        "hypothesis_count": 1,
        "expected_seed_attempt_count": attempts,
        "seed_attempt_count": attempts,
        "attempt_artifact_closure_count": attempts,
        "successful_candidate_count": attempts,
        "branch_best_count": 1,
        "all_candidate_refs": candidate_refs,
        "actual_replay_receipt_hashes": receipt_hashes,
        "actual_replay_receipts_root": compute_v2_3_actual_replay_receipts_root(
            candidate_refs, receipt_hashes
        ),
        "selected_candidate_ref": candidate_refs[0],
        "selected_candidate_record_hash": "1" * 64,
        "render_plan_ref": _ref(
            f"{split}:{sample.case_id}:render-plan",
            "renderer_plan",
            "renderer_plan_v3",
        ),
        "render_plan_record_hash": "4" * 64,
        "render_progress_ref": _ref(
            f"{split}:{sample.case_id}:render-progress",
            "renderer_progress",
            "renderer_progress_v2",
        ),
        "render_progress_record_hash": "5" * 64,
        "render_repeatability_ref": _ref(
            f"{split}:{sample.case_id}:repeatability",
            "render_repeatability_evidence",
            "render_repeatability_evidence_v2",
        ),
        "render_repeatability_record_hash": "6" * 64,
        "rendered_structure_evidence_ref": _ref(
            f"{split}:{sample.case_id}:evidence",
            "rendered_structure_evidence",
            "rendered_structure_evidence_v4",
        ),
        "rendered_structure_evidence_record_hash": "2" * 64,
        "rendered_structure_verification_ref": _ref(
            f"{split}:{sample.case_id}:verification",
            "rendered_structure_verification",
            "rendered_structure_verification_v4",
        ),
        "rendered_structure_verification_record_hash": "3" * 64,
        "prediction_source": "selected_candidate_rendered_structure_verification_v4",
        "verification_status": "structure_verified",
        "measured_topology": sample.topology,
        "measured_instance_count": sample.instance_count,
        "measured_hole_count": sample.hole_count,
        "layer_predictions": _layer_rows(sample),
        "beauty_capture_count": attempts * 5,
        "diagnostic_render_count": diagnostic_count,
        "nominal_render_request_count": nominal,
        "logical_render_request_attempt_count": nominal,
        "physical_render_call_count": nominal,
        "render_retry_count": 0,
        "transient_render_retry_count": 0,
        "unknown_render_retry_count": 0,
        "unknown_render_result_count": 0,
        "render_budget_used": nominal,
        "render_budget_reserved": 0,
        "renderer_environment_hash": ENVIRONMENT_SHA,
        "persisted_renderer_environment_hash": ENVIRONMENT_SHA,
        "failure_codes": (),
        "record_hash": "0" * 64,
    }
    payload["record_hash"] = compute_v2_3_rendered_case_outcome_hash(payload)
    return payload


def _outcome(
    dataset: LoadedV2Dataset,
    gate: V2DatasetStageGate,
    sample: V2DatasetSample,
    split: str,
) -> V2_3RenderedGraphCaseOutcome:
    return V2_3RenderedGraphCaseOutcome.model_validate(
        _outcome_payload(dataset, gate, sample, split), strict=True
    )


def _perfect_outcomes(
    dataset: LoadedV2Dataset, gate: V2DatasetStageGate
) -> tuple[V2_3RenderedGraphCaseOutcome, ...]:
    development = tuple(
        sample
        for sample in dataset.manifest.split("development").samples
        if sample.dataset_role == "regression"
        and sample.source_suite_id == "png_to_shader_v1_m0"
    )
    return tuple(
        _outcome(dataset, gate, sample, split)
        for split, samples in (
            ("development", development),
            ("validation", dataset.manifest.split("validation").samples),
        )
        for sample in samples
    )


def _failed_before_renderer(
    outcome: V2_3RenderedGraphCaseOutcome,
) -> V2_3RenderedGraphCaseOutcome:
    unavailable_rows = tuple(
        V2_3RenderedLayerPrediction(
            layer=layer,
            enabled=False,
            prediction_available=False,
            visible=None,
            diagnostic_render_ref=None,
        )
        for layer in REQUIRED_LAYER_ORDER
    )
    return _rebuild(
        outcome,
        success=False,
        terminal_phase="failed",
        stop_reason="failed_before_renderer",
        final_state_sha256=None,
        seed_attempt_count=0,
        attempt_artifact_closure_count=0,
        successful_candidate_count=0,
        branch_best_count=0,
        all_candidate_refs=(),
        actual_replay_receipt_hashes=(),
        actual_replay_receipts_root=None,
        selected_candidate_ref=None,
        selected_candidate_record_hash=None,
        render_plan_ref=None,
        render_plan_record_hash=None,
        render_progress_ref=None,
        render_progress_record_hash=None,
        render_repeatability_ref=None,
        render_repeatability_record_hash=None,
        rendered_structure_evidence_ref=None,
        rendered_structure_evidence_record_hash=None,
        rendered_structure_verification_ref=None,
        rendered_structure_verification_record_hash=None,
        prediction_source=None,
        verification_status=None,
        measured_topology=None,
        measured_instance_count=None,
        measured_hole_count=None,
        layer_predictions=unavailable_rows,
        beauty_capture_count=0,
        diagnostic_render_count=0,
        nominal_render_request_count=0,
        logical_render_request_attempt_count=0,
        physical_render_call_count=0,
        render_retry_count=0,
        transient_render_retry_count=0,
        unknown_render_retry_count=0,
        unknown_render_result_count=0,
        render_budget_used=0,
        render_budget_reserved=0,
        renderer_environment_hash=None,
        persisted_renderer_environment_hash=None,
        failure_codes=("failed_before_renderer",),
    )


def _rebuild(
    outcome: V2_3RenderedGraphCaseOutcome, **updates: object
) -> V2_3RenderedGraphCaseOutcome:
    payload = outcome.model_dump(mode="python")
    payload.update(
        {
            "all_candidate_refs": outcome.all_candidate_refs,
            "selected_candidate_ref": outcome.selected_candidate_ref,
            "render_plan_ref": outcome.render_plan_ref,
            "render_progress_ref": outcome.render_progress_ref,
            "render_repeatability_ref": outcome.render_repeatability_ref,
            "rendered_structure_evidence_ref": outcome.rendered_structure_evidence_ref,
            "rendered_structure_verification_ref": (
                outcome.rendered_structure_verification_ref
            ),
            "layer_predictions": outcome.layer_predictions,
        }
    )
    payload.update(updates)
    payload["record_hash"] = compute_v2_3_rendered_case_outcome_hash(payload)
    return V2_3RenderedGraphCaseOutcome.model_validate(payload)


def _evaluate(
    dataset: LoadedV2Dataset,
    gate: V2DatasetStageGate,
    outcomes: tuple[V2_3RenderedGraphCaseOutcome, ...],
) -> V2_3RenderedGraphGateReport:
    return _evaluate_v2_3_rendered_structure_statistics(
        dataset,
        gate,
        outcomes,
        config_sha256=CONFIG_SHA,
        input_intent_outcomes_sha256=INTENT_SHA,
        input_compiler_outcomes_sha256=COMPILER_SHA,
    )


def test_perfect_actual_render_gate_reports_frozen_metrics_and_unavailable_class() -> None:
    dataset, gate = _dataset_gate()
    outcomes = _perfect_outcomes(dataset, gate)
    report = _evaluate(dataset, gate, outcomes)

    assert report.ready
    assert report.development.cases_passed.numerator == 10
    assert report.validation.cases_passed.numerator == 41
    assert report.validation.instance_count_exact.value == 1.0
    assert {
        item.class_id: item.positive_denominator
        for item in report.validation.critical_class_metrics
    } == {
        "multi_instance": 11,
        "ring": 20,
        "hollow": 10,
        "required_highlight": 16,
        "required_rim": 26,
        "required_outline": 36,
    }
    hollow = next(
        item
        for item in report.development.critical_class_metrics
        if item.class_id == "hollow"
    )
    assert not hollow.metric_available
    assert hollow.recall is None and hollow.f1 is None
    assert report.development.macro_recall.class_count == 5
    assert report.validation.macro_recall.class_count == 6
    assert all(
        item.f1 is not None
        and item.f1.ci95.requested_replicates == 20_000
        and item.f1.ci95.accepted_replicates == 20_000
        for item in report.validation.critical_class_metrics
    )
    assert report.validation.macro_recall.ci95.accepted_replicates == 20_000
    assert report.validation.macro_f1.ci95.accepted_replicates == 20_000
    assert (
        report.development.macro_f1.ci95.accepted_replicates
        + report.development.macro_f1.ci95.undefined_replicates
        == 20_000
    )
    assert report.development.macro_f1.ci95.undefined_replicates > 0
    assert all(
        metric.true_positive == metric.positive_denominator
        and metric.false_positive == 0
        and metric.false_negative == 0
        and metric.true_negative == metric.negative_denominator
        for metric in report.validation.critical_class_metrics
    )
    assert _evaluate(dataset, gate, outcomes) == report
    assert report.record_hash == compute_v2_3_rendered_gate_report_hash(report)
    assert report.development.record_hash == (
        compute_v2_3_rendered_split_report_hash(report.development)
    )


def test_formal_gate_rejects_plain_outcomes_and_untrusted_capability_construction() -> None:
    dataset, gate = _dataset_gate()
    outcomes = _perfect_outcomes(dataset, gate)

    with pytest.raises(TypeError, match="capability"):
        evaluate_v2_3_rendered_structure_gate(
            dataset,
            gate,
            outcomes,  # type: ignore[arg-type]
            config_sha256=CONFIG_SHA,
            input_intent_outcomes_sha256=INTENT_SHA,
            input_compiler_outcomes_sha256=COMPILER_SHA,
        )
    with pytest.raises(TypeError, match="strict collector"):
        V2_3VerifiedRenderedCaseCapability(outcomes[0], _token=object())


def test_report_hash_and_standard_blockers_reject_semantic_tamper() -> None:
    dataset, gate = _dataset_gate()
    outcomes = _perfect_outcomes(dataset, gate)
    perfect = _evaluate(dataset, gate, outcomes)

    split_payload = perfect.validation.model_dump(mode="python")
    split_payload["ready"] = False
    split_payload["blockers"] = ("tampered",)
    split_payload["record_hash"] = compute_v2_3_rendered_split_report_hash(
        split_payload
    )
    with pytest.raises(ValueError, match="标准重算"):
        type(perfect.validation).model_validate(split_payload, strict=True)

    failed_outcomes = list(outcomes)
    validation_index = next(
        index
        for index, outcome in enumerate(failed_outcomes)
        if outcome.split == "validation"
    )
    failed_outcomes[validation_index] = _failed_before_renderer(
        failed_outcomes[validation_index]
    )
    failed_validation = _evaluate(
        dataset, gate, tuple(failed_outcomes)
    ).validation
    assert not failed_validation.ready

    gate_payload = perfect.model_dump(mode="python")
    gate_payload["validation"] = failed_validation
    gate_payload["record_hash"] = compute_v2_3_rendered_gate_report_hash(gate_payload)
    with pytest.raises(ValueError, match="标准重算"):
        V2_3RenderedGraphGateReport.model_validate(gate_payload, strict=True)

    gate_payload = perfect.model_dump(mode="python")
    gate_payload["outcomes_sha256"] = "9" * 64
    with pytest.raises(ValueError, match="record hash"):
        V2_3RenderedGraphGateReport.model_validate(gate_payload, strict=True)

    ci = perfect.development.macro_f1.ci95
    ci_payload = ci.model_dump(mode="python")
    ci_payload["accepted_replicates"] = ci.accepted_replicates - 1
    with pytest.raises(ValueError, match=r"accepted \+ undefined"):
        type(ci).model_validate(ci_payload, strict=True)


def test_failure_stays_in_every_denominator_and_no_unsupported_success_exists() -> None:
    dataset, gate = _dataset_gate()
    outcomes = list(_perfect_outcomes(dataset, gate))
    first = outcomes[0]
    outcomes[0] = _rebuild(
        first,
        success=False,
        seed_attempt_count=2,
        failure_codes=("seed_attempt_count_mismatch",),
    )

    report = _evaluate(dataset, gate, tuple(outcomes))

    assert not report.ready
    assert report.development.cases_passed.model_dump() ["numerator"] == 9
    assert report.development.cases_passed.denominator == 10
    assert report.development.instance_count_exact.denominator == 10
    assert "development_not_ready" in report.blockers
    assert not hasattr(report, "expected_unsupported_no_candidate_cases")


def test_false_positive_is_reported_and_threshold_boundary_is_inclusive() -> None:
    dataset, gate = _dataset_gate()
    outcomes = list(_perfect_outcomes(dataset, gate))
    validation_start = next(
        index
        for index, outcome in enumerate(outcomes)
        if outcome.split == "validation"
        and not next(
            item for item in outcome.layer_predictions if item.layer == "outline"
        ).enabled
    )
    target = outcomes[validation_start]
    rows = list(target.layer_predictions)
    outline_index = REQUIRED_LAYER_ORDER.index("outline")
    assert not rows[outline_index].enabled
    rows[outline_index] = V2_3RenderedLayerPrediction(
        layer="outline",
        enabled=True,
        prediction_available=True,
        visible=True,
        diagnostic_render_ref=_ref("unexpected-outline", "diagnostic_render_png", "v2"),
    )
    outcomes[validation_start] = _rebuild(
        target,
        layer_predictions=tuple(rows),
        diagnostic_render_count=target.diagnostic_render_count + 1,
        nominal_render_request_count=target.nominal_render_request_count + 1,
        logical_render_request_attempt_count=(
            target.logical_render_request_attempt_count + 1
        ),
        physical_render_call_count=target.physical_render_call_count + 1,
        render_budget_used=target.render_budget_used + 1,
    )
    report = _evaluate(dataset, gate, tuple(outcomes))
    outline = next(
        item
        for item in report.validation.critical_class_metrics
        if item.class_id == "required_outline"
    )
    assert outline.false_positive == 1

    outcomes = list(_perfect_outcomes(dataset, gate))
    hollow_indices = [
        index
        for index, outcome in enumerate(outcomes)
        if outcome.split == "validation" and outcome.measured_topology == "hollow"
    ]
    outcomes[hollow_indices[0]] = _rebuild(
        outcomes[hollow_indices[0]], measured_topology="solid"
    )
    boundary = _evaluate(dataset, gate, tuple(outcomes))
    hollow = next(
        item
        for item in boundary.validation.critical_class_metrics
        if item.class_id == "hollow"
    )
    assert hollow.recall is not None and hollow.recall.value == 0.9
    assert not any("validation_recall_below_90_percent:hollow" in item for item in boundary.validation.blockers)

    outcomes[hollow_indices[1]] = _rebuild(
        outcomes[hollow_indices[1]], measured_topology="solid"
    )
    below = _evaluate(dataset, gate, tuple(outcomes))
    assert any(
        "validation_recall_below_90_percent:hollow" in item
        for item in below.validation.blockers
    )


def test_development_exact_vector_covers_all_ten_taxonomy_rows() -> None:
    dataset, gate = _dataset_gate()
    outcomes = list(_perfect_outcomes(dataset, gate))
    target = outcomes[0]
    rows = list(target.layer_predictions)
    base_fill_index = REQUIRED_LAYER_ORDER.index("base_fill")
    assert rows[base_fill_index].visible is True
    rows[base_fill_index] = rows[base_fill_index].model_copy(
        update={"visible": False}
    )
    outcomes[0] = _rebuild(target, layer_predictions=tuple(rows))

    report = _evaluate(dataset, gate, tuple(outcomes))
    assert report.development.structure_label_vector_exact.numerator == 9
    assert report.development.structure_label_vector_exact.denominator == 10
    assert "development_structure_vector_exact:9/10" in report.development.blockers


def test_outcome_requires_full_explicit_layer_vector_and_actual_renderer() -> None:
    dataset, gate = _dataset_gate()
    sample = dataset.manifest.split("validation").samples[0]
    payload = _outcome_payload(dataset, gate, sample, "validation")
    payload["layer_predictions"] = payload["layer_predictions"][:-1]
    payload["record_hash"] = compute_v2_3_rendered_case_outcome_hash(payload)
    with pytest.raises(ValueError, match="10 items|taxonomy"):
        V2_3RenderedGraphCaseOutcome.model_validate(payload, strict=True)

    payload = _outcome_payload(dataset, gate, sample, "validation")
    payload["renderer_backend"] = "deterministic_reference_png_fixture_not_chromium"
    payload["record_hash"] = compute_v2_3_rendered_case_outcome_hash(payload)
    with pytest.raises(ValueError):
        V2_3RenderedGraphCaseOutcome.model_validate(payload, strict=True)

    payload = _outcome_payload(dataset, gate, sample, "validation")
    payload["expected_topology"] = sample.topology
    payload["record_hash"] = compute_v2_3_rendered_case_outcome_hash(payload)
    with pytest.raises(ValueError, match="Extra inputs"):
        V2_3RenderedGraphCaseOutcome.model_validate(payload, strict=True)


def test_failure_before_renderer_is_explicitly_unavailable_and_needs_no_environment() -> None:
    dataset, gate = _dataset_gate()
    outcomes = list(_perfect_outcomes(dataset, gate))
    failed = _failed_before_renderer(outcomes[0])

    assert failed.renderer_environment_hash is None
    assert all(
        not row.prediction_available and row.visible is None
        for row in failed.layer_predictions
    )
    outcomes[0] = failed
    report = _evaluate(dataset, gate, tuple(outcomes))
    assert not report.ready
    assert report.renderer_environment_hashes == (ENVIRONMENT_SHA,)
    assert report.development.cases_passed.numerator == 9

    payload = failed.model_dump(mode="python")
    rows = list(failed.layer_predictions)
    rows[0] = V2_3RenderedLayerPrediction(
        layer=rows[0].layer,
        enabled=False,
        prediction_available=True,
        visible=False,
    )
    payload["layer_predictions"] = tuple(rows)
    payload["record_hash"] = compute_v2_3_rendered_case_outcome_hash(payload)
    with pytest.raises(ValueError, match="unavailable"):
        V2_3RenderedGraphCaseOutcome.model_validate(payload, strict=True)


def test_unknown_result_retry_is_counted_and_cannot_be_a_success() -> None:
    dataset, gate = _dataset_gate()
    outcome = _perfect_outcomes(dataset, gate)[0]
    failed = _rebuild(
        outcome,
        success=False,
        physical_render_call_count=outcome.physical_render_call_count + 1,
        render_retry_count=1,
        unknown_render_retry_count=1,
        unknown_render_result_count=1,
        render_budget_used=outcome.render_budget_used + 1,
        failure_codes=("renderer_result_unknown",),
    )
    assert failed.physical_render_call_count == (
        failed.logical_render_request_attempt_count + failed.render_retry_count
    )

    with pytest.raises(ValueError, match=r"transient \+ unknown"):
        _rebuild(failed, render_retry_count=0)


def test_success_requires_complete_typed_render_closure() -> None:
    dataset, gate = _dataset_gate()
    outcome = _perfect_outcomes(dataset, gate)[0]
    with pytest.raises(ValueError, match="success"):
        _rebuild(
            outcome,
            render_plan_ref=None,
            render_plan_record_hash=None,
        )


def test_gate_rejects_release_identity_tamper_and_threshold_downgrade() -> None:
    dataset, gate = _dataset_gate()
    outcomes = list(_perfect_outcomes(dataset, gate))
    outcomes[0] = _rebuild(
        outcomes[0], split="release-held-out", case_id="sealed-case"
    )
    with pytest.raises(ValueError, match="release-held-out"):
        _evaluate(dataset, gate, tuple(outcomes))

    outcomes = list(_perfect_outcomes(dataset, gate))
    outcomes[0] = _rebuild(outcomes[0], source_image_sha256="9" * 64)
    with pytest.raises(ValueError, match="source image identity"):
        _evaluate(dataset, gate, tuple(outcomes))

    policy = build_v2_3_rendered_threshold_policy()
    payload = policy.model_dump(mode="python")
    payload["validation_class_recall_minimum"] = 0.8
    payload["policy_hash"] = compute_v2_3_rendered_threshold_policy_hash(payload)
    with pytest.raises(ValueError, match="不得降级"):
        type(policy).model_validate(payload, strict=True)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("renderer_execution_class", "reference_fixture"),
        ("renderer_backend", "deterministic_reference_png_fixture_not_chromium"),
        ("release_held_out_accessed", True),
        ("production_admission_enabled", True),
        ("model_calls", 1),
    ),
)
def test_outcome_rejects_non_actual_release_admission_and_model_modes(
    field: str, value: object
) -> None:
    dataset, gate = _dataset_gate()
    sample = dataset.manifest.split("validation").samples[0]
    payload = _outcome_payload(dataset, gate, sample, "validation")
    payload[field] = value
    payload["record_hash"] = compute_v2_3_rendered_case_outcome_hash(payload)
    with pytest.raises(ValueError):
        V2_3RenderedGraphCaseOutcome.model_validate(payload, strict=True)


def test_expected_unsupported_cannot_be_relabelled_as_success() -> None:
    dataset, gate = _dataset_gate()
    outcome = _perfect_outcomes(dataset, gate)[0]
    with pytest.raises(ValueError, match="success"):
        _rebuild(outcome, stop_reason="expected_unsupported")
    _evaluate_v2_3_rendered_structure_statistics,
    compute_v2_3_actual_replay_receipts_root,
