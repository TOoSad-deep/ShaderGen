"""V2.3 真实模型可见集 validation 的独立报告契约。"""
# ruff: noqa: D415

from __future__ import annotations

from typing import Literal, cast

from pydantic import Field, model_validator

from shaderforge.benchmark.v2_dataset import LoadedV2Dataset, V2DatasetStageGate
from shaderforge.contracts import FrozenModel, NonEmptyString, Sha256Hex
from shaderforge.contracts.canonical import canonical_sha256

V2_3_REAL_CASE_OUTCOME_SCHEMA_VERSION: Literal[
    "v2_3_real_model_validation_case_outcome_v1"
] = "v2_3_real_model_validation_case_outcome_v1"
V2_3_REAL_REPORT_SCHEMA_VERSION: Literal[
    "v2_3_real_model_validation_report_v1"
] = "v2_3_real_model_validation_report_v1"
V2_3_REAL_SPLIT_REPORT_SCHEMA_VERSION: Literal[
    "v2_3_real_model_validation_split_report_v1"
] = "v2_3_real_model_validation_split_report_v1"
V2_3_REAL_EXPECTED_DEVELOPMENT_COUNT = 10
V2_3_REAL_EXPECTED_VALIDATION_COUNT = 41
V2_3_REAL_EXPECTED_CASE_COUNT = 51

V2_3RealFailureCode = Literal[
    "provider_factory_failed",
    "budget_preflight_failed",
    "model_parse_failed",
    "model_output_budget_exceeded",
    "model_interpretation_validation_failed",
    "model_provider_indeterminate",
    "model_identity_failed",
    "model_operation_incomplete",
    "service_budget_exceeded",
    "service_execution_failed",
    "resume_verification_failed",
]


class V2_3RealBudgetV1(FrozenModel):
    """一次 case 或整套 suite 的七维硬预算及 input/output token 拆分。"""

    schema_version: Literal["v2_3_real_model_budget_v1"] = (
        "v2_3_real_model_budget_v1"
    )
    wall_time_ms: int = Field(gt=0)
    model_calls: int = Field(gt=0)
    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    render_calls: int = Field(gt=0)
    candidate_attempts: int = Field(gt=0)
    artifact_bytes: int = Field(gt=0)
    cost_usd_micros: int = Field(gt=0)

    @property
    def model_tokens(self) -> int:
        """返回预算账本使用的模型 token 总上限。"""
        return self.max_input_tokens + self.max_output_tokens

    def scaled(self, count: int) -> V2_3RealBudgetV1:
        """按 case 数扩展为整套最坏情况预算。"""
        if count <= 0:
            raise ValueError("预算扩展 count 必须为正数。")
        return self.model_copy(
            update={
                name: getattr(self, name) * count
                for name in (
                    "wall_time_ms",
                    "model_calls",
                    "max_input_tokens",
                    "max_output_tokens",
                    "render_calls",
                    "candidate_attempts",
                    "artifact_bytes",
                    "cost_usd_micros",
                )
            }
        )

    def covers(self, other: V2_3RealBudgetV1) -> bool:
        """判断每个冻结维度是否均覆盖另一预算。"""
        return all(
            getattr(self, name) >= getattr(other, name)
            for name in (
                "wall_time_ms",
                "model_calls",
                "max_input_tokens",
                "max_output_tokens",
                "render_calls",
                "candidate_attempts",
                "artifact_bytes",
                "cost_usd_micros",
            )
        )


class V2_3RealUsageV1(FrozenModel):
    """从 State 与 durable receipt 恢复的实际/保守用量。"""

    schema_version: Literal["v2_3_real_model_usage_v1"] = (
        "v2_3_real_model_usage_v1"
    )
    wall_time_ms: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    model_tokens: int = Field(ge=0)
    render_calls: int = Field(ge=0)
    candidate_attempts: int = Field(ge=0)
    artifact_bytes: int = Field(ge=0)
    cost_usd_micros: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_tokens(self) -> V2_3RealUsageV1:
        if (self.input_tokens is None) != (self.output_tokens is None):
            raise ValueError("input/output token receipt 必须同时存在或同时缺失。")
        if (
            self.input_tokens is not None
            and self.output_tokens is not None
            and self.model_tokens != self.input_tokens + self.output_tokens
        ):
            raise ValueError("model_tokens 必须等于 input + output token receipt。")
        return self

    def plus(self, other: V2_3RealUsageV1) -> V2_3RealUsageV1:
        """聚合用量；任一 case 缺拆分 receipt 时聚合拆分保持未知。"""
        split_known = self.input_tokens is not None and other.input_tokens is not None
        left_input = self.input_tokens
        right_input = other.input_tokens
        left_output = self.output_tokens
        right_output = other.output_tokens
        return V2_3RealUsageV1(
            wall_time_ms=self.wall_time_ms + other.wall_time_ms,
            model_calls=self.model_calls + other.model_calls,
            input_tokens=(
                cast(int, left_input) + cast(int, right_input)
                if split_known
                else None
            ),
            output_tokens=(
                cast(int, left_output) + cast(int, right_output)
                if split_known
                else None
            ),
            model_tokens=self.model_tokens + other.model_tokens,
            render_calls=self.render_calls + other.render_calls,
            candidate_attempts=self.candidate_attempts + other.candidate_attempts,
            artifact_bytes=self.artifact_bytes + other.artifact_bytes,
            cost_usd_micros=self.cost_usd_micros + other.cost_usd_micros,
        )


def zero_real_usage() -> V2_3RealUsageV1:
    """构造保留 token 拆分的零用量。"""
    return V2_3RealUsageV1(
        wall_time_ms=0,
        model_calls=0,
        input_tokens=0,
        output_tokens=0,
        model_tokens=0,
        render_calls=0,
        candidate_attempts=0,
        artifact_bytes=0,
        cost_usd_micros=0,
    )


class V2_3RealModelIdentityV1(FrozenModel):
    """真实调用前冻结的 provider/model/prompt/pricing 身份。"""

    schema_version: Literal["v2_3_real_model_identity_v1"] = (
        "v2_3_real_model_identity_v1"
    )
    provider_id: NonEmptyString
    model_id: NonEmptyString
    prompt_name: NonEmptyString
    prompt_version: NonEmptyString
    prompt_sha256: Sha256Hex
    pricing_policy_id: NonEmptyString
    pricing_policy_sha256: Sha256Hex


class V2_3RealCaseOutcome(FrozenModel):
    """一个 visible case 经 real Service 与同一 production Graph 的结果。"""

    schema_version: Literal["v2_3_real_model_validation_case_outcome_v1"] = (
        V2_3_REAL_CASE_OUTCOME_SCHEMA_VERSION
    )
    gate_stage: Literal["v2_3_real_model_validation"] = (
        "v2_3_real_model_validation"
    )
    suite_run_id: NonEmptyString
    manifest_id: NonEmptyString
    dataset_version: NonEmptyString
    manifest_sha256: Sha256Hex
    taxonomy_sha256: Sha256Hex
    config_sha256: Sha256Hex
    split: Literal["development", "validation"]
    case_id: NonEmptyString
    run_id: NonEmptyString
    execution_mode: Literal["real"] = "real"
    model_identity: V2_3RealModelIdentityV1
    budget_limit: V2_3RealBudgetV1
    budget_used: V2_3RealUsageV1
    budget_reserved: V2_3RealUsageV1
    success: bool
    failure_code: V2_3RealFailureCode | None
    error_type: NonEmptyString | None
    terminal_phase: NonEmptyString | None
    stop_reason: NonEmptyString | None
    resume_zero_new_charge_verified: bool
    visual_interpretation_sha256: Sha256Hex | None
    request_constraint_set_sha256: Sha256Hex | None
    intent_variant_count: int = Field(ge=0)
    target_structure_branch_count: int = Field(ge=0)
    objective_best_sha256: Sha256Hex | None
    candidate_summary_count: int = Field(ge=0)
    provider_receipt_id: NonEmptyString | None

    @model_validator(mode="after")
    def _validate_outcome(self) -> V2_3RealCaseOutcome:
        if self.success != (self.failure_code is None):
            raise ValueError("real case success 与 failure_code 必须互斥。")
        if self.success and (
            self.terminal_phase != "finalized"
            or not self.resume_zero_new_charge_verified
            or self.visual_interpretation_sha256 is None
            or self.request_constraint_set_sha256 is None
            or self.intent_variant_count < 1
            or self.target_structure_branch_count < 1
            or self.budget_used.model_calls != 1
            or self.budget_reserved.model_calls != 0
            or self.provider_receipt_id is None
        ):
            raise ValueError("成功 real case 缺少 Service/Intent/Graph/receipt 闭包。")
        if not self.success and self.error_type is None:
            raise ValueError("失败 real case 必须保留安全 error_type。")
        limits = {
            "wall_time_ms": self.budget_limit.wall_time_ms,
            "model_calls": self.budget_limit.model_calls,
            "model_tokens": self.budget_limit.model_tokens,
            "render_calls": self.budget_limit.render_calls,
            "candidate_attempts": self.budget_limit.candidate_attempts,
            "artifact_bytes": self.budget_limit.artifact_bytes,
            "cost_usd_micros": self.budget_limit.cost_usd_micros,
        }
        for name, limit in limits.items():
            if (
                getattr(self.budget_used, name)
                + getattr(self.budget_reserved, name)
                > limit
            ):
                raise ValueError(f"real case {name} 用量超过调用前硬预算。")
        for name in ("input_tokens", "output_tokens"):
            actual = getattr(self.budget_used, name)
            limit = getattr(self.budget_limit, f"max_{name}")
            if actual is not None and actual > limit:
                raise ValueError(f"real case {name} receipt 超过硬预算。")
        return self


class V2_3RealSplitReport(FrozenModel):
    """development 或 validation 的完整失败分母。"""

    schema_version: Literal["v2_3_real_model_validation_split_report_v1"] = (
        V2_3_REAL_SPLIT_REPORT_SCHEMA_VERSION
    )
    split: Literal["development", "validation"]
    case_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    resume_verified_count: int = Field(ge=0)
    usage: V2_3RealUsageV1
    reserved: V2_3RealUsageV1

    @model_validator(mode="after")
    def _validate_counts(self) -> V2_3RealSplitReport:
        if self.success_count + self.failure_count != self.case_count:
            raise ValueError("split success/failure 必须保留完整分母。")
        if self.resume_verified_count > self.case_count:
            raise ValueError("resume verified 不得超过 case 分母。")
        return self


class V2_3RealModelValidationReport(FrozenModel):
    """visible 10+41 真实模型验证报告；永不表示 release 或 VLM 质量通过。"""

    schema_version: Literal["v2_3_real_model_validation_report_v1"] = (
        V2_3_REAL_REPORT_SCHEMA_VERSION
    )
    gate_stage: Literal["v2_3_real_model_validation"] = (
        "v2_3_real_model_validation"
    )
    suite_run_id: NonEmptyString
    manifest_id: NonEmptyString
    dataset_version: NonEmptyString
    manifest_sha256: Sha256Hex
    taxonomy_sha256: Sha256Hex
    config_sha256: Sha256Hex
    outcomes_sha256: Sha256Hex
    model_identity: V2_3RealModelIdentityV1
    case_budget: V2_3RealBudgetV1
    suite_budget: V2_3RealBudgetV1
    development: V2_3RealSplitReport
    validation: V2_3RealSplitReport
    case_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    usage: V2_3RealUsageV1
    reserved: V2_3RealUsageV1
    visible_validation_complete: bool
    production_admission_enabled: Literal[False] = False
    release_ready: Literal[False] = False
    vlm_quality_claim: Literal["not_evaluated"] = "not_evaluated"
    release_held_out_accessed: Literal[False] = False
    report_sha256: Sha256Hex

    @model_validator(mode="after")
    def _validate_report(self) -> V2_3RealModelValidationReport:
        if self.development.split != "development":
            raise ValueError("development 字段 split 错绑。")
        if self.validation.split != "validation":
            raise ValueError("validation 字段 split 错绑。")
        if self.case_count != self.development.case_count + self.validation.case_count:
            raise ValueError("report case_count 与 split 分母不闭合。")
        if self.success_count + self.failure_count != self.case_count:
            raise ValueError("report success/failure 必须保留完整分母。")
        if self.success_count != (
            self.development.success_count + self.validation.success_count
        ) or self.failure_count != (
            self.development.failure_count + self.validation.failure_count
        ):
            raise ValueError("report success/failure 与 split 聚合不一致。")
        if self.usage != self.development.usage.plus(self.validation.usage):
            raise ValueError("report usage 与 split 聚合不一致。")
        if self.reserved != self.development.reserved.plus(
            self.validation.reserved
        ):
            raise ValueError("report reserved 与 split 聚合不一致。")
        limits = {
            "wall_time_ms": self.suite_budget.wall_time_ms,
            "model_calls": self.suite_budget.model_calls,
            "model_tokens": self.suite_budget.model_tokens,
            "render_calls": self.suite_budget.render_calls,
            "candidate_attempts": self.suite_budget.candidate_attempts,
            "artifact_bytes": self.suite_budget.artifact_bytes,
            "cost_usd_micros": self.suite_budget.cost_usd_micros,
        }
        for name, limit in limits.items():
            if getattr(self.usage, name) + getattr(self.reserved, name) > limit:
                raise ValueError(f"real suite {name} 用量超过调用前硬预算。")
        expected_complete = (
            self.development.case_count == V2_3_REAL_EXPECTED_DEVELOPMENT_COUNT
            and self.validation.case_count == V2_3_REAL_EXPECTED_VALIDATION_COUNT
            and self.case_count == V2_3_REAL_EXPECTED_CASE_COUNT
        )
        if self.visible_validation_complete != expected_complete:
            raise ValueError("visible validation complete 与 10+41 分母不一致。")
        if self.report_sha256 != compute_v2_3_real_report_sha256(self):
            raise ValueError("real validation report_sha256 不一致。")
        return self


def compute_v2_3_real_report_sha256(
    report: V2_3RealModelValidationReport | dict[str, object],
) -> str:
    """计算排除自身 hash 字段的报告身份。"""
    if isinstance(report, V2_3RealModelValidationReport):
        payload = report.model_dump(mode="python", exclude={"report_sha256"})
    else:
        payload = dict(report)
        payload.pop("report_sha256", None)
    return canonical_sha256(payload)


def _validate_dataset_identity(
    dataset: LoadedV2Dataset, stage_gate: V2DatasetStageGate
) -> None:
    if dataset.gate_stage != "v2_3_graph_conformance":
        raise ValueError("real validation 只接受 visible V2.3 Graph 数据集。")
    if (
        stage_gate.stage != "v2_3_graph_conformance"
        or stage_gate.required_splits != ("validation",)
        or not stage_gate.ready
        or stage_gate.blockers
    ):
        raise ValueError("real validation 只接受已通过的 visible StageGate。")
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
        raise ValueError("real validation StageGate 与 dataset identity 不一致。")
    release = dataset.manifest.split("release-held-out")
    if release.status != "not_populated" or release.samples:
        raise ValueError("real validation 禁止读取或填充 release-held-out。")


def _sum_usage(outcomes: tuple[V2_3RealCaseOutcome, ...]) -> V2_3RealUsageV1:
    usage = zero_real_usage()
    for outcome in outcomes:
        usage = usage.plus(outcome.budget_used)
    return usage


def _sum_reserved(outcomes: tuple[V2_3RealCaseOutcome, ...]) -> V2_3RealUsageV1:
    reserved = zero_real_usage()
    for outcome in outcomes:
        reserved = reserved.plus(outcome.budget_reserved)
    return reserved


def _split_report(
    split: Literal["development", "validation"],
    outcomes: tuple[V2_3RealCaseOutcome, ...],
) -> V2_3RealSplitReport:
    return V2_3RealSplitReport(
        split=split,
        case_count=len(outcomes),
        success_count=sum(item.success for item in outcomes),
        failure_count=sum(not item.success for item in outcomes),
        resume_verified_count=sum(
            item.resume_zero_new_charge_verified for item in outcomes
        ),
        usage=_sum_usage(outcomes),
        reserved=_sum_reserved(outcomes),
    )


def evaluate_v2_3_real_model_validation(
    dataset: LoadedV2Dataset,
    stage_gate: V2DatasetStageGate,
    outcomes: tuple[V2_3RealCaseOutcome, ...],
    *,
    suite_run_id: str,
    config_sha256: str,
    model_identity: V2_3RealModelIdentityV1,
    case_budget: V2_3RealBudgetV1,
    suite_budget: V2_3RealBudgetV1,
) -> V2_3RealModelValidationReport:
    """严格聚合 visible 10+41；失败保留分母且 release 结论恒为 false。"""
    _validate_dataset_identity(dataset, stage_gate)
    development_samples = tuple(
        sample
        for sample in dataset.manifest.split("development").samples
        if sample.dataset_role == "regression"
        and sample.source_suite_id == "png_to_shader_v1_m0"
    )
    validation_samples = dataset.manifest.split("validation").samples
    expected = tuple(
        (split, sample.case_id)
        for split, samples in (
            ("development", development_samples),
            ("validation", validation_samples),
        )
        for sample in samples
    )
    actual = tuple((item.split, item.case_id) for item in outcomes)
    if len(set(actual)) != len(actual) or actual != expected:
        raise ValueError("real validation outcomes 必须与 visible 10+41 顺序/集合一致。")
    run_ids = tuple(item.run_id for item in outcomes)
    if len(set(run_ids)) != len(run_ids):
        raise ValueError("real validation 每个 case 必须绑定唯一 run_id。")
    if len(development_samples) != 10 or len(validation_samples) != 41:
        raise ValueError("real validation 固定要求 development 10 + validation 41。")
    for outcome in outcomes:
        if (
            outcome.suite_run_id != suite_run_id
            or outcome.manifest_id != stage_gate.manifest_id
            or outcome.dataset_version != stage_gate.dataset_version
            or outcome.manifest_sha256 != stage_gate.manifest_sha256
            or outcome.taxonomy_sha256 != stage_gate.taxonomy_sha256
            or outcome.config_sha256 != config_sha256
            or outcome.model_identity != model_identity
            or outcome.budget_limit != case_budget
        ):
            raise ValueError("real validation case outcome identity 错绑。")
    required_suite_budget = case_budget.scaled(len(outcomes))
    if not suite_budget.covers(required_suite_budget):
        raise ValueError("suite budget 未覆盖 51 case 的调用前最坏情况。")
    development = tuple(item for item in outcomes if item.split == "development")
    validation = tuple(item for item in outcomes if item.split == "validation")
    dev_report = _split_report("development", development)
    val_report = _split_report("validation", validation)
    usage = _sum_usage(outcomes)
    report_values: dict[str, object] = {
        "schema_version": V2_3_REAL_REPORT_SCHEMA_VERSION,
        "gate_stage": "v2_3_real_model_validation",
        "suite_run_id": suite_run_id,
        "manifest_id": stage_gate.manifest_id,
        "dataset_version": stage_gate.dataset_version,
        "manifest_sha256": stage_gate.manifest_sha256,
        "taxonomy_sha256": stage_gate.taxonomy_sha256,
        "config_sha256": config_sha256,
        "outcomes_sha256": canonical_sha256(
            [item.model_dump(mode="json") for item in outcomes]
        ),
        "model_identity": model_identity,
        "case_budget": case_budget,
        "suite_budget": suite_budget,
        "development": dev_report,
        "validation": val_report,
        "case_count": len(outcomes),
        "success_count": sum(item.success for item in outcomes),
        "failure_count": sum(not item.success for item in outcomes),
        "usage": usage,
        "reserved": _sum_reserved(outcomes),
        "visible_validation_complete": True,
        "production_admission_enabled": False,
        "release_ready": False,
        "vlm_quality_claim": "not_evaluated",
        "release_held_out_accessed": False,
    }
    report_values["report_sha256"] = compute_v2_3_real_report_sha256(report_values)
    return V2_3RealModelValidationReport.model_validate(report_values, strict=True)


__all__ = [
    "V2_3_REAL_CASE_OUTCOME_SCHEMA_VERSION",
    "V2_3_REAL_EXPECTED_CASE_COUNT",
    "V2_3_REAL_EXPECTED_DEVELOPMENT_COUNT",
    "V2_3_REAL_EXPECTED_VALIDATION_COUNT",
    "V2_3_REAL_REPORT_SCHEMA_VERSION",
    "V2_3RealBudgetV1",
    "V2_3RealCaseOutcome",
    "V2_3RealFailureCode",
    "V2_3RealModelIdentityV1",
    "V2_3RealModelValidationReport",
    "V2_3RealSplitReport",
    "V2_3RealUsageV1",
    "compute_v2_3_real_report_sha256",
    "evaluate_v2_3_real_model_validation",
    "zero_real_usage",
]
