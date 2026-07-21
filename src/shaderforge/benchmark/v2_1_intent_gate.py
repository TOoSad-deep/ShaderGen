"""V2.1 Intent validation 的严格纯聚合门禁。."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from shaderforge.benchmark.v2_dataset import (
    CRITICAL_CLASS_IDS,
    LoadedV2Dataset,
    RequiredLayer,
    V2DatasetSample,
    V2DatasetStageGate,
)
from shaderforge.contracts import FrozenModel, NonEmptyString, Sha256Hex
from shaderforge.contracts.canonical import canonical_sha256

V2_1_INTENT_CASE_OUTCOME_SCHEMA_VERSION: Literal["v2_1_intent_case_outcome_v1"] = (
    "v2_1_intent_case_outcome_v1"
)
V2_1_INTENT_GATE_REPORT_SCHEMA_VERSION: Literal["v2_1_intent_gate_report_v1"] = (
    "v2_1_intent_gate_report_v1"
)

V2_1_CURRENT_REGRESSION_REQUIRED_COUNT = 10
V2_1_VALIDATION_INTENT_LEGAL_MINIMUM = 0.80
V2_1_CRITICAL_CLASS_RECALL_MINIMUM = 0.90

CriticalClassId = Literal[
    "multi_instance",
    "ring",
    "hollow",
    "required_highlight",
    "required_rim",
    "required_outline",
]
_TYPED_CRITICAL_CLASS_IDS: tuple[CriticalClassId, ...] = (
    "multi_instance",
    "ring",
    "hollow",
    "required_highlight",
    "required_rim",
    "required_outline",
)
if _TYPED_CRITICAL_CLASS_IDS != CRITICAL_CLASS_IDS:  # pragma: no cover
    raise RuntimeError("Intent gate critical classes 与 Dataset taxonomy 漂移。")


class V2_1IntentCaseOutcome(FrozenModel):
    """一次真实 case 执行的 typed outcome；不允许聚合器补预测。."""

    schema_version: Literal["v2_1_intent_case_outcome_v1"] = (
        V2_1_INTENT_CASE_OUTCOME_SCHEMA_VERSION
    )
    gate_stage: Literal["v2_1_intent"] = "v2_1_intent"
    manifest_id: NonEmptyString
    dataset_version: NonEmptyString
    manifest_sha256: Sha256Hex
    taxonomy_sha256: Sha256Hex
    split: Literal["development", "validation", "release-held-out"]
    case_id: NonEmptyString
    intent_valid: bool
    predicted_topology: Literal["solid", "hollow", "ring", "open"] | None
    predicted_instance_count: int | None = Field(default=None, ge=1)
    predicted_required_layers: tuple[RequiredLayer, ...] = ()
    failure_code: NonEmptyString | None = None

    @model_validator(mode="after")
    def _validate_outcome(self) -> V2_1IntentCaseOutcome:
        if len(self.predicted_required_layers) != len(
            set(self.predicted_required_layers)
        ):
            raise ValueError("predicted_required_layers 不得重复。")
        if self.intent_valid:
            if self.predicted_topology is None or self.predicted_instance_count is None:
                raise ValueError("合法 Intent 必须提供完整结构预测。")
            if self.failure_code is not None:
                raise ValueError("合法 Intent 不得携带 failure_code。")
        elif (
            self.predicted_topology is not None
            or self.predicted_instance_count is not None
            or self.predicted_required_layers
            or self.failure_code is None
        ):
            raise ValueError("非法 Intent 只允许记录 failure_code，不得伪造预测。")
        return self


class WilsonInterval95(FrozenModel):
    """一个二项比例的双侧 95% Wilson score interval。."""

    lower: float = Field(ge=0.0, le=1.0)
    upper: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_order(self) -> WilsonInterval95:
        if self.lower > self.upper:
            raise ValueError("Wilson interval 必须满足 lower <= upper。")
        return self


class V2_1ProportionMetric(FrozenModel):
    """带 numerator/denominator 与 Wilson CI 的冻结比例。."""

    numerator: int = Field(ge=0)
    denominator: int = Field(ge=1)
    value: float = Field(ge=0.0, le=1.0)
    ci95: WilsonInterval95

    @model_validator(mode="after")
    def _validate_ratio(self) -> V2_1ProportionMetric:
        if self.numerator > self.denominator:
            raise ValueError("比例 numerator 不得超过 denominator。")
        expected = self.numerator / self.denominator
        if not math.isclose(self.value, expected, rel_tol=0.0, abs_tol=1e-15):
            raise ValueError("比例 value 与 numerator/denominator 不一致。")
        if self.ci95 != _wilson_interval(self.numerator, self.denominator):
            raise ValueError("比例 CI 与 Wilson 95% 公式不一致。")
        return self


class V2_1IntentClassMetric(FrozenModel):
    """一个 validation 关键类的 recall/F1 事实。."""

    class_id: CriticalClassId
    true_positive: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    recall: V2_1ProportionMetric
    f1_numerator: int = Field(ge=0)
    f1_denominator: int = Field(ge=1)
    f1: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_confusion_counts(self) -> V2_1IntentClassMetric:
        if self.recall.numerator != self.true_positive:
            raise ValueError("recall numerator 必须等于 true_positive。")
        if self.recall.denominator != self.true_positive + self.false_negative:
            raise ValueError("recall denominator 必须等于 TP + FN。")
        expected_numerator = 2 * self.true_positive
        expected_denominator = (
            2 * self.true_positive + self.false_positive + self.false_negative
        )
        if self.f1_numerator != expected_numerator:
            raise ValueError("F1 numerator 必须等于 2 * TP。")
        if self.f1_denominator != expected_denominator:
            raise ValueError("F1 denominator 必须等于 2TP + FP + FN。")
        if not math.isclose(
            self.f1,
            self.f1_numerator / self.f1_denominator,
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise ValueError("F1 与 numerator/denominator 不一致。")
        return self


class V2_1IntentGateReport(FrozenModel):
    """完整 outcome 集合的不可变 V2.1 Intent gate 报告。."""

    schema_version: Literal["v2_1_intent_gate_report_v1"] = (
        V2_1_INTENT_GATE_REPORT_SCHEMA_VERSION
    )
    gate_stage: Literal["v2_1_intent"] = "v2_1_intent"
    manifest_id: NonEmptyString
    dataset_version: NonEmptyString
    manifest_sha256: Sha256Hex
    taxonomy_sha256: Sha256Hex
    outcomes_sha256: Sha256Hex
    current_10_intent_legal: V2_1ProportionMetric
    validation_intent_legal: V2_1ProportionMetric
    validation_instance_count_exact: V2_1ProportionMetric
    critical_class_metrics: tuple[V2_1IntentClassMetric, ...]
    macro_recall: float = Field(ge=0.0, le=1.0)
    macro_f1: float = Field(ge=0.0, le=1.0)
    ready: bool
    blockers: tuple[NonEmptyString, ...]

    @model_validator(mode="after")
    def _validate_report(self) -> V2_1IntentGateReport:
        if tuple(item.class_id for item in self.critical_class_metrics) != (
            CRITICAL_CLASS_IDS
        ):
            raise ValueError("critical_class_metrics 必须完整且顺序固定。")
        expected_macro_recall = sum(
            item.recall.value for item in self.critical_class_metrics
        ) / len(self.critical_class_metrics)
        expected_macro_f1 = sum(item.f1 for item in self.critical_class_metrics) / len(
            self.critical_class_metrics
        )
        if not math.isclose(
            self.macro_recall,
            expected_macro_recall,
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise ValueError("macro_recall 与逐类 recall 不一致。")
        if not math.isclose(
            self.macro_f1,
            expected_macro_f1,
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise ValueError("macro_f1 与逐类 F1 不一致。")
        if self.ready != (not self.blockers):
            raise ValueError("Intent gate ready 与 blockers 不一致。")
        return self


def _wilson_interval(numerator: int, denominator: int) -> WilsonInterval95:
    if denominator <= 0:
        raise ValueError("Wilson interval denominator 必须大于 0。")
    z = 1.959963984540054
    proportion = numerator / denominator
    z_squared = z * z
    scale = 1.0 + z_squared / denominator
    center = (proportion + z_squared / (2.0 * denominator)) / scale
    margin = (
        z
        * math.sqrt(
            proportion * (1.0 - proportion) / denominator
            + z_squared / (4.0 * denominator * denominator)
        )
        / scale
    )
    return WilsonInterval95(
        lower=max(0.0, center - margin),
        upper=min(1.0, center + margin),
    )


def _proportion(numerator: int, denominator: int) -> V2_1ProportionMetric:
    return V2_1ProportionMetric(
        numerator=numerator,
        denominator=denominator,
        value=numerator / denominator,
        ci95=_wilson_interval(numerator, denominator),
    )


def _actual_class(sample: V2DatasetSample, class_id: str) -> bool:
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


def _predicted_class(outcome: V2_1IntentCaseOutcome, class_id: str) -> bool:
    if not outcome.intent_valid:
        return False
    if class_id == "multi_instance":
        assert outcome.predicted_instance_count is not None
        return outcome.predicted_instance_count > 1
    if class_id == "ring":
        return outcome.predicted_topology == "ring"
    if class_id == "hollow":
        return outcome.predicted_topology == "hollow"
    layer = {
        "required_highlight": "highlight",
        "required_rim": "rim",
        "required_outline": "outline",
    }[class_id]
    return layer in outcome.predicted_required_layers


def _class_metric(
    class_id: CriticalClassId,
    samples: tuple[V2DatasetSample, ...],
    outcomes: dict[str, V2_1IntentCaseOutcome],
) -> V2_1IntentClassMetric:
    true_positive = false_positive = false_negative = 0
    for sample in samples:
        actual = _actual_class(sample, class_id)
        predicted = _predicted_class(outcomes[sample.case_id], class_id)
        if actual and predicted:
            true_positive += 1
        elif predicted:
            false_positive += 1
        elif actual:
            false_negative += 1
    recall_denominator = true_positive + false_negative
    if recall_denominator <= 0:
        raise ValueError(f"关键类 {class_id} 在 validation 中没有正例。")
    f1_numerator = 2 * true_positive
    f1_denominator = f1_numerator + false_positive + false_negative
    if f1_denominator <= 0:
        raise ValueError(f"关键类 {class_id} 无法计算 F1。")
    return V2_1IntentClassMetric(
        class_id=class_id,
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        recall=_proportion(true_positive, recall_denominator),
        f1_numerator=f1_numerator,
        f1_denominator=f1_denominator,
        f1=f1_numerator / f1_denominator,
    )


def _validate_dataset_gate_identity(
    dataset: LoadedV2Dataset,
    stage_gate: V2DatasetStageGate,
) -> None:
    if dataset.gate_stage != "v2_1_intent":
        raise ValueError("Intent report 只接受 gate_stage='v2_1_intent' 数据集。")
    if stage_gate.stage != "v2_1_intent":
        raise ValueError("Intent report 只接受 V2.1 StageGate。")
    if not stage_gate.ready or stage_gate.blockers:
        raise ValueError("V2.1 StageGate 未通过，不得聚合 Intent 结果。")
    expected = (
        dataset.manifest.manifest_id,
        dataset.manifest.dataset_version,
        dataset.manifest_sha256,
        dataset.taxonomy_sha256,
    )
    actual = (
        stage_gate.manifest_id,
        stage_gate.dataset_version,
        stage_gate.manifest_sha256,
        stage_gate.taxonomy_sha256,
    )
    if actual != expected:
        raise ValueError("StageGate 与 LoadedV2Dataset 内容身份不一致。")


def evaluate_v2_1_intent_gate(
    dataset: LoadedV2Dataset,
    stage_gate: V2DatasetStageGate,
    outcomes: tuple[V2_1IntentCaseOutcome, ...],
) -> V2_1IntentGateReport:
    """聚合真实 outcomes；缺失、重复、额外或 release case 全部 fail closed。."""
    _validate_dataset_gate_identity(dataset, stage_gate)
    development = tuple(
        sample
        for sample in dataset.manifest.split("development").samples
        if sample.dataset_role == "regression"
        and sample.source_suite_id == "png_to_shader_v1_m0"
    )
    validation = dataset.manifest.split("validation").samples
    if len(development) != V2_1_CURRENT_REGRESSION_REQUIRED_COUNT:
        raise ValueError("development 当前回归集必须精确包含冻结的 10 例。")
    if not validation:
        raise ValueError("validation 不能为空。")

    expected_identity = (
        stage_gate.manifest_id,
        stage_gate.dataset_version,
        stage_gate.manifest_sha256,
        stage_gate.taxonomy_sha256,
    )
    indexed: dict[tuple[str, str], V2_1IntentCaseOutcome] = {}
    for outcome in outcomes:
        if outcome.gate_stage != stage_gate.stage:
            raise ValueError(f"outcome {outcome.case_id} 的 gate stage 不一致。")
        if outcome.split == "release-held-out":
            raise ValueError("V2.1 Intent gate 禁止接收 release-held-out outcome。")
        if (
            outcome.manifest_id,
            outcome.dataset_version,
            outcome.manifest_sha256,
            outcome.taxonomy_sha256,
        ) != expected_identity:
            raise ValueError(f"outcome {outcome.case_id} 的 dataset/hash 身份不一致。")
        key = (outcome.split, outcome.case_id)
        if key in indexed:
            raise ValueError(f"case outcome 重复：{outcome.split}/{outcome.case_id}。")
        indexed[key] = outcome

    expected_keys = {
        *(("development", sample.case_id) for sample in development),
        *(("validation", sample.case_id) for sample in validation),
    }
    actual_keys = set(indexed)
    missing = sorted(expected_keys - actual_keys)
    extra = sorted(actual_keys - expected_keys)
    if missing or extra:
        raise ValueError(f"outcome case 集不闭合；missing={missing} extra={extra}。")

    development_outcomes = {
        sample.case_id: indexed[("development", sample.case_id)]
        for sample in development
    }
    validation_outcomes = {
        sample.case_id: indexed[("validation", sample.case_id)] for sample in validation
    }
    current_legal = _proportion(
        sum(item.intent_valid for item in development_outcomes.values()),
        len(development),
    )
    validation_legal = _proportion(
        sum(item.intent_valid for item in validation_outcomes.values()),
        len(validation),
    )
    instance_exact = _proportion(
        sum(
            outcome.intent_valid
            and outcome.predicted_instance_count == sample.instance_count
            for sample in validation
            for outcome in (validation_outcomes[sample.case_id],)
        ),
        len(validation),
    )
    class_metrics = tuple(
        _class_metric(
            class_id,
            validation,
            validation_outcomes,
        )
        for class_id in _TYPED_CRITICAL_CLASS_IDS
    )
    macro_recall = sum(item.recall.value for item in class_metrics) / len(class_metrics)
    macro_f1 = sum(item.f1 for item in class_metrics) / len(class_metrics)

    minimums = dataset.manifest.critical_class_minimums.as_dict()
    blockers: list[str] = []
    if current_legal.numerator != V2_1_CURRENT_REGRESSION_REQUIRED_COUNT:
        blockers.append(
            "current_10_intent_legal:"
            f"{current_legal.numerator}/{current_legal.denominator}"
        )
    if validation_legal.value < V2_1_VALIDATION_INTENT_LEGAL_MINIMUM:
        blockers.append(
            "validation_intent_legal_below_80_percent:"
            f"{validation_legal.numerator}/{validation_legal.denominator}"
        )
    for metric in class_metrics:
        minimum = minimums[metric.class_id]
        if metric.recall.denominator < minimum:
            blockers.append(
                f"critical_class_denominator:{metric.class_id}:"
                f"{metric.recall.denominator}/{minimum}"
            )
        if metric.recall.value < V2_1_CRITICAL_CLASS_RECALL_MINIMUM:
            blockers.append(
                f"critical_class_recall_below_90_percent:{metric.class_id}:"
                f"{metric.recall.numerator}/{metric.recall.denominator}"
            )

    normalized_outcomes = tuple(
        indexed[key].model_dump(mode="python") for key in sorted(indexed)
    )
    return V2_1IntentGateReport(
        manifest_id=stage_gate.manifest_id,
        dataset_version=stage_gate.dataset_version,
        manifest_sha256=stage_gate.manifest_sha256,
        taxonomy_sha256=stage_gate.taxonomy_sha256,
        outcomes_sha256=canonical_sha256(normalized_outcomes),
        current_10_intent_legal=current_legal,
        validation_intent_legal=validation_legal,
        validation_instance_count_exact=instance_exact,
        critical_class_metrics=class_metrics,
        macro_recall=macro_recall,
        macro_f1=macro_f1,
        ready=not blockers,
        blockers=tuple(blockers),
    )


__all__ = [
    "V2_1_CRITICAL_CLASS_RECALL_MINIMUM",
    "V2_1_CURRENT_REGRESSION_REQUIRED_COUNT",
    "V2_1_INTENT_CASE_OUTCOME_SCHEMA_VERSION",
    "V2_1_INTENT_GATE_REPORT_SCHEMA_VERSION",
    "V2_1_VALIDATION_INTENT_LEGAL_MINIMUM",
    "V2_1IntentCaseOutcome",
    "V2_1IntentClassMetric",
    "V2_1IntentGateReport",
    "V2_1ProportionMetric",
    "WilsonInterval95",
    "evaluate_v2_1_intent_gate",
]
