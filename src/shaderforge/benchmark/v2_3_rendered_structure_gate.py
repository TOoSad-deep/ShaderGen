"""V2.3 actual-render structure conformance 的 verified-capability 门禁。."""
# ruff: noqa: D107, D415

from __future__ import annotations

import math
import random
from hashlib import sha256
from typing import Literal

from pydantic import Field, model_validator

from shaderforge.benchmark.v2_dataset import (
    CRITICAL_CLASS_IDS,
    LoadedV2Dataset,
    V2DatasetSample,
    V2DatasetStageGate,
)
from shaderforge.contracts import FrozenModel, NonEmptyString, Sha256Hex
from shaderforge.contracts.canonical import canonical_sha256
from shaderforge.contracts.taxonomy import REQUIRED_LAYER_ORDER, RequiredLayerTaxon
from shaderforge.store import ArtifactRefV2

V2_3_RENDERED_CASE_OUTCOME_SCHEMA_VERSION: Literal[
    "v2_3_rendered_graph_case_outcome_v4"
] = "v2_3_rendered_graph_case_outcome_v4"
V2_3_RENDERED_SPLIT_REPORT_SCHEMA_VERSION: Literal[
    "v2_3_rendered_graph_split_report_v5"
] = "v2_3_rendered_graph_split_report_v5"
V2_3_RENDERED_GATE_REPORT_SCHEMA_VERSION: Literal[
    "v2_3_rendered_graph_gate_report_v5"
] = "v2_3_rendered_graph_gate_report_v5"
V2_3_RENDERED_THRESHOLD_POLICY_VERSION: Literal[
    "v2_3_rendered_structure_threshold_policy_v2"
] = "v2_3_rendered_structure_threshold_policy_v2"

V2_3_RENDERED_DEVELOPMENT_COUNT = 10
V2_3_RENDERED_VALIDATION_COUNT = 41
V2_3_RENDERED_SEEDS_PER_HYPOTHESIS = 3
V2_3_RENDERED_BEAUTY_CAPTURES_PER_ATTEMPT = 5
V2_3_RENDERED_BOOTSTRAP_REPLICATES = 20_000

CriticalClassId = Literal[
    "multi_instance",
    "ring",
    "hollow",
    "required_highlight",
    "required_rim",
    "required_outline",
]
_CRITICAL_CLASSES: tuple[CriticalClassId, ...] = (
    "multi_instance",
    "ring",
    "hollow",
    "required_highlight",
    "required_rim",
    "required_outline",
)
if _CRITICAL_CLASSES != CRITICAL_CLASS_IDS:  # pragma: no cover
    raise RuntimeError("Rendered structure gate critical classes 与 dataset 漂移。")


class V2_3RenderedValidationDenominators(FrozenModel):
    """V2.0 visible validation 已冻结的精确正例分母。."""

    multi_instance: Literal[11] = 11
    ring: Literal[20] = 20
    hollow: Literal[10] = 10
    required_highlight: Literal[16] = 16
    required_rim: Literal[26] = 26
    required_outline: Literal[36] = 36

    def as_dict(self) -> dict[str, int]:
        """返回冻结类名到最低分母的映射。."""
        return {name: int(getattr(self, name)) for name in _CRITICAL_CLASSES}


class V2_3RenderedThresholdPolicy(FrozenModel):
    """纳入 config identity 的统计口径与冻结阈值。."""

    schema_version: Literal["v2_3_rendered_structure_threshold_policy_v2"] = (
        V2_3_RENDERED_THRESHOLD_POLICY_VERSION
    )
    development_case_count: Literal[10] = 10
    validation_case_count: Literal[41] = 41
    seeds_per_hypothesis: Literal[3] = 3
    beauty_captures_per_attempt: Literal[5] = 5
    development_case_success_minimum: float = Field(default=1.0, ge=0.0, le=1.0)
    development_instance_exact_minimum: float = Field(default=1.0, ge=0.0, le=1.0)
    development_structure_vector_exact_minimum: float = Field(
        default=1.0, ge=0.0, le=1.0
    )
    validation_case_success_minimum: float = Field(default=1.0, ge=0.0, le=1.0)
    validation_instance_exact_minimum: float = Field(default=1.0, ge=0.0, le=1.0)
    validation_class_recall_minimum: float = Field(default=0.9, ge=0.0, le=1.0)
    validation_class_f1_minimum: float = Field(default=0.9, ge=0.0, le=1.0)
    validation_macro_recall_minimum: float = Field(default=0.9, ge=0.0, le=1.0)
    validation_macro_f1_minimum: float = Field(default=0.9, ge=0.0, le=1.0)
    validation_positive_denominators: V2_3RenderedValidationDenominators = (
        V2_3RenderedValidationDenominators()
    )
    recall_ci_method: Literal["wilson_score_two_sided_95_v1"] = (
        "wilson_score_two_sided_95_v1"
    )
    bootstrap_method: Literal[
        "paired_case_fixed_draw_available_percentile_95_mt19937_v2"
    ] = (
        "paired_case_fixed_draw_available_percentile_95_mt19937_v2"
    )
    bootstrap_replicates: Literal[20000] = 20000
    policy_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_hash(self) -> V2_3RenderedThresholdPolicy:
        thresholds = (
            self.development_case_success_minimum,
            self.development_instance_exact_minimum,
            self.development_structure_vector_exact_minimum,
            self.validation_case_success_minimum,
            self.validation_instance_exact_minimum,
            self.validation_class_recall_minimum,
            self.validation_class_f1_minimum,
            self.validation_macro_recall_minimum,
            self.validation_macro_f1_minimum,
        )
        if thresholds != (1.0, 1.0, 1.0, 1.0, 1.0, 0.9, 0.9, 0.9, 0.9):
            raise ValueError("Rendered structure threshold policy 不得降级。")
        if self.policy_hash != compute_v2_3_rendered_threshold_policy_hash(self):
            raise ValueError("Rendered structure threshold policy hash 不一致。")
        return self


class V2_3WilsonInterval95(FrozenModel):
    """二项比例的双侧 95% Wilson score interval。."""

    method: Literal["wilson_score_two_sided_95_v1"] = (
        "wilson_score_two_sided_95_v1"
    )
    lower: float = Field(ge=0.0, le=1.0)
    upper: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_order(self) -> V2_3WilsonInterval95:
        if self.lower > self.upper:
            raise ValueError("Wilson CI 必须满足 lower <= upper。")
        return self


class V2_3BootstrapInterval95(FrozenModel):
    """固定 draw 数、显式报告 undefined draw 的双侧 percentile interval。."""

    method: Literal[
        "paired_case_fixed_draw_available_percentile_95_mt19937_v2"
    ] = (
        "paired_case_fixed_draw_available_percentile_95_mt19937_v2"
    )
    requested_replicates: Literal[20000] = 20000
    accepted_replicates: int = Field(ge=1, le=20000)
    undefined_replicates: int = Field(ge=0, le=19999)
    lower: float = Field(ge=0.0, le=1.0)
    upper: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_order(self) -> V2_3BootstrapInterval95:
        if self.accepted_replicates + self.undefined_replicates != (
            self.requested_replicates
        ):
            raise ValueError("Bootstrap accepted + undefined 必须等于固定 draw 数。")
        if self.lower > self.upper:
            raise ValueError("Bootstrap CI 必须满足 lower <= upper。")
        return self


class V2_3CountMetric(FrozenModel):
    """允许零分母的普通计数；不把 0/0 解释为通过。."""

    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_count(self) -> V2_3CountMetric:
        if self.numerator > self.denominator:
            raise ValueError("count numerator 不得超过 denominator。")
        return self

    @property
    def value(self) -> float | None:
        """返回比例；零分母显式返回 None。."""
        return None if self.denominator == 0 else self.numerator / self.denominator


class V2_3ProportionMetric(FrozenModel):
    """带 Wilson CI 的普通二项比例。."""

    numerator: int = Field(ge=0)
    denominator: int = Field(ge=1)
    value: float = Field(ge=0.0, le=1.0)
    ci95: V2_3WilsonInterval95

    @model_validator(mode="after")
    def _validate_ratio(self) -> V2_3ProportionMetric:
        if self.numerator > self.denominator:
            raise ValueError("proportion numerator 不得超过 denominator。")
        expected = self.numerator / self.denominator
        if not math.isclose(self.value, expected, rel_tol=0.0, abs_tol=1e-15):
            raise ValueError("proportion value 与计数不一致。")
        if self.ci95 != _wilson_interval(self.numerator, self.denominator):
            raise ValueError("proportion Wilson CI 与计数不一致。")
        return self


class V2_3F1Metric(FrozenModel):
    """带 paired bootstrap CI 的 F1 比例。."""

    numerator: int = Field(ge=0)
    denominator: int = Field(ge=1)
    value: float = Field(ge=0.0, le=1.0)
    ci95: V2_3BootstrapInterval95

    @model_validator(mode="after")
    def _validate_ratio(self) -> V2_3F1Metric:
        if self.numerator > self.denominator:
            raise ValueError("F1 numerator 不得超过 denominator。")
        if not math.isclose(
            self.value,
            self.numerator / self.denominator,
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise ValueError("F1 value 与计数不一致。")
        return self


class V2_3RenderedClassMetric(FrozenModel):
    """一个关键类的完整 confusion matrix、recall 与 F1。."""

    class_id: CriticalClassId
    true_positive: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    true_negative: int = Field(ge=0)
    positive_denominator: int = Field(ge=0)
    negative_denominator: int = Field(ge=0)
    metric_available: bool
    recall: V2_3ProportionMetric | None
    f1: V2_3F1Metric | None

    @model_validator(mode="after")
    def _validate_confusion(self) -> V2_3RenderedClassMetric:
        if self.positive_denominator != self.true_positive + self.false_negative:
            raise ValueError("positive denominator 必须等于 TP+FN。")
        if self.negative_denominator != self.true_negative + self.false_positive:
            raise ValueError("negative denominator 必须等于 TN+FP。")
        available = self.positive_denominator > 0
        if self.metric_available != available:
            raise ValueError("metric_available 必须由正例分母决定。")
        if not available:
            if self.recall is not None or self.f1 is not None:
                raise ValueError("零正例类的 recall/F1 必须显式 unavailable。")
            return self
        if self.recall is None or self.f1 is None:
            raise ValueError("有正例类必须同时报告 recall 与 F1。")
        if (
            self.recall.numerator != self.true_positive
            or self.recall.denominator != self.positive_denominator
        ):
            raise ValueError("recall 必须绑定 TP/(TP+FN)。")
        expected_f1_numerator = 2 * self.true_positive
        expected_f1_denominator = (
            expected_f1_numerator + self.false_positive + self.false_negative
        )
        if (
            self.f1.numerator != expected_f1_numerator
            or self.f1.denominator != expected_f1_denominator
        ):
            raise ValueError("F1 必须绑定 2TP/(2TP+FP+FN)。")
        return self


class V2_3MacroMetric(FrozenModel):
    """只聚合当前 split 有正例类的 macro 指标。."""

    included_class_ids: tuple[CriticalClassId, ...] = Field(min_length=1)
    class_count: int = Field(ge=1)
    value: float = Field(ge=0.0, le=1.0)
    ci95: V2_3BootstrapInterval95

    @model_validator(mode="after")
    def _validate_classes(self) -> V2_3MacroMetric:
        if self.class_count != len(self.included_class_ids):
            raise ValueError("macro class_count 与 included_class_ids 不一致。")
        if tuple(name for name in _CRITICAL_CLASSES if name in self.included_class_ids) != (
            self.included_class_ids
        ):
            raise ValueError("macro class ids 必须按 taxonomy 顺序且唯一。")
        return self


class V2_3RenderedLayerPrediction(FrozenModel):
    """Verification 输出的一个显式 taxonomy row。.

    成功 verification 必须对 10 个 taxon 都给出 bool；失败 case 则必须
    显式写成 unavailable，不得把“没有预测”伪装成 negative。
    """

    layer: RequiredLayerTaxon
    enabled: bool
    prediction_available: bool
    visible: bool | None
    diagnostic_render_ref: ArtifactRefV2 | None = None

    @model_validator(mode="after")
    def _validate_row(self) -> V2_3RenderedLayerPrediction:
        if self.prediction_available != (self.visible is not None):
            raise ValueError("prediction_available 必须与 visible 是否可用一致。")
        if self.visible is True and not self.enabled:
            raise ValueError("disabled layer 不得报告 visible。")
        if self.enabled != (self.diagnostic_render_ref is not None):
            raise ValueError("enabled layer 必须且只能绑定 diagnostic render ref。")
        return self


class V2_3RenderedGraphCaseOutcome(FrozenModel):
    """只保存实际 render verification 预测；不保存 Manifest truth。."""

    schema_version: Literal["v2_3_rendered_graph_case_outcome_v4"] = (
        V2_3_RENDERED_CASE_OUTCOME_SCHEMA_VERSION
    )
    gate_stage: Literal["v2_3_rendered_structure_conformance"] = (
        "v2_3_rendered_structure_conformance"
    )
    manifest_id: NonEmptyString
    dataset_version: NonEmptyString
    manifest_sha256: Sha256Hex
    taxonomy_sha256: Sha256Hex
    config_sha256: Sha256Hex
    threshold_policy_hash: Sha256Hex
    input_intent_outcomes_sha256: Sha256Hex
    input_compiler_outcomes_sha256: Sha256Hex
    split: Literal["development", "validation", "release-held-out"]
    case_id: NonEmptyString
    source_image_sha256: Sha256Hex
    success: bool
    terminal_phase: NonEmptyString | None
    stop_reason: NonEmptyString | None
    final_state_sha256: Sha256Hex | None
    hypothesis_count: int = Field(ge=1)
    expected_seed_attempt_count: int = Field(ge=3)
    seed_attempt_count: int = Field(ge=0)
    attempt_artifact_closure_count: int = Field(ge=0)
    successful_candidate_count: int = Field(ge=0)
    branch_best_count: int = Field(ge=0)
    all_candidate_refs: tuple[ArtifactRefV2, ...]
    actual_replay_receipt_hashes: tuple[Sha256Hex, ...]
    actual_replay_receipts_root: Sha256Hex | None = None
    selected_candidate_ref: ArtifactRefV2 | None = None
    selected_candidate_record_hash: Sha256Hex | None = None
    render_plan_ref: ArtifactRefV2 | None = None
    render_plan_record_hash: Sha256Hex | None = None
    render_progress_ref: ArtifactRefV2 | None = None
    render_progress_record_hash: Sha256Hex | None = None
    render_repeatability_ref: ArtifactRefV2 | None = None
    render_repeatability_record_hash: Sha256Hex | None = None
    rendered_structure_evidence_ref: ArtifactRefV2 | None = None
    rendered_structure_evidence_record_hash: Sha256Hex | None = None
    rendered_structure_verification_ref: ArtifactRefV2 | None = None
    rendered_structure_verification_record_hash: Sha256Hex | None = None
    prediction_source: Literal[
        "selected_candidate_rendered_structure_verification_v4"
    ] | None = None
    verification_status: Literal["structure_verified", "rejected"] | None = None
    measured_topology: Literal["solid", "hollow", "ring", "open", "unknown"] | None
    measured_instance_count: int | None = Field(default=None, ge=0)
    measured_hole_count: int | None = Field(default=None, ge=0)
    layer_predictions: tuple[V2_3RenderedLayerPrediction, ...] = Field(
        min_length=10, max_length=10
    )
    beauty_capture_count: int = Field(ge=0)
    diagnostic_render_count: int = Field(ge=0)
    nominal_render_request_count: int = Field(ge=0)
    logical_render_request_attempt_count: int = Field(ge=0)
    physical_render_call_count: int = Field(ge=0)
    render_retry_count: int = Field(ge=0)
    transient_render_retry_count: int = Field(ge=0)
    unknown_render_retry_count: int = Field(ge=0)
    unknown_render_result_count: int = Field(ge=0)
    render_budget_used: int = Field(ge=0)
    render_budget_reserved: int = Field(ge=0)
    renderer_execution_class: Literal["actual_glsl_execution"] = (
        "actual_glsl_execution"
    )
    renderer_backend: Literal["chromium_webgl1_actual_v1"] = (
        "chromium_webgl1_actual_v1"
    )
    renderer_environment_hash: Sha256Hex | None = None
    persisted_renderer_environment_hash: Sha256Hex | None = None
    release_held_out_accessed: Literal[False] = False
    production_admission_enabled: Literal[False] = False
    model_calls: Literal[0] = 0
    failure_codes: tuple[NonEmptyString, ...]
    record_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_outcome(self) -> V2_3RenderedGraphCaseOutcome:
        if self.expected_seed_attempt_count != (
            self.hypothesis_count * V2_3_RENDERED_SEEDS_PER_HYPOTHESIS
        ):
            raise ValueError("expected attempts 必须等于 hypothesis_count×3。")
        if self.branch_best_count > self.hypothesis_count:
            raise ValueError("branch best 不得超过 hypothesis count。")
        if any(
            value > self.expected_seed_attempt_count
            for value in (
                self.seed_attempt_count,
                self.attempt_artifact_closure_count,
                self.successful_candidate_count,
            )
        ):
            raise ValueError("Attempt/Candidate 计数不得超过冻结分母。")
        if self.failure_codes != tuple(sorted(set(self.failure_codes))):
            raise ValueError("failure_codes 必须唯一排序。")
        if tuple(item.layer for item in self.layer_predictions) != REQUIRED_LAYER_ORDER:
            raise ValueError("layer predictions 必须覆盖完整 taxonomy 并按冻结顺序。")
        if any(
            ref.kind != "candidate_record"
            or ref.schema_version != "candidate_record_v3"
            or ref.content_type != "application/json"
            for ref in self.all_candidate_refs
        ):
            raise ValueError("all_candidate_refs 只接受 Candidate v3 JSON refs。")
        if len({ref.artifact_id for ref in self.all_candidate_refs}) != len(
            self.all_candidate_refs
        ):
            raise ValueError("all_candidate_refs 不得重复。")
        if len(set(self.actual_replay_receipt_hashes)) != len(
            self.actual_replay_receipt_hashes
        ):
            raise ValueError("actual replay receipt hashes 不得重复。")
        if (self.actual_replay_receipts_root is None) != (
            not self.actual_replay_receipt_hashes
        ):
            raise ValueError("actual replay receipt root 与 hashes 必须同时存在或缺席。")
        if self.actual_replay_receipts_root is not None and (
            self.actual_replay_receipts_root
            != compute_v2_3_actual_replay_receipts_root(
                self.all_candidate_refs, self.actual_replay_receipt_hashes
            )
        ):
            raise ValueError("actual replay receipt root 与 Candidate/hash bindings 不一致。")
        closure_pairs = (
            (self.selected_candidate_ref, self.selected_candidate_record_hash),
            (self.render_plan_ref, self.render_plan_record_hash),
            (self.render_progress_ref, self.render_progress_record_hash),
            (self.render_repeatability_ref, self.render_repeatability_record_hash),
            (
                self.rendered_structure_evidence_ref,
                self.rendered_structure_evidence_record_hash,
            ),
            (
                self.rendered_structure_verification_ref,
                self.rendered_structure_verification_record_hash,
            ),
        )
        if any((ref is None) != (record_hash is None) for ref, record_hash in closure_pairs):
            raise ValueError("typed closure ref 与 record hash 必须同时存在或同时缺席。")
        refs = tuple(ref for ref, _record_hash in closure_pairs if ref is not None)
        if len({ref.artifact_id for ref in refs}) != len(refs):
            raise ValueError("typed closure refs 不得复用 artifact id。")
        expected_ref_types = (
            (self.selected_candidate_ref, "candidate_record", "candidate_record_v3"),
            (self.render_plan_ref, "renderer_plan", "renderer_plan_v3"),
            (self.render_progress_ref, "renderer_progress", "renderer_progress_v2"),
            (
                self.render_repeatability_ref,
                "render_repeatability_evidence",
                "render_repeatability_evidence_v2",
            ),
            (
                self.rendered_structure_evidence_ref,
                "rendered_structure_evidence",
                "rendered_structure_evidence_v4",
            ),
            (
                self.rendered_structure_verification_ref,
                "rendered_structure_verification",
                "rendered_structure_verification_v4",
            ),
        )
        for ref, expected_kind, expected_schema in expected_ref_types:
            if ref is not None and (
                ref.kind != expected_kind or ref.schema_version != expected_schema
            ):
                raise ValueError(
                    f"typed closure ref 类型不一致：{expected_kind}/{expected_schema}。"
                )
        has_prediction = self.verification_status == "structure_verified"
        full_prediction_vector = all(
            item.prediction_available and item.visible is not None
            for item in self.layer_predictions
        )
        unavailable_prediction_vector = all(
            not item.prediction_available and item.visible is None
            for item in self.layer_predictions
        )
        prediction_complete = (
            self.prediction_source
            == "selected_candidate_rendered_structure_verification_v4"
            and self.measured_topology not in {None, "unknown"}
            and self.measured_instance_count is not None
            and self.measured_hole_count is not None
            and all(
                value is not None
                for value in (
                    self.selected_candidate_ref,
                    self.selected_candidate_record_hash,
                    self.rendered_structure_evidence_ref,
                    self.rendered_structure_evidence_record_hash,
                    self.rendered_structure_verification_ref,
                    self.rendered_structure_verification_record_hash,
                )
            )
            and full_prediction_vector
        )
        if has_prediction != prediction_complete:
            raise ValueError("structure_verified 必须有完整 typed ref/hash 与实测预测。")
        if not has_prediction and not unavailable_prediction_vector:
            raise ValueError("未完成 verification 的十类预测必须全部 unavailable。")
        if not has_prediction and any(
            value is not None
            for value in (
                self.prediction_source,
                self.measured_topology,
                self.measured_instance_count,
                self.measured_hole_count,
            )
        ):
            raise ValueError("未完成 verification 不得伪造实测预测。")
        successful_render_count = (
            self.beauty_capture_count + self.diagnostic_render_count
        )
        if successful_render_count > self.logical_render_request_attempt_count:
            raise ValueError("成功 render 数不得超过已尝试 logical request。")
        if self.logical_render_request_attempt_count > self.nominal_render_request_count:
            raise ValueError("已尝试 logical render 不得超过冻结 plan 分母。")
        if self.render_retry_count != (
            self.transient_render_retry_count + self.unknown_render_retry_count
        ):
            raise ValueError("render retry 必须闭合 transient + unknown retry。")
        if self.unknown_render_retry_count > self.unknown_render_result_count:
            raise ValueError("unknown retry 不得超过 unknown result 次数。")
        if self.unknown_render_result_count > self.physical_render_call_count:
            raise ValueError("unknown result 不得超过物理 Render 调用数。")
        if self.physical_render_call_count != (
            self.logical_render_request_attempt_count + self.render_retry_count
        ):
            raise ValueError("physical render calls 与 logical attempt/retry 不一致。")
        if self.physical_render_call_count > 2 * self.logical_render_request_attempt_count:
            raise ValueError("每个 logical render 最多一次 retry。")
        if self.render_budget_used != self.physical_render_call_count:
            raise ValueError("render budget used 必须等于物理调用数。")

        fully_successful = (
            self.terminal_phase == "finalized"
            and self.stop_reason == "completed_with_objective_best"
            and self.final_state_sha256 is not None
            and self.seed_attempt_count == self.expected_seed_attempt_count
            and self.attempt_artifact_closure_count
            == self.expected_seed_attempt_count
            and self.successful_candidate_count == self.expected_seed_attempt_count
            and self.branch_best_count == self.hypothesis_count
            and has_prediction
            and all(ref is not None for ref, _record_hash in closure_pairs)
            and self.beauty_capture_count
            == self.expected_seed_attempt_count
            * V2_3_RENDERED_BEAUTY_CAPTURES_PER_ATTEMPT
            and self.diagnostic_render_count >= self.expected_seed_attempt_count
            and self.logical_render_request_attempt_count
            == self.nominal_render_request_count
            and successful_render_count == self.nominal_render_request_count
            and self.unknown_render_result_count == 0
            and self.render_budget_reserved == 0
            and self.renderer_environment_hash is not None
            and self.persisted_renderer_environment_hash is not None
            and len(self.all_candidate_refs) == self.expected_seed_attempt_count
            and len(self.actual_replay_receipt_hashes)
            == self.expected_seed_attempt_count
            and self.actual_replay_receipts_root is not None
        )
        if self.success != fully_successful:
            raise ValueError("success 与 Graph/Candidate/actual-render closure 不一致。")
        if self.success != (not self.failure_codes):
            raise ValueError("success 与 failure_codes 必须互斥。")
        if self.record_hash != compute_v2_3_rendered_case_outcome_hash(self):
            raise ValueError("Rendered case outcome record hash 不一致。")
        return self


class V2_3RenderedGraphSplitReport(FrozenModel):
    """development 或 validation 的独立 actual-render 指标。."""

    schema_version: Literal["v2_3_rendered_graph_split_report_v5"] = (
        V2_3_RENDERED_SPLIT_REPORT_SCHEMA_VERSION
    )
    split: Literal["development", "validation"]
    threshold_policy_hash: Sha256Hex
    cases_passed: V2_3ProportionMetric
    instance_count_exact: V2_3ProportionMetric
    structure_label_vector_exact: V2_3ProportionMetric
    critical_class_metrics: tuple[V2_3RenderedClassMetric, ...]
    macro_recall: V2_3MacroMetric
    macro_f1: V2_3MacroMetric
    ready: bool
    blockers: tuple[NonEmptyString, ...]
    record_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_report(self) -> V2_3RenderedGraphSplitReport:
        if tuple(item.class_id for item in self.critical_class_metrics) != (
            _CRITICAL_CLASSES
        ):
            raise ValueError("split critical class 顺序/集合不完整。")
        available = tuple(
            item.class_id for item in self.critical_class_metrics if item.metric_available
        )
        if (
            self.macro_recall.included_class_ids != available
            or self.macro_f1.included_class_ids != available
        ):
            raise ValueError("macro 必须只聚合该 split 有正例的冻结类。")
        expected_recall = sum(
            item.recall.value
            for item in self.critical_class_metrics
            if item.recall is not None
        ) / len(available)
        expected_f1 = sum(
            item.f1.value
            for item in self.critical_class_metrics
            if item.f1 is not None
        ) / len(available)
        if not math.isclose(
            self.macro_recall.value, expected_recall, rel_tol=0.0, abs_tol=1e-15
        ) or not math.isclose(
            self.macro_f1.value, expected_f1, rel_tol=0.0, abs_tol=1e-15
        ):
            raise ValueError("macro point estimate 与逐类指标不一致。")
        expected_blockers = _expected_split_blockers(self)
        if self.blockers != expected_blockers:
            raise ValueError("split blockers 未由冻结 policy/metrics 标准重算。")
        if self.ready != (not expected_blockers):
            raise ValueError("split ready 与冻结 policy/metrics 不一致。")
        if self.record_hash != compute_v2_3_rendered_split_report_hash(self):
            raise ValueError("Rendered split report record hash 不一致。")
        return self


class V2_3RenderedGraphGateReport(FrozenModel):
    """完整 development 10 + validation 41 的 verified breaking v5 报告。."""

    schema_version: Literal["v2_3_rendered_graph_gate_report_v5"] = (
        V2_3_RENDERED_GATE_REPORT_SCHEMA_VERSION
    )
    gate_stage: Literal["v2_3_rendered_structure_conformance"] = (
        "v2_3_rendered_structure_conformance"
    )
    quality_claim: Literal[
        "deterministic_intent_to_actual_render_structure_conformance"
    ] = "deterministic_intent_to_actual_render_structure_conformance"
    manifest_id: NonEmptyString
    dataset_version: NonEmptyString
    manifest_sha256: Sha256Hex
    taxonomy_sha256: Sha256Hex
    config_sha256: Sha256Hex
    threshold_policy: V2_3RenderedThresholdPolicy
    input_intent_outcomes_sha256: Sha256Hex
    input_compiler_outcomes_sha256: Sha256Hex
    outcomes_sha256: Sha256Hex
    development: V2_3RenderedGraphSplitReport
    validation: V2_3RenderedGraphSplitReport
    renderer_execution_class: Literal["actual_glsl_execution"] = (
        "actual_glsl_execution"
    )
    renderer_backend: Literal["chromium_webgl1_actual_v1"] = (
        "chromium_webgl1_actual_v1"
    )
    renderer_environment_hashes: tuple[Sha256Hex, ...]
    release_held_out_accessed: Literal[False] = False
    production_admission_enabled: Literal[False] = False
    model_calls: Literal[0] = 0
    ready: bool
    blockers: tuple[NonEmptyString, ...]
    record_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_report(self) -> V2_3RenderedGraphGateReport:
        if self.development.split != "development" or self.validation.split != (
            "validation"
        ):
            raise ValueError("development/validation split report 错位。")
        if self.renderer_environment_hashes != tuple(
            sorted(set(self.renderer_environment_hashes))
        ):
            raise ValueError("Renderer environment hashes 必须唯一排序。")
        expected_blockers = _expected_gate_blockers(
            development=self.development,
            validation=self.validation,
            renderer_environment_hashes=self.renderer_environment_hashes,
        )
        if self.blockers != expected_blockers:
            raise ValueError("Rendered Graph gate blockers 未由子报告/环境标准重算。")
        if self.ready != (not expected_blockers):
            raise ValueError("Rendered Graph gate ready 与子报告/环境不一致。")
        if self.record_hash != compute_v2_3_rendered_gate_report_hash(self):
            raise ValueError("Rendered Graph gate report record hash 不一致。")
        return self


def _expected_split_blockers(
    report: V2_3RenderedGraphSplitReport,
) -> tuple[str, ...]:
    """从冻结 policy 与指标正文唯一重算 split blockers。."""
    policy = build_v2_3_rendered_threshold_policy()
    if report.threshold_policy_hash != policy.policy_hash:
        raise ValueError("split threshold policy hash 不是仓库冻结版本。")
    expected_case_count = (
        policy.development_case_count
        if report.split == "development"
        else policy.validation_case_count
    )
    ordinary_metrics = (
        report.cases_passed,
        report.instance_count_exact,
        report.structure_label_vector_exact,
    )
    if any(item.denominator != expected_case_count for item in ordinary_metrics):
        raise ValueError("split 普通指标分母必须等于冻结 case count。")
    if any(
        item.positive_denominator + item.negative_denominator != expected_case_count
        for item in report.critical_class_metrics
    ):
        raise ValueError("split confusion matrix 必须逐类覆盖冻结 case count。")

    blockers: list[str] = []
    case_minimum = (
        policy.development_case_success_minimum
        if report.split == "development"
        else policy.validation_case_success_minimum
    )
    instance_minimum = (
        policy.development_instance_exact_minimum
        if report.split == "development"
        else policy.validation_instance_exact_minimum
    )
    if report.cases_passed.value < case_minimum:
        blockers.append(
            f"{report.split}_case_success:"
            f"{report.cases_passed.numerator}/{report.cases_passed.denominator}"
        )
    if report.instance_count_exact.value < instance_minimum:
        blockers.append(
            f"{report.split}_instance_exact:"
            f"{report.instance_count_exact.numerator}/"
            f"{report.instance_count_exact.denominator}"
        )
    if report.split == "development":
        if (
            report.structure_label_vector_exact.value
            < policy.development_structure_vector_exact_minimum
        ):
            blockers.append(
                "development_structure_vector_exact:"
                f"{report.structure_label_vector_exact.numerator}/"
                f"{report.structure_label_vector_exact.denominator}"
            )
        return tuple(blockers)

    frozen_denominators = policy.validation_positive_denominators.as_dict()
    for metric in report.critical_class_metrics:
        if metric.positive_denominator != frozen_denominators[metric.class_id]:
            blockers.append(
                f"validation_denominator:{metric.class_id}:"
                f"{metric.positive_denominator}/"
                f"{frozen_denominators[metric.class_id]}"
            )
        if metric.recall is None or (
            metric.recall.value < policy.validation_class_recall_minimum
        ):
            blockers.append(
                f"validation_recall_below_90_percent:{metric.class_id}:"
                f"{metric.true_positive}/{metric.positive_denominator}"
            )
        if metric.f1 is None or metric.f1.value < policy.validation_class_f1_minimum:
            f1_value = "unavailable" if metric.f1 is None else f"{metric.f1.value:.17g}"
            blockers.append(
                f"validation_f1_below_90_percent:{metric.class_id}:{f1_value}"
            )
    if report.macro_recall.value < policy.validation_macro_recall_minimum:
        blockers.append(
            "validation_macro_recall_below_90_percent:"
            f"{report.macro_recall.value:.17g}"
        )
    if report.macro_f1.value < policy.validation_macro_f1_minimum:
        blockers.append(
            f"validation_macro_f1_below_90_percent:{report.macro_f1.value:.17g}"
        )
    return tuple(blockers)


def _expected_gate_blockers(
    *,
    development: V2_3RenderedGraphSplitReport,
    validation: V2_3RenderedGraphSplitReport,
    renderer_environment_hashes: tuple[Sha256Hex, ...],
) -> tuple[str, ...]:
    blockers: list[str] = []
    if not development.ready:
        blockers.append("development_not_ready")
    if not validation.ready:
        blockers.append("validation_not_ready")
    if len(renderer_environment_hashes) != 1:
        blockers.append(
            f"renderer_environment_count:{len(renderer_environment_hashes)}/1"
        )
    return tuple(blockers)


def compute_v2_3_rendered_split_report_hash(
    value: V2_3RenderedGraphSplitReport | dict[str, object],
) -> str:
    """计算排除自身字段的 split report record hash。."""
    if isinstance(value, V2_3RenderedGraphSplitReport):
        payload = value.model_dump(mode="python", exclude={"record_hash"})
    else:
        payload = {
            "schema_version": V2_3_RENDERED_SPLIT_REPORT_SCHEMA_VERSION,
            **{key: item for key, item in value.items() if key != "record_hash"},
        }
    return canonical_sha256(
        {
            "hash_version": "v2_3_rendered_graph_split_report_hash_v2",
            "record": payload,
        }
    )


def compute_v2_3_rendered_gate_report_hash(
    value: V2_3RenderedGraphGateReport | dict[str, object],
) -> str:
    """计算排除自身字段的 gate report record hash。."""
    if isinstance(value, V2_3RenderedGraphGateReport):
        payload = value.model_dump(mode="python", exclude={"record_hash"})
    else:
        payload = {
            "schema_version": V2_3_RENDERED_GATE_REPORT_SCHEMA_VERSION,
            "gate_stage": "v2_3_rendered_structure_conformance",
            "quality_claim": (
                "deterministic_intent_to_actual_render_structure_conformance"
            ),
            "renderer_execution_class": "actual_glsl_execution",
            "renderer_backend": "chromium_webgl1_actual_v1",
            "release_held_out_accessed": False,
            "production_admission_enabled": False,
            "model_calls": 0,
            **{key: item for key, item in value.items() if key != "record_hash"},
        }
    return canonical_sha256(
        {
            "hash_version": "v2_3_rendered_graph_gate_report_hash_v2",
            "record": payload,
        }
    )


def compute_v2_3_rendered_threshold_policy_hash(
    value: V2_3RenderedThresholdPolicy | dict[str, object],
) -> str:
    """计算排除自身字段的 threshold policy hash。."""
    payload = (
        value.model_dump(mode="python", exclude={"policy_hash"})
        if isinstance(value, V2_3RenderedThresholdPolicy)
        else {key: item for key, item in value.items() if key != "policy_hash"}
    )
    return canonical_sha256(payload)


def build_v2_3_rendered_threshold_policy() -> V2_3RenderedThresholdPolicy:
    """创建仓库唯一冻结的 strict threshold policy。."""
    payload: dict[str, object] = {
        "schema_version": V2_3_RENDERED_THRESHOLD_POLICY_VERSION,
        "development_case_count": 10,
        "validation_case_count": 41,
        "seeds_per_hypothesis": 3,
        "beauty_captures_per_attempt": 5,
        "development_case_success_minimum": 1.0,
        "development_instance_exact_minimum": 1.0,
        "development_structure_vector_exact_minimum": 1.0,
        "validation_case_success_minimum": 1.0,
        "validation_instance_exact_minimum": 1.0,
        "validation_class_recall_minimum": 0.9,
        "validation_class_f1_minimum": 0.9,
        "validation_macro_recall_minimum": 0.9,
        "validation_macro_f1_minimum": 0.9,
        "validation_positive_denominators": (
            V2_3RenderedValidationDenominators()
        ),
        "recall_ci_method": "wilson_score_two_sided_95_v1",
        "bootstrap_method": (
            "paired_case_fixed_draw_available_percentile_95_mt19937_v2"
        ),
        "bootstrap_replicates": 20_000,
    }
    payload["policy_hash"] = compute_v2_3_rendered_threshold_policy_hash(payload)
    return V2_3RenderedThresholdPolicy.model_validate(payload, strict=True)


def compute_v2_3_rendered_case_outcome_hash(
    value: V2_3RenderedGraphCaseOutcome | dict[str, object],
) -> str:
    """计算排除自身字段的 case outcome record hash。."""
    if isinstance(value, V2_3RenderedGraphCaseOutcome):
        payload = value.model_dump(mode="python", exclude={"record_hash"})
    else:
        payload = {
            "schema_version": V2_3_RENDERED_CASE_OUTCOME_SCHEMA_VERSION,
            "gate_stage": "v2_3_rendered_structure_conformance",
            "renderer_execution_class": "actual_glsl_execution",
            "renderer_backend": "chromium_webgl1_actual_v1",
            "release_held_out_accessed": False,
            "production_admission_enabled": False,
            "model_calls": 0,
            **{key: item for key, item in value.items() if key != "record_hash"},
        }
    return canonical_sha256(
        {
            "hash_version": "v2_3_rendered_graph_case_outcome_hash_v2",
            "record": payload,
        }
    )


def compute_v2_3_actual_replay_receipts_root(
    candidate_refs: tuple[ArtifactRefV2, ...],
    receipt_hashes: tuple[str, ...],
) -> str:
    """按 State 顺序绑定全部 Candidate refs 与 concrete replay receipts。."""
    if len(candidate_refs) != len(receipt_hashes):
        raise ValueError("Candidate refs 与 actual replay receipt hashes 数量不一致。")
    return canonical_sha256(
        {
            "hash_version": "v2_3_actual_replay_receipts_root_v1",
            "bindings": tuple(
                {
                    "candidate_ref": ref,
                    "actual_replay_receipt_hash": receipt_hash,
                }
                for ref, receipt_hash in zip(
                    candidate_refs, receipt_hashes, strict=True
                )
            ),
        }
    )


def _wilson_interval(numerator: int, denominator: int) -> V2_3WilsonInterval95:
    if denominator <= 0:
        raise ValueError("Wilson denominator 必须大于 0。")
    z = 1.959963984540054
    p = numerator / denominator
    z2 = z * z
    scale = 1.0 + z2 / denominator
    center = (p + z2 / (2.0 * denominator)) / scale
    margin = (
        z
        * math.sqrt(p * (1.0 - p) / denominator + z2 / (4 * denominator**2))
        / scale
    )
    return V2_3WilsonInterval95(
        lower=max(0.0, center - margin), upper=min(1.0, center + margin)
    )


def _proportion(numerator: int, denominator: int) -> V2_3ProportionMetric:
    return V2_3ProportionMetric(
        numerator=numerator,
        denominator=denominator,
        value=numerator / denominator,
        ci95=_wilson_interval(numerator, denominator),
    )


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        raise ValueError("Bootstrap percentile 不接受空样本。")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _bootstrap_ci(values: list[float]) -> V2_3BootstrapInterval95:
    if not values or len(values) > V2_3_RENDERED_BOOTSTRAP_REPLICATES:
        raise ValueError("Bootstrap CI 必须绑定固定 20000 draws 的非空可用子集。")
    return V2_3BootstrapInterval95(
        accepted_replicates=len(values),
        undefined_replicates=V2_3_RENDERED_BOOTSTRAP_REPLICATES - len(values),
        lower=_percentile(values, 0.025),
        upper=_percentile(values, 0.975),
    )


def _truth(sample: V2DatasetSample, class_id: CriticalClassId) -> bool:
    if class_id == "multi_instance":
        return sample.instance_count > 1
    if class_id == "ring":
        return sample.topology == "ring"
    if class_id == "hollow":
        return sample.topology == "hollow"
    layer = {
        "required_highlight": "highlight",
        "required_rim": "rim",
        "required_outline": "outline",
    }[class_id]
    return layer in sample.required_layers


def _has_verified_prediction(outcome: V2_3RenderedGraphCaseOutcome) -> bool:
    return outcome.success and outcome.verification_status == "structure_verified"


def _prediction(
    outcome: V2_3RenderedGraphCaseOutcome, class_id: CriticalClassId
) -> bool:
    if not _has_verified_prediction(outcome):
        return False
    if class_id == "multi_instance":
        assert outcome.measured_instance_count is not None
        return outcome.measured_instance_count > 1
    if class_id == "ring":
        return outcome.measured_topology == "ring"
    if class_id == "hollow":
        return outcome.measured_topology == "hollow"
    layer = {
        "required_highlight": "highlight",
        "required_rim": "rim",
        "required_outline": "outline",
    }[class_id]
    visible = next(
        item for item in outcome.layer_predictions if item.layer == layer
    ).visible
    assert visible is not None
    return visible


def _confusion(
    samples: tuple[V2DatasetSample, ...],
    outcomes: dict[str, V2_3RenderedGraphCaseOutcome],
    class_id: CriticalClassId,
    indices: tuple[int, ...] | None = None,
) -> tuple[int, int, int, int]:
    selected = range(len(samples)) if indices is None else indices
    tp = fp = fn = tn = 0
    for index in selected:
        sample = samples[index]
        actual = _truth(sample, class_id)
        predicted = _prediction(outcomes[sample.case_id], class_id)
        if actual and predicted:
            tp += 1
        elif predicted:
            fp += 1
        elif actual:
            fn += 1
        else:
            tn += 1
    return tp, fp, fn, tn


def _paired_bootstrap(
    *,
    split: Literal["development", "validation"],
    samples: tuple[V2DatasetSample, ...],
    outcomes: dict[str, V2_3RenderedGraphCaseOutcome],
    policy: V2_3RenderedThresholdPolicy,
) -> tuple[
    dict[CriticalClassId, list[float]],
    list[float],
    list[float],
]:
    baseline_available = tuple(
        class_id
        for class_id in _CRITICAL_CLASSES
        if any(_truth(sample, class_id) for sample in samples)
    )
    f1_values: dict[CriticalClassId, list[float]] = {
        class_id: [] for class_id in baseline_available
    }
    macro_recall_values: list[float] = []
    macro_f1_values: list[float] = []
    seed_material = (
        f"{policy.policy_hash}:{split}:{policy.bootstrap_method}:"
        f"{policy.bootstrap_replicates}:"
        + ":".join(sample.case_id for sample in samples)
    ).encode()
    rng = random.Random(int.from_bytes(sha256(seed_material).digest(), "big"))
    for _draw in range(policy.bootstrap_replicates):
        indices = tuple(rng.randrange(len(samples)) for _ in samples)
        recalls: list[float] = []
        f1s: list[float] = []
        complete_macro_draw = True
        for class_id in baseline_available:
            tp, fp, fn, _tn = _confusion(
                samples, outcomes, class_id, indices=indices
            )
            positive = tp + fn
            if positive == 0:
                complete_macro_draw = False
                continue
            recall = tp / positive
            f1 = 2 * tp / (2 * tp + fp + fn)
            f1_values[class_id].append(f1)
            recalls.append(recall)
            f1s.append(f1)
        if not complete_macro_draw:
            continue
        macro_recall_values.append(sum(recalls) / len(recalls))
        macro_f1_values.append(sum(f1s) / len(f1s))
    return f1_values, macro_recall_values, macro_f1_values


def _structure_vector_exact(
    sample: V2DatasetSample, outcome: V2_3RenderedGraphCaseOutcome
) -> bool:
    if not _has_verified_prediction(outcome):
        return False
    assert outcome.measured_instance_count is not None
    assert outcome.measured_hole_count is not None
    if (
        outcome.measured_instance_count != sample.instance_count
        or outcome.measured_hole_count != sample.hole_count
        or outcome.measured_topology != sample.topology
    ):
        return False
    if not all(
        row.prediction_available
        and row.visible == (row.layer in sample.required_layers)
        for row in outcome.layer_predictions
    ):
        return False
    return all(
        _truth(sample, class_id) == _prediction(outcome, class_id)
        for class_id in _CRITICAL_CLASSES
    )


def _split_report(
    *,
    split: Literal["development", "validation"],
    samples: tuple[V2DatasetSample, ...],
    outcomes: dict[str, V2_3RenderedGraphCaseOutcome],
    policy: V2_3RenderedThresholdPolicy,
) -> V2_3RenderedGraphSplitReport:
    f1_bootstrap, macro_recall_samples, macro_f1_samples = _paired_bootstrap(
        split=split, samples=samples, outcomes=outcomes, policy=policy
    )
    class_metrics: list[V2_3RenderedClassMetric] = []
    for class_id in _CRITICAL_CLASSES:
        tp, fp, fn, tn = _confusion(samples, outcomes, class_id)
        positive = tp + fn
        available = positive > 0
        recall = _proportion(tp, positive) if available else None
        f1 = (
            V2_3F1Metric(
                numerator=2 * tp,
                denominator=2 * tp + fp + fn,
                value=2 * tp / (2 * tp + fp + fn),
                ci95=_bootstrap_ci(f1_bootstrap[class_id]),
            )
            if available
            else None
        )
        class_metrics.append(
            V2_3RenderedClassMetric(
                class_id=class_id,
                true_positive=tp,
                false_positive=fp,
                false_negative=fn,
                true_negative=tn,
                positive_denominator=positive,
                negative_denominator=fp + tn,
                metric_available=available,
                recall=recall,
                f1=f1,
            )
        )
    available_ids = tuple(
        item.class_id for item in class_metrics if item.metric_available
    )
    macro_recall_value = sum(
        item.recall.value for item in class_metrics if item.recall is not None
    ) / len(available_ids)
    macro_f1_value = sum(
        item.f1.value for item in class_metrics if item.f1 is not None
    ) / len(available_ids)
    cases = _proportion(sum(outcomes[item.case_id].success for item in samples), len(samples))
    instance_exact = _proportion(
        sum(
            _has_verified_prediction(outcomes[item.case_id])
            and outcomes[item.case_id].measured_instance_count == item.instance_count
            for item in samples
        ),
        len(samples),
    )
    vector_exact = _proportion(
        sum(_structure_vector_exact(item, outcomes[item.case_id]) for item in samples),
        len(samples),
    )
    blockers: list[str] = []
    if cases.value < 1.0:
        blockers.append(f"{split}_case_success:{cases.numerator}/{cases.denominator}")
    if instance_exact.value < 1.0:
        blockers.append(
            f"{split}_instance_exact:{instance_exact.numerator}/{instance_exact.denominator}"
        )
    if split == "development":
        if vector_exact.value < policy.development_structure_vector_exact_minimum:
            blockers.append(
                "development_structure_vector_exact:"
                f"{vector_exact.numerator}/{vector_exact.denominator}"
            )
    else:
        frozen_denominators = policy.validation_positive_denominators.as_dict()
        for metric in class_metrics:
            if metric.positive_denominator != frozen_denominators[metric.class_id]:
                blockers.append(
                    f"validation_denominator:{metric.class_id}:"
                    f"{metric.positive_denominator}/{frozen_denominators[metric.class_id]}"
                )
            if metric.recall is None or (
                metric.recall.value < policy.validation_class_recall_minimum
            ):
                blockers.append(
                    f"validation_recall_below_90_percent:{metric.class_id}:"
                    f"{metric.true_positive}/{metric.positive_denominator}"
                )
            if metric.f1 is None or metric.f1.value < policy.validation_class_f1_minimum:
                f1_value = "unavailable" if metric.f1 is None else f"{metric.f1.value:.17g}"
                blockers.append(
                    f"validation_f1_below_90_percent:{metric.class_id}:{f1_value}"
                )
        if macro_recall_value < policy.validation_macro_recall_minimum:
            blockers.append(
                f"validation_macro_recall_below_90_percent:{macro_recall_value:.17g}"
            )
        if macro_f1_value < policy.validation_macro_f1_minimum:
            blockers.append(
                f"validation_macro_f1_below_90_percent:{macro_f1_value:.17g}"
            )
    macro_recall = V2_3MacroMetric(
        included_class_ids=available_ids,
        class_count=len(available_ids),
        value=macro_recall_value,
        ci95=_bootstrap_ci(macro_recall_samples),
    )
    macro_f1 = V2_3MacroMetric(
        included_class_ids=available_ids,
        class_count=len(available_ids),
        value=macro_f1_value,
        ci95=_bootstrap_ci(macro_f1_samples),
    )
    payload: dict[str, object] = {
        "split": split,
        "threshold_policy_hash": policy.policy_hash,
        "cases_passed": cases,
        "instance_count_exact": instance_exact,
        "structure_label_vector_exact": vector_exact,
        "critical_class_metrics": tuple(class_metrics),
        "macro_recall": macro_recall,
        "macro_f1": macro_f1,
        "ready": not blockers,
        "blockers": tuple(blockers),
    }
    payload["record_hash"] = compute_v2_3_rendered_split_report_hash(payload)
    return V2_3RenderedGraphSplitReport.model_validate(payload, strict=True)


def _validate_dataset_identity(
    dataset: LoadedV2Dataset, stage_gate: V2DatasetStageGate
) -> None:
    if dataset.gate_stage != "v2_3_graph_conformance":
        raise ValueError("Rendered gate 当前只接受封存 release 的 V2.3 conformance 数据。")
    if (
        stage_gate.stage != "v2_3_graph_conformance"
        or stage_gate.required_splits != ("validation",)
        or not stage_gate.ready
        or stage_gate.blockers
    ):
        raise ValueError("Rendered gate 的 V2.3 dataset StageGate 未通过。")
    if (
        stage_gate.manifest_id,
        stage_gate.dataset_version,
        stage_gate.manifest_sha256,
        stage_gate.taxonomy_sha256,
    ) != (
        dataset.manifest.manifest_id,
        dataset.manifest.dataset_version,
        dataset.manifest_sha256,
        dataset.taxonomy_sha256,
    ):
        raise ValueError("Rendered gate StageGate/dataset identity 不一致。")
    release = dataset.manifest.split("release-held-out")
    if release.status != "not_populated" or release.samples:
        raise ValueError("Rendered conformance 必须保持 release-held-out 未填充封存。")


_VERIFIED_RENDERED_CASE_CAPABILITY_TOKEN = object()


class V2_3VerifiedRenderedCaseCapability:
    """同进程 State collector 才能签发的不可序列化 case capability。"""

    __slots__ = ("_outcome", "_token")

    def __init__(
        self, outcome: V2_3RenderedGraphCaseOutcome, *, _token: object
    ) -> None:
        if _token is not _VERIFIED_RENDERED_CASE_CAPABILITY_TOKEN:
            raise TypeError("Verified rendered case capability 只能由 strict collector 签发。")
        self._outcome = outcome
        self._token = _token

    @property
    def outcome(self) -> V2_3RenderedGraphCaseOutcome:
        """返回 collector 已从 confirmed State/replay 派生的 immutable outcome。"""
        if self._token is not _VERIFIED_RENDERED_CASE_CAPABILITY_TOKEN:
            raise TypeError("Verified rendered case capability token 无效。")
        return self._outcome


def _issue_v2_3_verified_rendered_case_capability(
    outcome: V2_3RenderedGraphCaseOutcome,
) -> V2_3VerifiedRenderedCaseCapability:
    """Agent strict collector 的窄签发边界；不得作为普通 outcome adapter。"""
    return V2_3VerifiedRenderedCaseCapability(
        outcome, _token=_VERIFIED_RENDERED_CASE_CAPABILITY_TOKEN
    )


def _evaluate_v2_3_rendered_structure_statistics(
    dataset: LoadedV2Dataset,
    stage_gate: V2DatasetStageGate,
    outcomes: tuple[V2_3RenderedGraphCaseOutcome, ...],
    *,
    config_sha256: str,
    input_intent_outcomes_sha256: str,
    input_compiler_outcomes_sha256: str,
    threshold_policy: V2_3RenderedThresholdPolicy | None = None,
) -> V2_3RenderedGraphGateReport:
    """内部纯统计内核；不得直接用于 actual-render admission。"""
    _validate_dataset_identity(dataset, stage_gate)
    policy = threshold_policy or build_v2_3_rendered_threshold_policy()
    development = tuple(
        sample
        for sample in dataset.manifest.split("development").samples
        if sample.dataset_role == "regression"
        and sample.source_suite_id == "png_to_shader_v1_m0"
    )
    validation = dataset.manifest.split("validation").samples
    if (
        len(development) != policy.development_case_count
        or len(validation) != policy.validation_case_count
    ):
        raise ValueError("Rendered gate 要求冻结 development 10 + validation 41。")
    expected_identity = (
        stage_gate.manifest_id,
        stage_gate.dataset_version,
        stage_gate.manifest_sha256,
        stage_gate.taxonomy_sha256,
        config_sha256,
        policy.policy_hash,
        input_intent_outcomes_sha256,
        input_compiler_outcomes_sha256,
    )
    indexed: dict[tuple[str, str], V2_3RenderedGraphCaseOutcome] = {}
    for raw in outcomes:
        outcome = V2_3RenderedGraphCaseOutcome.model_validate(raw, strict=True)
        if outcome.split == "release-held-out":
            raise ValueError("Rendered conformance 禁止接收 release-held-out outcome。")
        if (
            outcome.manifest_id,
            outcome.dataset_version,
            outcome.manifest_sha256,
            outcome.taxonomy_sha256,
            outcome.config_sha256,
            outcome.threshold_policy_hash,
            outcome.input_intent_outcomes_sha256,
            outcome.input_compiler_outcomes_sha256,
        ) != expected_identity:
            raise ValueError(f"outcome {outcome.case_id} identity/hash 不一致。")
        outcome_key = (outcome.split, outcome.case_id)
        if outcome_key in indexed:
            raise ValueError(f"Rendered case outcome 重复：{outcome_key}。")
        indexed[outcome_key] = outcome
    expected_samples = {
        **{("development", sample.case_id): sample for sample in development},
        **{("validation", sample.case_id): sample for sample in validation},
    }
    if set(indexed) != set(expected_samples):
        missing = sorted(set(expected_samples) - set(indexed))
        extra = sorted(set(indexed) - set(expected_samples))
        raise ValueError(f"Rendered outcome case 集不闭合；missing={missing} extra={extra}。")
    for sample_key, sample in expected_samples.items():
        if indexed[sample_key].source_image_sha256 != sample.sha256:
            raise ValueError(f"outcome {sample_key} source image identity 不一致。")
    ordered = tuple(indexed[item_key] for item_key in sorted(indexed))
    development_outcomes = {
        sample.case_id: indexed[("development", sample.case_id)]
        for sample in development
    }
    validation_outcomes = {
        sample.case_id: indexed[("validation", sample.case_id)]
        for sample in validation
    }
    development_report = _split_report(
        split="development",
        samples=development,
        outcomes=development_outcomes,
        policy=policy,
    )
    validation_report = _split_report(
        split="validation",
        samples=validation,
        outcomes=validation_outcomes,
        policy=policy,
    )
    environments = tuple(
        sorted(
            {
                item.renderer_environment_hash
                for item in ordered
                if item.renderer_environment_hash is not None
            }
        )
    )
    blockers = _expected_gate_blockers(
        development=development_report,
        validation=validation_report,
        renderer_environment_hashes=environments,
    )
    payload: dict[str, object] = {
        "manifest_id": stage_gate.manifest_id,
        "dataset_version": stage_gate.dataset_version,
        "manifest_sha256": stage_gate.manifest_sha256,
        "taxonomy_sha256": stage_gate.taxonomy_sha256,
        "config_sha256": config_sha256,
        "threshold_policy": policy,
        "input_intent_outcomes_sha256": input_intent_outcomes_sha256,
        "input_compiler_outcomes_sha256": input_compiler_outcomes_sha256,
        "outcomes_sha256": canonical_sha256(
            tuple(item.model_dump(mode="python") for item in ordered)
        ),
        "development": development_report,
        "validation": validation_report,
        "renderer_environment_hashes": environments,
        "ready": not blockers,
        "blockers": blockers,
    }
    payload["record_hash"] = compute_v2_3_rendered_gate_report_hash(payload)
    return V2_3RenderedGraphGateReport.model_validate(payload, strict=True)


def evaluate_v2_3_rendered_structure_gate(
    dataset: LoadedV2Dataset,
    stage_gate: V2DatasetStageGate,
    verified_cases: tuple[V2_3VerifiedRenderedCaseCapability, ...],
    *,
    config_sha256: str,
    input_intent_outcomes_sha256: str,
    input_compiler_outcomes_sha256: str,
    threshold_policy: V2_3RenderedThresholdPolicy | None = None,
) -> V2_3RenderedGraphGateReport:
    """只聚合 confirmed State + concrete Chromium replay 产生的 capability。"""
    if not isinstance(verified_cases, tuple) or any(
        not isinstance(item, V2_3VerifiedRenderedCaseCapability)
        for item in verified_cases
    ):
        raise TypeError("正式 rendered gate 只接受 strict collector capability tuple。")
    outcomes = tuple(item.outcome for item in verified_cases)
    return _evaluate_v2_3_rendered_structure_statistics(
        dataset,
        stage_gate,
        outcomes,
        config_sha256=config_sha256,
        input_intent_outcomes_sha256=input_intent_outcomes_sha256,
        input_compiler_outcomes_sha256=input_compiler_outcomes_sha256,
        threshold_policy=threshold_policy,
    )


__all__ = [
    "V2_3_RENDERED_BOOTSTRAP_REPLICATES",
    "V2_3_RENDERED_CASE_OUTCOME_SCHEMA_VERSION",
    "V2_3_RENDERED_GATE_REPORT_SCHEMA_VERSION",
    "V2_3_RENDERED_SPLIT_REPORT_SCHEMA_VERSION",
    "V2_3_RENDERED_THRESHOLD_POLICY_VERSION",
    "V2_3BootstrapInterval95",
    "V2_3CountMetric",
    "V2_3F1Metric",
    "V2_3MacroMetric",
    "V2_3ProportionMetric",
    "V2_3RenderedClassMetric",
    "V2_3RenderedGraphCaseOutcome",
    "V2_3RenderedGraphGateReport",
    "V2_3RenderedGraphSplitReport",
    "V2_3RenderedLayerPrediction",
    "V2_3RenderedThresholdPolicy",
    "V2_3RenderedValidationDenominators",
    "V2_3VerifiedRenderedCaseCapability",
    "V2_3WilsonInterval95",
    "build_v2_3_rendered_threshold_policy",
    "compute_v2_3_rendered_case_outcome_hash",
    "compute_v2_3_actual_replay_receipts_root",
    "compute_v2_3_rendered_gate_report_hash",
    "compute_v2_3_rendered_split_report_hash",
    "compute_v2_3_rendered_threshold_policy_hash",
    "evaluate_v2_3_rendered_structure_gate",
]
