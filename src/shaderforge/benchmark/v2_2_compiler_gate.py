"""V2.2 三 Genome 与确定性 Compiler 的严格纯聚合门禁。."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from shaderforge.benchmark.v2_dataset import (
    LoadedV2Dataset,
    V2DatasetStageGate,
)
from shaderforge.contracts import FrozenModel, NonEmptyString, Sha256Hex
from shaderforge.contracts.canonical import canonical_sha256

V2_2_COMPILER_CASE_OUTCOME_SCHEMA_VERSION: Literal["v2_2_compiler_case_outcome_v1"] = (
    "v2_2_compiler_case_outcome_v1"
)
V2_2_COMPILER_GATE_REPORT_SCHEMA_VERSION: Literal["v2_2_compiler_gate_report_v1"] = (
    "v2_2_compiler_gate_report_v1"
)
V2_2_EXPECTED_CASE_COUNT = 51
V2_2_GENOMES_PER_INTENT = 3

V2_2FailureCode = Literal[
    "input_intent_unavailable",
    "seed_expansion_failed",
    "typed_genome_invalid",
    "semantic_genome_hash_not_unique",
    "structural_diversity_failed",
    "deterministic_compile_failed",
    "deterministic_compile_mismatch",
    "static_validation_failed",
    "webgl_compile_or_draw_failed",
    "webgl_renderer_unavailable",
]


class V2_2CountMetric(FrozenModel):
    """保留失败分母的精确计数。."""

    numerator: int = Field(ge=0)
    denominator: int = Field(ge=1)

    @model_validator(mode="after")
    def _validate_count(self) -> V2_2CountMetric:
        if self.numerator > self.denominator:
            raise ValueError("计数 numerator 不得超过 denominator。")
        return self

    @property
    def value(self) -> float:
        """返回精确计数对应的比例。."""
        return self.numerator / self.denominator


class V2_2CompilerCaseOutcome(FrozenModel):
    """一个冻结 Intent 的三 Genome/Compiler 真实执行结果。."""

    schema_version: Literal["v2_2_compiler_case_outcome_v1"] = (
        V2_2_COMPILER_CASE_OUTCOME_SCHEMA_VERSION
    )
    gate_stage: Literal["v2_2_genome_compiler"] = "v2_2_genome_compiler"
    manifest_id: NonEmptyString
    dataset_version: NonEmptyString
    manifest_sha256: Sha256Hex
    taxonomy_sha256: Sha256Hex
    config_sha256: Sha256Hex
    input_intent_outcomes_sha256: Sha256Hex
    split: Literal["development", "validation", "release-held-out"]
    case_id: NonEmptyString
    success: bool
    genome_count: int = Field(ge=0, le=V2_2_GENOMES_PER_INTENT)
    semantic_genome_hashes: tuple[Sha256Hex, ...] = Field(
        max_length=V2_2_GENOMES_PER_INTENT
    )
    distinct_structural_signatures: int = Field(ge=0, le=V2_2_GENOMES_PER_INTENT)
    diversity_gate_passed: bool
    deterministic_compile_success_count: int = Field(ge=0, le=V2_2_GENOMES_PER_INTENT)
    static_validation_success_count: int = Field(ge=0, le=V2_2_GENOMES_PER_INTENT)
    webgl_requested: bool
    webgl_success_count: int | None = Field(
        default=None, ge=0, le=V2_2_GENOMES_PER_INTENT
    )
    failure_code: V2_2FailureCode | None = None

    @model_validator(mode="after")
    def _validate_outcome(self) -> V2_2CompilerCaseOutcome:
        if len(self.semantic_genome_hashes) != self.genome_count:
            raise ValueError("semantic hash 数必须与真实 Genome 数一致。")
        if self.deterministic_compile_success_count > self.genome_count:
            raise ValueError("编译成功数不得超过 Genome 数。")
        if (
            self.static_validation_success_count
            > self.deterministic_compile_success_count
        ):
            raise ValueError("静态校验成功数不得超过确定性编译成功数。")
        if self.diversity_gate_passed and (
            self.genome_count != V2_2_GENOMES_PER_INTENT
            or len(set(self.semantic_genome_hashes)) != V2_2_GENOMES_PER_INTENT
            or self.distinct_structural_signatures < 2
        ):
            raise ValueError("diversity gate 通过但 semantic/结构证据不闭合。")
        if self.webgl_requested != (self.webgl_success_count is not None):
            raise ValueError("WebGL 请求状态与成功计数必须一致。")
        if self.webgl_success_count is not None and (
            self.webgl_success_count > self.static_validation_success_count
        ):
            raise ValueError("WebGL 成功数不得超过静态校验成功数。")

        fully_successful = (
            self.genome_count == V2_2_GENOMES_PER_INTENT
            and len(set(self.semantic_genome_hashes)) == V2_2_GENOMES_PER_INTENT
            and self.distinct_structural_signatures >= 2
            and self.diversity_gate_passed
            and self.deterministic_compile_success_count == V2_2_GENOMES_PER_INTENT
            and self.static_validation_success_count == V2_2_GENOMES_PER_INTENT
            and (
                self.webgl_success_count == V2_2_GENOMES_PER_INTENT
                if self.webgl_requested
                else True
            )
        )
        if self.success != fully_successful:
            raise ValueError("success 与 Genome/Compiler/WebGL 真实证据不一致。")
        if self.success != (self.failure_code is None):
            raise ValueError("success 与 failure_code 必须互斥。")
        return self


class V2_2CompilerGateReport(FrozenModel):
    """完整 51 Intent outcome 闭包的 V2.2 聚合报告。."""

    schema_version: Literal["v2_2_compiler_gate_report_v1"] = (
        V2_2_COMPILER_GATE_REPORT_SCHEMA_VERSION
    )
    gate_stage: Literal["v2_2_genome_compiler"] = "v2_2_genome_compiler"
    manifest_id: NonEmptyString
    dataset_version: NonEmptyString
    manifest_sha256: Sha256Hex
    taxonomy_sha256: Sha256Hex
    config_sha256: Sha256Hex
    input_intent_outcomes_sha256: Sha256Hex
    outcomes_sha256: Sha256Hex
    cases_passed: V2_2CountMetric
    legal_genomes: V2_2CountMetric
    unique_semantic_hash_cases: V2_2CountMetric
    structurally_diverse_cases: V2_2CountMetric
    deterministic_compiles: V2_2CountMetric
    static_validations: V2_2CountMetric
    webgl_requested: bool
    webgl_compiles_and_draws: V2_2CountMetric | None
    ready: bool
    blockers: tuple[NonEmptyString, ...]

    @model_validator(mode="after")
    def _validate_report(self) -> V2_2CompilerGateReport:
        if self.webgl_requested != (self.webgl_compiles_and_draws is not None):
            raise ValueError("报告不得伪造未执行的 WebGL 指标。")
        if self.ready != (not self.blockers):
            raise ValueError("V2.2 gate ready 与 blockers 不一致。")
        return self


def _metric(numerator: int, denominator: int) -> V2_2CountMetric:
    return V2_2CountMetric(numerator=numerator, denominator=denominator)


def _validate_dataset_gate_identity(
    dataset: LoadedV2Dataset,
    stage_gate: V2DatasetStageGate,
) -> None:
    if dataset.gate_stage != "v2_2_genome_compiler":
        raise ValueError("Compiler report 只接受 V2.2 gate_stage 数据集。")
    if stage_gate.stage != "v2_2_genome_compiler":
        raise ValueError("Compiler report 只接受 V2.2 StageGate。")
    if not stage_gate.ready or stage_gate.blockers:
        raise ValueError("V2.2 StageGate 未通过，不得聚合 Compiler 结果。")
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
        raise ValueError("StageGate 与 LoadedV2Dataset 内容身份不一致。")


def evaluate_v2_2_compiler_gate(
    dataset: LoadedV2Dataset,
    stage_gate: V2DatasetStageGate,
    outcomes: tuple[V2_2CompilerCaseOutcome, ...],
    *,
    config_sha256: str,
    input_intent_outcomes_sha256: str,
    webgl_requested: bool,
) -> V2_2CompilerGateReport:
    """聚合完整 10+41 outcomes；缺失、重复、额外/release 一律拒绝。."""
    _validate_dataset_gate_identity(dataset, stage_gate)
    development = tuple(
        sample
        for sample in dataset.manifest.split("development").samples
        if sample.dataset_role == "regression"
        and sample.source_suite_id == "png_to_shader_v1_m0"
    )
    validation = dataset.manifest.split("validation").samples
    if len(development) != 10 or len(validation) != 41:
        raise ValueError("V2.2 gate 要求冻结 development 10 + validation 41。")

    expected_identity = (
        stage_gate.manifest_id,
        stage_gate.dataset_version,
        stage_gate.manifest_sha256,
        stage_gate.taxonomy_sha256,
        config_sha256,
        input_intent_outcomes_sha256,
    )
    indexed: dict[tuple[str, str], V2_2CompilerCaseOutcome] = {}
    for outcome in outcomes:
        if outcome.split == "release-held-out":
            raise ValueError("V2.2 gate 禁止接收 release-held-out outcome。")
        if (
            outcome.manifest_id,
            outcome.dataset_version,
            outcome.manifest_sha256,
            outcome.taxonomy_sha256,
            outcome.config_sha256,
            outcome.input_intent_outcomes_sha256,
        ) != expected_identity:
            raise ValueError(f"outcome {outcome.case_id} 的身份/hash 不一致。")
        if outcome.webgl_requested != webgl_requested:
            raise ValueError(f"outcome {outcome.case_id} 的 WebGL 模式不一致。")
        key = (outcome.split, outcome.case_id)
        if key in indexed:
            raise ValueError(f"case outcome 重复：{outcome.split}/{outcome.case_id}。")
        indexed[key] = outcome

    expected_keys = {
        *(("development", item.case_id) for item in development),
        *(("validation", item.case_id) for item in validation),
    }
    missing = sorted(expected_keys - set(indexed))
    extra = sorted(set(indexed) - expected_keys)
    if missing or extra:
        raise ValueError(f"outcome case 集不闭合；missing={missing} extra={extra}。")

    ordered = tuple(indexed[key] for key in sorted(indexed))
    case_denominator = len(ordered)
    genome_denominator = case_denominator * V2_2_GENOMES_PER_INTENT
    cases_passed = _metric(sum(item.success for item in ordered), case_denominator)
    legal_genomes = _metric(
        sum(item.genome_count for item in ordered), genome_denominator
    )
    unique_cases = _metric(
        sum(
            len(item.semantic_genome_hashes) == V2_2_GENOMES_PER_INTENT
            and len(set(item.semantic_genome_hashes)) == V2_2_GENOMES_PER_INTENT
            for item in ordered
        ),
        case_denominator,
    )
    diverse_cases = _metric(
        sum(item.diversity_gate_passed for item in ordered), case_denominator
    )
    deterministic_compiles = _metric(
        sum(item.deterministic_compile_success_count for item in ordered),
        genome_denominator,
    )
    static_validations = _metric(
        sum(item.static_validation_success_count for item in ordered),
        genome_denominator,
    )
    webgl = (
        _metric(
            sum(item.webgl_success_count or 0 for item in ordered),
            genome_denominator,
        )
        if webgl_requested
        else None
    )

    blockers: list[str] = []
    for code, metric in (
        ("case_failures", cases_passed),
        ("legal_genomes", legal_genomes),
        ("unique_semantic_hash_cases", unique_cases),
        ("structural_diversity_cases", diverse_cases),
        ("deterministic_compile_failures", deterministic_compiles),
        ("static_validation_failures", static_validations),
    ):
        if metric.numerator != metric.denominator:
            blockers.append(f"{code}:{metric.numerator}/{metric.denominator}")
    if webgl is not None and webgl.numerator != webgl.denominator:
        blockers.append(
            f"webgl_compile_or_draw_failures:{webgl.numerator}/{webgl.denominator}"
        )

    return V2_2CompilerGateReport(
        manifest_id=stage_gate.manifest_id,
        dataset_version=stage_gate.dataset_version,
        manifest_sha256=stage_gate.manifest_sha256,
        taxonomy_sha256=stage_gate.taxonomy_sha256,
        config_sha256=config_sha256,
        input_intent_outcomes_sha256=input_intent_outcomes_sha256,
        outcomes_sha256=canonical_sha256(
            tuple(item.model_dump(mode="python") for item in ordered)
        ),
        cases_passed=cases_passed,
        legal_genomes=legal_genomes,
        unique_semantic_hash_cases=unique_cases,
        structurally_diverse_cases=diverse_cases,
        deterministic_compiles=deterministic_compiles,
        static_validations=static_validations,
        webgl_requested=webgl_requested,
        webgl_compiles_and_draws=webgl,
        ready=not blockers,
        blockers=tuple(blockers),
    )


__all__ = [
    "V2_2_COMPILER_CASE_OUTCOME_SCHEMA_VERSION",
    "V2_2_COMPILER_GATE_REPORT_SCHEMA_VERSION",
    "V2_2_EXPECTED_CASE_COUNT",
    "V2_2_GENOMES_PER_INTENT",
    "V2_2CompilerCaseOutcome",
    "V2_2CompilerGateReport",
    "V2_2CountMetric",
    "V2_2FailureCode",
    "evaluate_v2_2_compiler_gate",
]
