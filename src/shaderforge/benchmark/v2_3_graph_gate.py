"""V2.3 development/validation Graph conformance 的严格纯聚合门禁。."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from shaderforge.benchmark.v2_dataset import LoadedV2Dataset, V2DatasetStageGate
from shaderforge.contracts import FrozenModel, NonEmptyString, Sha256Hex
from shaderforge.contracts.canonical import canonical_sha256

V2_3_GRAPH_CASE_OUTCOME_SCHEMA_VERSION: Literal["v2_3_graph_case_outcome_v2"] = (
    "v2_3_graph_case_outcome_v2"
)
V2_3_GRAPH_GATE_REPORT_SCHEMA_VERSION: Literal["v2_3_graph_gate_report_v2"] = (
    "v2_3_graph_gate_report_v2"
)
V2_3_GRAPH_SPLIT_REPORT_SCHEMA_VERSION: Literal["v2_3_graph_split_report_v2"] = (
    "v2_3_graph_split_report_v2"
)
V2_3_EXPECTED_CASE_COUNT = 51
V2_3_EXPECTED_DEVELOPMENT_COUNT = 10
V2_3_EXPECTED_VALIDATION_COUNT = 41
V2_3_SEED_ATTEMPTS_PER_CASE = 3

V2_3GraphFailureCode = Literal[
    "graph_execution_failed",
    "terminal_state_incomplete",
    "seed_attempt_count_mismatch",
    "artifact_closure_failed",
    "hypothesis_identity_mismatch",
    "checkpoint_restart_failed",
    "deterministic_replay_mismatch",
    "cas_evidence_missing",
    "production_admission_enabled",
    "model_calls_nonzero",
    "unsupported_classification_mismatch",
]

V2_3TerminalClass = Literal[
    "objective_best",
    "unsupported_no_valid_candidate",
]

V2_3RestartPhase = Literal[
    "measured",
    "interpreted",
    "seeding",
    "compiled",
    "rendered",
    "evaluated",
    "materialized",
    "selected",
]
V2_3_RESTART_PHASES: tuple[V2_3RestartPhase, ...] = (
    "measured",
    "interpreted",
    "seeding",
    "compiled",
    "rendered",
    "evaluated",
    "materialized",
    "selected",
)


class V2_3CountMetric(FrozenModel):
    """保留失败分母的精确计数。."""

    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_count(self) -> V2_3CountMetric:
        if self.numerator > self.denominator:
            raise ValueError("计数 numerator 不得超过 denominator。")
        return self

    @property
    def value(self) -> float | None:
        """返回精确比例。."""
        return None if self.denominator == 0 else self.numerator / self.denominator


class V2_3RestartPhaseOutcome(FrozenModel):
    """一次真实中间 checkpoint 崩溃、重建依赖并恢复到终态的证据。."""

    schema_version: Literal["v2_3_restart_phase_outcome_v2"] = (
        "v2_3_restart_phase_outcome_v2"
    )
    phase: V2_3RestartPhase
    verified: bool
    crash_state_projection_sha256: Sha256Hex | None
    uninterrupted_final_state_sha256: Sha256Hex | None
    resumed_final_state_sha256: Sha256Hex | None
    side_effect_counts_match: bool
    budget_match: bool
    artifact_closure_match: bool
    cursor_match: bool
    evaluation_revision_match: bool

    @model_validator(mode="after")
    def _validate_restart(self) -> V2_3RestartPhaseOutcome:
        fully_verified = (
            self.crash_state_projection_sha256 is not None
            and self.uninterrupted_final_state_sha256 is not None
            and self.resumed_final_state_sha256
            == self.uninterrupted_final_state_sha256
            and self.side_effect_counts_match
            and self.budget_match
            and self.artifact_closure_match
            and self.cursor_match
            and self.evaluation_revision_match
        )
        if self.verified != fully_verified:
            raise ValueError("restart verified 与逐项恢复证据不一致。")
        return self


class V2_3RestartPhaseMetric(FrozenModel):
    """按逻辑 phase 聚合的 restart 精确分子/分母。."""

    phase: V2_3RestartPhase
    recoveries: V2_3CountMetric


class V2_3GraphCaseOutcome(FrozenModel):
    """一个冻结输入经 production V2 Graph Builder 执行后的证据摘要。."""

    schema_version: Literal["v2_3_graph_case_outcome_v2"] = (
        V2_3_GRAPH_CASE_OUTCOME_SCHEMA_VERSION
    )
    gate_stage: Literal["v2_3_graph_conformance"] = "v2_3_graph_conformance"
    manifest_id: NonEmptyString
    dataset_version: NonEmptyString
    manifest_sha256: Sha256Hex
    taxonomy_sha256: Sha256Hex
    config_sha256: Sha256Hex
    input_intent_outcomes_sha256: Sha256Hex
    input_compiler_outcomes_sha256: Sha256Hex
    split: Literal["development", "validation", "release-held-out"]
    case_id: NonEmptyString
    success: bool
    expected_terminal_class: V2_3TerminalClass
    supported_hypothesis_count: int = Field(ge=0)
    unsupported_hypothesis_count: int = Field(ge=0)
    hypothesis_capability_evidence_sha256: Sha256Hex
    terminal_phase: NonEmptyString | None
    stop_reason: NonEmptyString | None
    final_state_sha256: Sha256Hex | None
    replay_final_state_sha256: Sha256Hex | None
    expected_seed_attempt_count: int = Field(ge=0)
    seed_attempt_count: int = Field(ge=0)
    attempt_artifact_closure_count: int = Field(ge=0)
    successful_candidate_count: int = Field(ge=0)
    branch_best_count: int = Field(ge=0)
    unsupported_attempt_count: int = Field(ge=0)
    unsupported_reason_codes: tuple[NonEmptyString, ...]
    unsupported_classification_verified: bool
    artifact_manifest_sha256: Sha256Hex | None
    hypothesis_count: int = Field(ge=0)
    hypothesis_ids: tuple[NonEmptyString, ...]
    hypothesis_hashes: tuple[Sha256Hex, ...]
    hypothesis_identity_propagated: bool
    restart_phase_results: tuple[V2_3RestartPhaseOutcome, ...] = ()
    deterministic_replay_verified: bool
    cas_stale_write_rejected: bool
    production_admission_enabled: bool
    model_calls: int = Field(ge=0)
    failure_code: V2_3GraphFailureCode | None = None

    @model_validator(mode="after")
    def _validate_outcome(self) -> V2_3GraphCaseOutcome:
        if len(self.hypothesis_ids) != self.hypothesis_count:
            raise ValueError("hypothesis id 数必须与 hypothesis_count 一致。")
        if len(self.hypothesis_hashes) != self.hypothesis_count:
            raise ValueError("hypothesis hash 数必须与 hypothesis_count 一致。")
        if len(set(self.hypothesis_ids)) != len(self.hypothesis_ids):
            raise ValueError("hypothesis id 不得重复。")
        if len(set(self.hypothesis_hashes)) != len(self.hypothesis_hashes):
            raise ValueError("hypothesis hash 不得重复。")
        restart_phases = tuple(item.phase for item in self.restart_phase_results)
        if len(set(restart_phases)) != len(restart_phases):
            raise ValueError("同一 case 的 restart phase 不得重复。")
        if (
            self.supported_hypothesis_count + self.unsupported_hypothesis_count
            != self.hypothesis_count
        ):
            raise ValueError("supported/unsupported hypothesis 数必须闭合。")
        derived_terminal: V2_3TerminalClass = (
            "objective_best"
            if self.supported_hypothesis_count > 0
            else "unsupported_no_valid_candidate"
        )
        if self.expected_terminal_class != derived_terminal:
            raise ValueError("expected terminal 必须由实际 branch capability 推导。")
        if self.hypothesis_identity_propagated and self.hypothesis_count < 1:
            raise ValueError("hypothesis identity 通过必须有至少一个真实 hypothesis。")
        if self.expected_seed_attempt_count != (
            self.hypothesis_count * V2_3_SEED_ATTEMPTS_PER_CASE
        ):
            raise ValueError("expected seed attempt 数必须等于 hypothesis_count × 3。")
        if self.seed_attempt_count > self.expected_seed_attempt_count:
            raise ValueError("实际 seed attempt 数不得超过该 case 的期望分母。")
        if self.attempt_artifact_closure_count > self.expected_seed_attempt_count:
            raise ValueError("Attempt Artifact 闭包数不得超过该 case 的期望分母。")
        if self.successful_candidate_count > self.attempt_artifact_closure_count:
            raise ValueError("成功 Candidate 数不得超过闭合 Attempt 数。")
        if self.branch_best_count > self.hypothesis_count:
            raise ValueError("branch best 数不得超过 hypothesis 数。")
        if self.unsupported_attempt_count > self.attempt_artifact_closure_count:
            raise ValueError("unsupported Attempt 数不得超过闭合 Attempt 数。")
        if self.deterministic_replay_verified and (
            self.final_state_sha256 is None
            or self.replay_final_state_sha256 != self.final_state_sha256
        ):
            raise ValueError("deterministic replay 通过但最终 State hash 不一致。")

        base_successful = (
            self.terminal_phase == "finalized"
            and self.final_state_sha256 is not None
            and self.replay_final_state_sha256 == self.final_state_sha256
            and self.seed_attempt_count == self.expected_seed_attempt_count
            and self.attempt_artifact_closure_count == self.expected_seed_attempt_count
            and self.artifact_manifest_sha256 is not None
            and self.hypothesis_count >= 1
            and self.hypothesis_identity_propagated
            and self.deterministic_replay_verified
            and self.cas_stale_write_rejected
            and not self.production_admission_enabled
            and self.model_calls == 0
        )
        objective_best = (
            self.expected_terminal_class == "objective_best"
            and self.stop_reason == "completed_with_objective_best"
            and self.successful_candidate_count >= self.supported_hypothesis_count
            and self.branch_best_count == self.supported_hypothesis_count
            and self.unsupported_attempt_count
            == self.unsupported_hypothesis_count * V2_3_SEED_ATTEMPTS_PER_CASE
            and (
                self.unsupported_hypothesis_count == 0
                or (
                    bool(self.unsupported_reason_codes)
                    and self.unsupported_classification_verified
                )
            )
        )
        unsupported = (
            self.expected_terminal_class == "unsupported_no_valid_candidate"
            and self.stop_reason == "no_valid_candidate"
            and self.successful_candidate_count == 0
            and self.branch_best_count == 0
            and self.unsupported_attempt_count == self.expected_seed_attempt_count
            and bool(self.unsupported_reason_codes)
            and self.unsupported_classification_verified
        )
        fully_successful = base_successful and (objective_best or unsupported)
        if self.success != fully_successful:
            raise ValueError("success 与 Graph/Artifact/restart/CAS 真实证据不一致。")
        if self.success != (self.failure_code is None):
            raise ValueError("success 与 failure_code 必须互斥。")
        return self


class V2_3GraphSplitReport(FrozenModel):
    """development 或 validation 的独立 Graph conformance 分母。."""

    schema_version: Literal["v2_3_graph_split_report_v2"] = (
        V2_3_GRAPH_SPLIT_REPORT_SCHEMA_VERSION
    )
    split: Literal["development", "validation"]
    cases_passed: V2_3CountMetric
    finalized_states: V2_3CountMetric
    seed_attempts: V2_3CountMetric
    attempt_artifact_closures: V2_3CountMetric
    successful_candidates: V2_3CountMetric
    hypothesis_branch_bests: V2_3CountMetric
    objective_best_cases: V2_3CountMetric
    expected_unsupported_no_candidate_cases: V2_3CountMetric
    unsupported_attempts: V2_3CountMetric
    hypothesis_identity_propagations: V2_3CountMetric
    restart_phase_recoveries: tuple[V2_3RestartPhaseMetric, ...]
    deterministic_replays: V2_3CountMetric
    cas_stale_write_rejections: V2_3CountMetric
    production_admission_disabled: V2_3CountMetric
    zero_model_call_cases: V2_3CountMetric
    ready: bool
    blockers: tuple[NonEmptyString, ...]

    @model_validator(mode="after")
    def _validate_report(self) -> V2_3GraphSplitReport:
        if tuple(item.phase for item in self.restart_phase_recoveries) != (
            V2_3_RESTART_PHASES
        ):
            raise ValueError("split restart matrix 必须逐 phase 完整且顺序冻结。")
        if self.ready != (not self.blockers):
            raise ValueError("split ready 与 blockers 不一致。")
        return self


class V2_3GraphGateReport(FrozenModel):
    """完整 development 10 + validation 41 Graph outcome 闭包。."""

    schema_version: Literal["v2_3_graph_gate_report_v2"] = (
        V2_3_GRAPH_GATE_REPORT_SCHEMA_VERSION
    )
    gate_stage: Literal["v2_3_graph_conformance"] = "v2_3_graph_conformance"
    manifest_id: NonEmptyString
    dataset_version: NonEmptyString
    manifest_sha256: Sha256Hex
    taxonomy_sha256: Sha256Hex
    config_sha256: Sha256Hex
    input_intent_outcomes_sha256: Sha256Hex
    input_compiler_outcomes_sha256: Sha256Hex
    outcomes_sha256: Sha256Hex
    development: V2_3GraphSplitReport
    validation: V2_3GraphSplitReport
    cases_passed: V2_3CountMetric
    seed_attempts: V2_3CountMetric
    attempt_artifact_closures: V2_3CountMetric
    successful_candidates: V2_3CountMetric
    hypothesis_branch_bests: V2_3CountMetric
    objective_best_cases: V2_3CountMetric
    expected_unsupported_no_candidate_cases: V2_3CountMetric
    unsupported_attempts: V2_3CountMetric
    restart_phase_recoveries: tuple[V2_3RestartPhaseMetric, ...]
    model_calls: int = Field(ge=0)
    production_admission_enabled: bool
    ready: bool
    blockers: tuple[NonEmptyString, ...]

    @model_validator(mode="after")
    def _validate_report(self) -> V2_3GraphGateReport:
        if self.development.split != "development":
            raise ValueError("development 字段必须保存 development split report。")
        if self.validation.split != "validation":
            raise ValueError("validation 字段必须保存 validation split report。")
        if tuple(item.phase for item in self.restart_phase_recoveries) != (
            V2_3_RESTART_PHASES
        ):
            raise ValueError("Graph restart matrix 必须逐 phase 完整且顺序冻结。")
        if self.ready != (not self.blockers):
            raise ValueError("V2.3 Graph gate ready 与 blockers 不一致。")
        if self.ready and (self.production_admission_enabled or self.model_calls):
            raise ValueError(
                "fixture/no-model ready 不得启用 production admission 或模型。"
            )
        return self


def _metric(numerator: int, denominator: int) -> V2_3CountMetric:
    return V2_3CountMetric(numerator=numerator, denominator=denominator)


def _restart_metrics(
    outcomes: tuple[V2_3GraphCaseOutcome, ...],
) -> tuple[V2_3RestartPhaseMetric, ...]:
    return tuple(
        V2_3RestartPhaseMetric(
            phase=phase,
            recoveries=_metric(
                sum(
                    result.verified
                    for outcome in outcomes
                    for result in outcome.restart_phase_results
                    if result.phase == phase
                ),
                sum(
                    result.phase == phase
                    for outcome in outcomes
                    for result in outcome.restart_phase_results
                ),
            ),
        )
        for phase in V2_3_RESTART_PHASES
    )


def _validate_dataset_gate_identity(
    dataset: LoadedV2Dataset,
    stage_gate: V2DatasetStageGate,
) -> None:
    if dataset.gate_stage != "v2_3_graph_conformance":
        raise ValueError(
            "Graph report 只接受 V2.3 graph conformance gate_stage 数据集。"
        )
    if stage_gate.stage != "v2_3_graph_conformance":
        raise ValueError("Graph report 只接受 V2.3 Graph StageGate。")
    if stage_gate.required_splits != ("validation",):
        raise ValueError("V2.3 Graph conformance StageGate 只允许 validation 前置。")
    if not stage_gate.ready or stage_gate.blockers:
        raise ValueError("V2.3 Graph StageGate 未通过，不得聚合 Graph 结果。")
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


def _split_report(
    split: Literal["development", "validation"],
    outcomes: tuple[V2_3GraphCaseOutcome, ...],
) -> V2_3GraphSplitReport:
    case_denominator = len(outcomes)
    seed_denominator = sum(item.expected_seed_attempt_count for item in outcomes)
    restart_metrics = _restart_metrics(outcomes)
    metrics = {
        "cases_passed": _metric(
            sum(item.success for item in outcomes), case_denominator
        ),
        "finalized_states": _metric(
            sum(item.terminal_phase == "finalized" for item in outcomes),
            case_denominator,
        ),
        "seed_attempts": _metric(
            sum(item.seed_attempt_count for item in outcomes), seed_denominator
        ),
        "attempt_artifact_closures": _metric(
            sum(item.attempt_artifact_closure_count for item in outcomes),
            seed_denominator,
        ),
        "successful_candidates": _metric(
            sum(item.successful_candidate_count for item in outcomes),
            seed_denominator,
        ),
        "hypothesis_branch_bests": _metric(
            sum(item.branch_best_count for item in outcomes),
            sum(item.supported_hypothesis_count for item in outcomes),
        ),
        "objective_best_cases": _metric(
            sum(
                item.stop_reason == "completed_with_objective_best"
                for item in outcomes
                if item.expected_terminal_class == "objective_best"
            ),
            sum(item.expected_terminal_class == "objective_best" for item in outcomes),
        ),
        "expected_unsupported_no_candidate_cases": _metric(
            sum(
                item.stop_reason == "no_valid_candidate"
                and item.unsupported_classification_verified
                for item in outcomes
                if item.expected_terminal_class == "unsupported_no_valid_candidate"
            ),
            sum(
                item.expected_terminal_class == "unsupported_no_valid_candidate"
                for item in outcomes
            ),
        ),
        "unsupported_attempts": _metric(
            sum(item.unsupported_attempt_count for item in outcomes),
            sum(
                item.unsupported_hypothesis_count * V2_3_SEED_ATTEMPTS_PER_CASE
                for item in outcomes
            ),
        ),
        "hypothesis_identity_propagations": _metric(
            sum(item.hypothesis_identity_propagated for item in outcomes),
            case_denominator,
        ),
        "deterministic_replays": _metric(
            sum(item.deterministic_replay_verified for item in outcomes),
            case_denominator,
        ),
        "cas_stale_write_rejections": _metric(
            sum(item.cas_stale_write_rejected for item in outcomes),
            case_denominator,
        ),
        "production_admission_disabled": _metric(
            sum(not item.production_admission_enabled for item in outcomes),
            case_denominator,
        ),
        "zero_model_call_cases": _metric(
            sum(item.model_calls == 0 for item in outcomes), case_denominator
        ),
    }
    required_metrics = {
        name: metric
        for name, metric in metrics.items()
        if name != "successful_candidates"
    }
    blockers = tuple(
        f"{name}:{metric.numerator}/{metric.denominator}"
        for name, metric in required_metrics.items()
        if metric.numerator != metric.denominator
    ) + tuple(
        f"restart_phase_{item.phase}:{item.recoveries.numerator}/{item.recoveries.denominator}"
        for item in restart_metrics
        if item.recoveries.denominator != 1 or item.recoveries.numerator != 1
    )
    return V2_3GraphSplitReport(
        split=split,
        cases_passed=metrics["cases_passed"],
        finalized_states=metrics["finalized_states"],
        seed_attempts=metrics["seed_attempts"],
        attempt_artifact_closures=metrics["attempt_artifact_closures"],
        successful_candidates=metrics["successful_candidates"],
        hypothesis_branch_bests=metrics["hypothesis_branch_bests"],
        objective_best_cases=metrics["objective_best_cases"],
        expected_unsupported_no_candidate_cases=metrics[
            "expected_unsupported_no_candidate_cases"
        ],
        unsupported_attempts=metrics["unsupported_attempts"],
        hypothesis_identity_propagations=metrics["hypothesis_identity_propagations"],
        restart_phase_recoveries=restart_metrics,
        deterministic_replays=metrics["deterministic_replays"],
        cas_stale_write_rejections=metrics["cas_stale_write_rejections"],
        production_admission_disabled=metrics["production_admission_disabled"],
        zero_model_call_cases=metrics["zero_model_call_cases"],
        ready=not blockers,
        blockers=blockers,
    )


def evaluate_v2_3_graph_gate(
    dataset: LoadedV2Dataset,
    stage_gate: V2DatasetStageGate,
    outcomes: tuple[V2_3GraphCaseOutcome, ...],
    *,
    config_sha256: str,
    input_intent_outcomes_sha256: str,
    input_compiler_outcomes_sha256: str,
) -> V2_3GraphGateReport:
    """聚合完整 10+41 outcomes；缺失、重复、额外/release 一律拒绝。."""
    _validate_dataset_gate_identity(dataset, stage_gate)
    development = tuple(
        sample
        for sample in dataset.manifest.split("development").samples
        if sample.dataset_role == "regression"
        and sample.source_suite_id == "png_to_shader_v1_m0"
    )
    validation = dataset.manifest.split("validation").samples
    if (
        len(development) != V2_3_EXPECTED_DEVELOPMENT_COUNT
        or len(validation) != V2_3_EXPECTED_VALIDATION_COUNT
    ):
        raise ValueError("V2.3 Graph gate 要求冻结 development 10 + validation 41。")

    expected_identity = (
        stage_gate.manifest_id,
        stage_gate.dataset_version,
        stage_gate.manifest_sha256,
        stage_gate.taxonomy_sha256,
        config_sha256,
        input_intent_outcomes_sha256,
        input_compiler_outcomes_sha256,
    )
    indexed: dict[tuple[str, str], V2_3GraphCaseOutcome] = {}
    for raw_outcome in outcomes:
        outcome = V2_3GraphCaseOutcome.model_validate(
            raw_outcome.model_dump(mode="python"), strict=True
        )
        if outcome.split == "release-held-out":
            raise ValueError(
                "V2.3 Graph conformance 禁止接收 release-held-out outcome。"
            )
        if (
            outcome.manifest_id,
            outcome.dataset_version,
            outcome.manifest_sha256,
            outcome.taxonomy_sha256,
            outcome.config_sha256,
            outcome.input_intent_outcomes_sha256,
            outcome.input_compiler_outcomes_sha256,
        ) != expected_identity:
            raise ValueError(f"outcome {outcome.case_id} 的身份/hash 不一致。")
        key = (outcome.split, outcome.case_id)
        if key in indexed:
            raise ValueError(f"case outcome 重复：{outcome.split}/{outcome.case_id}。")
        indexed[key] = outcome

    expected_samples = {
        **{("development", item.case_id): item for item in development},
        **{("validation", item.case_id): item for item in validation},
    }
    expected_keys = set(expected_samples)
    missing = sorted(expected_keys - set(indexed))
    extra = sorted(set(indexed) - expected_keys)
    if missing or extra:
        raise ValueError(f"outcome case 集不闭合；missing={missing} extra={extra}。")
    ordered = tuple(indexed[key] for key in sorted(indexed))
    development_report = _split_report(
        "development", tuple(item for item in ordered if item.split == "development")
    )
    validation_report = _split_report(
        "validation", tuple(item for item in ordered if item.split == "validation")
    )
    case_denominator = V2_3_EXPECTED_CASE_COUNT
    seed_denominator = sum(item.expected_seed_attempt_count for item in ordered)
    cases_passed = _metric(sum(item.success for item in ordered), case_denominator)
    seed_attempts = _metric(
        sum(item.seed_attempt_count for item in ordered), seed_denominator
    )
    closures = _metric(
        sum(item.attempt_artifact_closure_count for item in ordered),
        seed_denominator,
    )
    candidates = _metric(
        sum(item.successful_candidate_count for item in ordered),
        seed_denominator,
    )
    branch_bests = _metric(
        sum(item.branch_best_count for item in ordered),
        sum(item.supported_hypothesis_count for item in ordered),
    )
    objective_best_cases = _metric(
        sum(
            item.stop_reason == "completed_with_objective_best"
            for item in ordered
            if item.expected_terminal_class == "objective_best"
        ),
        sum(item.expected_terminal_class == "objective_best" for item in ordered),
    )
    unsupported_cases = _metric(
        sum(
            item.stop_reason == "no_valid_candidate"
            and item.unsupported_classification_verified
            for item in ordered
            if item.expected_terminal_class == "unsupported_no_valid_candidate"
        ),
        sum(
            item.expected_terminal_class == "unsupported_no_valid_candidate"
            for item in ordered
        ),
    )
    unsupported_attempts = _metric(
        sum(item.unsupported_attempt_count for item in ordered),
        sum(
            item.unsupported_hypothesis_count * V2_3_SEED_ATTEMPTS_PER_CASE
            for item in ordered
        ),
    )
    model_calls = sum(item.model_calls for item in ordered)
    restart_metrics = _restart_metrics(ordered)
    admission_enabled = any(item.production_admission_enabled for item in ordered)
    blockers: list[str] = []
    if not development_report.ready:
        blockers.append("development_not_ready")
    if not validation_report.ready:
        blockers.append("validation_not_ready")
    if cases_passed.numerator != cases_passed.denominator:
        blockers.append(
            f"case_failures:{cases_passed.numerator}/{cases_passed.denominator}"
        )
    if seed_attempts.numerator != seed_attempts.denominator:
        blockers.append(
            f"seed_attempts:{seed_attempts.numerator}/{seed_attempts.denominator}"
        )
    if closures.numerator != closures.denominator:
        blockers.append(
            f"attempt_artifact_closures:{closures.numerator}/{closures.denominator}"
        )
    if branch_bests.numerator != branch_bests.denominator:
        blockers.append(
            f"hypothesis_branch_bests:{branch_bests.numerator}/{branch_bests.denominator}"
        )
    if model_calls:
        blockers.append(f"model_calls_nonzero:{model_calls}")
    if admission_enabled:
        blockers.append("production_admission_enabled")
    blockers.extend(
        f"restart_phase_{item.phase}:{item.recoveries.numerator}/{item.recoveries.denominator}"
        for item in restart_metrics
        if item.recoveries.denominator != 2 or item.recoveries.numerator != 2
    )

    return V2_3GraphGateReport(
        manifest_id=stage_gate.manifest_id,
        dataset_version=stage_gate.dataset_version,
        manifest_sha256=stage_gate.manifest_sha256,
        taxonomy_sha256=stage_gate.taxonomy_sha256,
        config_sha256=config_sha256,
        input_intent_outcomes_sha256=input_intent_outcomes_sha256,
        input_compiler_outcomes_sha256=input_compiler_outcomes_sha256,
        outcomes_sha256=canonical_sha256(
            tuple(item.model_dump(mode="python") for item in ordered)
        ),
        development=development_report,
        validation=validation_report,
        cases_passed=cases_passed,
        seed_attempts=seed_attempts,
        attempt_artifact_closures=closures,
        successful_candidates=candidates,
        hypothesis_branch_bests=branch_bests,
        objective_best_cases=objective_best_cases,
        expected_unsupported_no_candidate_cases=unsupported_cases,
        unsupported_attempts=unsupported_attempts,
        restart_phase_recoveries=restart_metrics,
        model_calls=model_calls,
        production_admission_enabled=admission_enabled,
        ready=not blockers,
        blockers=tuple(blockers),
    )


__all__ = [
    "V2_3_EXPECTED_CASE_COUNT",
    "V2_3_EXPECTED_DEVELOPMENT_COUNT",
    "V2_3_EXPECTED_VALIDATION_COUNT",
    "V2_3_GRAPH_CASE_OUTCOME_SCHEMA_VERSION",
    "V2_3_GRAPH_GATE_REPORT_SCHEMA_VERSION",
    "V2_3_GRAPH_SPLIT_REPORT_SCHEMA_VERSION",
    "V2_3_SEED_ATTEMPTS_PER_CASE",
    "V2_3_RESTART_PHASES",
    "V2_3CountMetric",
    "V2_3GraphCaseOutcome",
    "V2_3GraphFailureCode",
    "V2_3GraphGateReport",
    "V2_3GraphSplitReport",
    "V2_3RestartPhase",
    "V2_3RestartPhaseMetric",
    "V2_3RestartPhaseOutcome",
    "V2_3TerminalClass",
    "evaluate_v2_3_graph_gate",
]
