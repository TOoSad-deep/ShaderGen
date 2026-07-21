"""M6.2 measurement seed admission 的只读 counterfactual replay."""

from __future__ import annotations

import json
import math
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import Field, model_validator

from shaderforge.benchmark.m6_2_diagnostics import (
    M6_2CaseDiagnostic,
    M6_2StructureDiagnosticReport,
)
from shaderforge.contracts import AcceptancePolicy
from shaderforge.contracts.base import (
    FiniteFloat,
    FrozenModel,
    NonEmptyString,
    Sha256Hex,
)
from shaderforge.contracts.canonical import canonical_json_bytes
from shaderforge.evaluation import (
    CandidateRecord,
    CurrentBestDecision,
    MeasurementSeedAdmissionPolicy,
    ScoreBreakdownV1,
    TargetStructureFacts,
    build_generator_admission_evidence,
    select_current_best,
)

M6_2_SELECTOR_REPLAY_SCHEMA_VERSION: Literal[
    "png_to_shader_m6_2_seed_admission_replay_v2"
] = "png_to_shader_m6_2_seed_admission_replay_v2"

ReplaySelectionReason = Literal[
    "first_valid_candidate",
    "improved",
    "hard_constraints_failed",
    "score_missing",
    "current_best_score_missing",
    "insufficient_total_improvement",
    "protected_evidence_missing",
    "protected_region_regression",
    "generator_capability_unsupported",
    "generator_capability_unknown",
]


class _ReplayScore(FrozenModel):
    """严格读取旧 Candidate 中的完整 V1 score vector."""

    metric_version: NonEmptyString
    total_loss: FiniteFloat = Field(ge=0.0)
    global_rmse: FiniteFloat = Field(ge=0.0)
    global_mae: FiniteFloat = Field(ge=0.0)
    edge_loss: FiniteFloat = Field(ge=0.0)
    geometry_loss: FiniteFloat | None = Field(default=None, ge=0.0)
    representative_pixel_loss: FiniteFloat = Field(ge=0.0)
    roi_losses: dict[NonEmptyString, FiniteFloat]
    protected_region_losses: dict[NonEmptyString, FiniteFloat]
    effective_weights: dict[NonEmptyString, FiniteFloat]
    diagnostics: list[str]

    def to_score(self) -> ScoreBreakdownV1:
        """转换为真实 Selector 消费的不可变 ScoreBreakdownV1."""
        return ScoreBreakdownV1(
            metric_version=self.metric_version,
            total_loss=self.total_loss,
            global_rmse=self.global_rmse,
            global_mae=self.global_mae,
            edge_loss=self.edge_loss,
            geometry_loss=self.geometry_loss,
            representative_pixel_loss=self.representative_pixel_loss,
            roi_losses=tuple(self.roi_losses.items()),
            protected_region_losses=tuple(self.protected_region_losses.items()),
            effective_weights=tuple(self.effective_weights.items()),
            diagnostics=tuple(self.diagnostics),
        )


class _ReplayCandidate(FrozenModel):
    """拒绝旧 run-evidence 中的类型 coercion 与未知 Candidate 字段."""

    candidate_id: NonEmptyString
    parent_candidate_id: NonEmptyString | None
    glsl_sha256: Sha256Hex
    glsl_ref: NonEmptyString
    author_ref: NonEmptyString
    provenance_ref: NonEmptyString
    compile_ref: NonEmptyString | None
    render_ref: NonEmptyString | None
    render_sha256: Sha256Hex | None
    metrics_ref: NonEmptyString | None
    review_ref: NonEmptyString | None
    iteration: int = Field(ge=0)
    changed_problem_domain: NonEmptyString
    prompt_version: NonEmptyString
    model_ref: NonEmptyString
    score_summary: _ReplayScore | None
    hard_constraints_passed: bool
    origin: Literal["model", "deterministic"]
    generator_version: NonEmptyString | None

    def to_candidate(self) -> CandidateRecord:
        """转换为真实 Selector 消费的 CandidateRecord."""
        return CandidateRecord(
            candidate_id=self.candidate_id,
            parent_candidate_id=self.parent_candidate_id,
            glsl_sha256=self.glsl_sha256,
            glsl_ref=self.glsl_ref,
            author_ref=self.author_ref,
            provenance_ref=self.provenance_ref,
            compile_ref=self.compile_ref,
            render_ref=self.render_ref,
            render_sha256=self.render_sha256,
            metrics_ref=self.metrics_ref,
            review_ref=self.review_ref,
            iteration=self.iteration,
            changed_problem_domain=self.changed_problem_domain,
            prompt_version=self.prompt_version,
            model_ref=self.model_ref,
            score_summary=(
                None if self.score_summary is None else self.score_summary.to_score()
            ),
            hard_constraints_passed=self.hard_constraints_passed,
            origin=self.origin,
            generator_version=self.generator_version,
        )


class _ReplayStaticValidation(FrozenModel):
    """成功 compile Artifact 中封闭的静态校验事实."""

    contract_id: Literal["webgl1_static_no_texture_v1"]
    source_chars: int = Field(ge=1)
    valid: Literal[True]
    violations: list[dict[str, Any]]

    @model_validator(mode="after")
    def _validate_success(self) -> _ReplayStaticValidation:
        if self.violations:
            raise ValueError("成功 compile 的 static violations 必须为空。")
        return self


class _ReplayCompile(FrozenModel):
    """与当前 CompileResult 成功态一致的严格 replay 契约."""

    success: Literal[True]
    vertex_log: str
    fragment_log: str
    link_log: str
    draw_error: None
    static_validation: _ReplayStaticValidation


class ReplaySelectionDecision(FrozenModel):
    """一个真实 Selector decision 的 JSON-safe 快照."""

    accepted: bool
    reason: ReplaySelectionReason
    total_improvement: FiniteFloat | None
    max_protected_regression: FiniteFloat | None = Field(default=None, ge=0.0)
    admission_status: Literal["admitted", "unsupported", "unknown"] | None = None
    admission_policy_version: Literal["measurement_seed_admission_v1"] | None = None
    admission_reason_codes: tuple[NonEmptyString, ...] | None = None

    @model_validator(mode="after")
    def _validate_cross_fields(self) -> ReplaySelectionDecision:
        accepted_reasons = {"first_valid_candidate", "improved"}
        if self.accepted != (self.reason in accepted_reasons):
            raise ValueError("accepted 与 Selector reason 不一致。")
        admission_fields = (
            self.admission_status,
            self.admission_policy_version,
            self.admission_reason_codes,
        )
        if any(value is None for value in admission_fields) and any(
            value is not None for value in admission_fields
        ):
            raise ValueError("admission decision 字段必须同时出现或同时缺失。")
        if self.admission_reason_codes == ():
            raise ValueError("admission_reason_codes 不能为空。")
        generator_reason_status = {
            "generator_capability_unsupported": "unsupported",
            "generator_capability_unknown": "unknown",
        }
        expected_status = generator_reason_status.get(self.reason)
        if expected_status is not None:
            if self.admission_status != expected_status:
                raise ValueError("generator rejection reason 与 admission status 不一致。")
            if self.total_improvement is not None:
                raise ValueError("generator admission 拒绝不得伪造 score improvement。")
        elif self.admission_status in {"unsupported", "unknown"}:
            raise ValueError("rejected admission status 缺少对应 generator reason。")
        if self.reason == "improved" and self.total_improvement is None:
            raise ValueError("improved decision 必须提供 total_improvement。")
        return self

    @classmethod
    def from_decision(cls, value: CurrentBestDecision) -> ReplaySelectionDecision:
        """从 Selector decision 构造严格 replay 证据."""
        return cls.model_validate(value.to_dict(), strict=True)


class M6_2SelectorReplayCase(FrozenModel):
    """一个 affine seed 选择点的基线与 opt-in admission 对照."""

    case_id: NonEmptyString
    human_preference: Literal["initial", "final", "tie"]
    initial_candidate_id: NonEmptyString
    seed_candidate_id: NonEmptyString
    seed_generator_version: Literal["measurement_affine_seed_v1"]
    capability_status: Literal["supported", "unsupported", "unknown"]
    capability_reason_codes: tuple[NonEmptyString, ...] = Field(min_length=1)
    baseline_decision: ReplaySelectionDecision
    admission_decision: ReplaySelectionDecision

    @model_validator(mode="after")
    def _validate_decisions(self) -> M6_2SelectorReplayCase:
        if self.initial_candidate_id == self.seed_candidate_id:
            raise ValueError("replay initial 与 seed candidate 不得相同。")
        if not self.baseline_decision.accepted:
            raise ValueError("旧 Selector 基线必须接受被重放的 affine seed。")
        if self.baseline_decision.reason != "improved":
            raise ValueError("旧 Selector affine replay 必须是 improved。")
        if self.baseline_decision.admission_status is not None:
            raise ValueError("旧 Selector baseline 不得携带 admission 证据。")
        expected_admission_status = (
            "admitted"
            if self.capability_status == "supported"
            else self.capability_status
        )
        if self.admission_decision.admission_status != expected_admission_status:
            raise ValueError("capability status 与 admission decision 不一致。")
        if self.admission_decision.admission_reason_codes != self.capability_reason_codes:
            raise ValueError("capability/admission reason_codes 不一致。")
        if self.capability_status == "supported":
            if not self.admission_decision.accepted:
                raise ValueError("supported seed 不得被 admission 错误拒绝。")
            if self.admission_decision.admission_status != "admitted":
                raise ValueError("supported seed 缺少 admitted 证据。")
            if (
                self.admission_decision.reason != self.baseline_decision.reason
                or self.admission_decision.total_improvement
                != self.baseline_decision.total_improvement
                or self.admission_decision.max_protected_regression
                != self.baseline_decision.max_protected_regression
            ):
                raise ValueError("supported admission 改变了既有 Selector 结果。")
        else:
            if self.admission_decision.accepted:
                raise ValueError("unsupported/unknown seed 必须 fail closed。")
            expected_reason = (
                "generator_capability_unsupported"
                if self.capability_status == "unsupported"
                else "generator_capability_unknown"
            )
            if self.admission_decision.reason != expected_reason:
                raise ValueError("admission rejection reason 与 capability 不一致。")
        return self


class M6_2SelectorReplayReport(FrozenModel):
    """旧正式 run 的只读 measurement seed admission replay 报告."""

    schema_version: Literal["png_to_shader_m6_2_seed_admission_replay_v2"]
    selection_point: Literal["initial_to_affine_seed_counterfactual"]
    production_enabled: Literal[False]
    source_suite_run_id: NonEmptyString
    source_diagnostic_report_hash: Sha256Hex
    source_diagnostic_document_sha256: Sha256Hex
    source_config_sha256: Sha256Hex
    admission_policy_version: Literal["measurement_seed_admission_v1"]
    capability_policy_version: Literal["deterministic_generator_capability_v2"]
    case_count: int = Field(ge=1)
    baseline_accepted_count: int = Field(ge=0)
    admission_rejected_count: int = Field(ge=0)
    initial_preferred_unsupported_rejected_count: int = Field(ge=0)
    supported_admitted_count: int = Field(ge=0)
    cases: tuple[M6_2SelectorReplayCase, ...] = Field(min_length=1)
    report_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_summary(self) -> M6_2SelectorReplayReport:
        if self.case_count != len(self.cases):
            raise ValueError("replay case_count 与 cases 不一致。")
        if len({item.case_id for item in self.cases}) != len(self.cases):
            raise ValueError("replay case_id 不得重复。")
        expected = {
            "baseline": sum(item.baseline_decision.accepted for item in self.cases),
            "rejected": sum(not item.admission_decision.accepted for item in self.cases),
            "initial_unsupported": sum(
                item.human_preference == "initial"
                and item.capability_status == "unsupported"
                and not item.admission_decision.accepted
                for item in self.cases
            ),
            "supported": sum(
                item.capability_status == "supported"
                and item.admission_decision.accepted
                for item in self.cases
            ),
        }
        actual = {
            "baseline": self.baseline_accepted_count,
            "rejected": self.admission_rejected_count,
            "initial_unsupported": self.initial_preferred_unsupported_rejected_count,
            "supported": self.supported_admitted_count,
        }
        if actual != expected:
            raise ValueError("replay 汇总与逐例 decision 不一致。")
        if self.report_hash != compute_m6_2_selector_replay_hash(
            self.model_dump(mode="json")
        ):
            raise ValueError("M6.2 selector replay report_hash 不一致。")
        return self


def compute_m6_2_selector_replay_hash(value: Mapping[str, Any]) -> str:
    """计算排除自身字段后的 replay canonical hash."""
    payload = dict(value)
    payload.pop("report_hash", None)
    return sha256(canonical_json_bytes(payload)).hexdigest()


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 字段不得重复：{key}。")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON 不允许非有限数值：{value}。")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_bytes(),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path} 不是严格 JSON。") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} 必须是 JSON object。")
    return value


def _sha256_path(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _safe_path(root: Path, relative_path: str, *, field_name: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute():
        raise ValueError(f"{field_name} 必须是相对路径。")
    resolved_root = root.resolve()
    path = (resolved_root / relative).resolve()
    if not path.is_relative_to(resolved_root):
        raise ValueError(f"{field_name} 越过允许根目录。")
    return path


def _require_mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} 必须是 object。")
    return value


def _require_string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} 必须是非空字符串。")
    return value


def _find_candidate(
    records: list[Any], candidate_id: str, *, field_name: str
) -> Mapping[str, Any]:
    matches = [
        _require_mapping(item, field_name=field_name)
        for item in records
        if isinstance(item, Mapping) and item.get("candidate_id") == candidate_id
    ]
    if len(matches) != 1:
        raise ValueError(f"{field_name} 必须唯一存在：{candidate_id}。")
    return matches[0]


def _load_verified_candidate(
    *,
    run_root: Path,
    raw_record: Mapping[str, Any],
    diagnostic: M6_2CaseDiagnostic,
    role: Literal["initial", "final"],
) -> CandidateRecord:
    expected = diagnostic.initial if role == "initial" else diagnostic.final
    candidate_id = _require_string(
        raw_record.get("candidate_id"), field_name=f"{role}.candidate_id"
    )
    manifest_path = _safe_path(
        run_root,
        f"candidates/{candidate_id}/manifest.json",
        field_name=f"{role}.manifest_ref",
    )
    manifest = _read_json(manifest_path)
    if dict(raw_record) != manifest:
        raise ValueError(f"{candidate_id} run-evidence 与 Candidate manifest 不一致。")
    parsed = _ReplayCandidate.model_validate(manifest, strict=True)
    if (
        parsed.candidate_id != expected.candidate_id
        or parsed.origin != expected.origin
        or parsed.generator_version != expected.generator_version
        or parsed.glsl_sha256 != expected.glsl_sha256
        or parsed.render_sha256 != expected.render_sha256
        or parsed.glsl_ref != expected.glsl_ref
        or parsed.render_ref != expected.artifact_render_ref
        or parsed.provenance_ref != expected.provenance_ref
    ):
        raise ValueError(f"{candidate_id} Candidate 与 capability diagnostic 不一致。")
    if (
        parsed.score_summary is None
        or parsed.metrics_ref is None
        or parsed.render_ref is None
        or parsed.render_sha256 is None
        or parsed.compile_ref is None
    ):
        raise ValueError(f"{candidate_id} 缺少可 replay 的 compile/render/metrics。")
    metrics_path = _safe_path(
        run_root,
        parsed.metrics_ref,
        field_name=f"{candidate_id}.metrics_ref",
    )
    if _read_json(metrics_path) != parsed.score_summary.model_dump(mode="json"):
        raise ValueError(f"{candidate_id} metrics 与 Candidate score_summary 不一致。")
    glsl_path = _safe_path(
        run_root,
        parsed.glsl_ref,
        field_name=f"{candidate_id}.glsl_ref",
    )
    render_path = _safe_path(
        run_root,
        parsed.render_ref,
        field_name=f"{candidate_id}.render_ref",
    )
    compile_path = _safe_path(
        run_root,
        parsed.compile_ref,
        field_name=f"{candidate_id}.compile_ref",
    )
    glsl_bytes = glsl_path.read_bytes()
    if sha256(glsl_bytes).hexdigest() != parsed.glsl_sha256:
        raise ValueError(f"{candidate_id} GLSL hash 不一致。")
    if _sha256_path(render_path) != parsed.render_sha256:
        raise ValueError(f"{candidate_id} render hash 不一致。")
    compile_result = _ReplayCompile.model_validate(
        _read_json(compile_path),
        strict=True,
    )
    if parsed.hard_constraints_passed != compile_result.success:
        raise ValueError(f"{candidate_id} compile 与 hard_constraints_passed 不一致。")
    try:
        source_chars = len(glsl_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"{candidate_id} GLSL 不是 UTF-8。") from exc
    if compile_result.static_validation.source_chars != source_chars:
        raise ValueError(f"{candidate_id} compile source_chars 与 GLSL 不一致。")
    return parsed.to_candidate()


def _acceptance_policy(value: Any) -> AcceptancePolicy:
    raw = _require_mapping(value, field_name="acceptance_policy")
    expected_keys = {
        "min_total_improvement",
        "max_protected_regression",
        "quality_threshold",
        "stagnation_rounds",
    }
    if set(raw) != expected_keys:
        raise ValueError("acceptance_policy 字段集合不一致。")
    numeric_values = (
        raw["min_total_improvement"],
        raw["max_protected_regression"],
        raw["quality_threshold"],
    )
    if any(
        isinstance(item, bool) or not isinstance(item, (int, float))
        for item in numeric_values
    ):
        raise ValueError("acceptance_policy 阈值必须是有限数值。")
    if any(not math.isfinite(float(item)) for item in numeric_values):
        raise ValueError("acceptance_policy 阈值必须是有限数值。")
    stagnation = raw["stagnation_rounds"]
    if isinstance(stagnation, bool) or not isinstance(stagnation, int):
        raise ValueError("acceptance_policy stagnation_rounds 必须是整数。")
    return AcceptancePolicy(
        min_total_improvement=float(raw["min_total_improvement"]),
        max_protected_regression=float(raw["max_protected_regression"]),
        quality_threshold=float(raw["quality_threshold"]),
        stagnation_rounds=stagnation,
    )


def build_m6_2_selector_replay_report(
    *,
    suite_root: str | Path,
    artifact_root: str | Path,
    diagnostic: M6_2StructureDiagnosticReport,
    diagnostic_document_sha256: str,
) -> M6_2SelectorReplayReport:
    """只读重放旧 run 的 initial→affine seed 选择点."""
    suite = Path(suite_root).resolve()
    artifacts = Path(artifact_root).resolve()
    source_report_path = suite / "report.json"
    if _sha256_path(source_report_path) != diagnostic.source.source_report_sha256:
        raise ValueError("source report 与 capability diagnostic 锚点不一致。")
    source_report = _read_json(source_report_path)
    if source_report.get("suite_run_id") != diagnostic.source.suite_run_id:
        raise ValueError("source report suite_run_id 与 diagnostic 不一致。")
    source_config_sha256 = _require_string(
        source_report.get("config_sha256"),
        field_name="source report config_sha256",
    )
    config_path = suite / "config.json"
    if not config_path.is_file():
        raise ValueError("selector replay 缺少 suite config.json。")
    actual_config_sha256 = _sha256_path(config_path)
    if source_config_sha256 != actual_config_sha256:
        raise ValueError("source report config_sha256 与 config bytes 不一致。")
    suite_config = _read_json(config_path)
    if suite_config.get("suite_run_id") != diagnostic.source.suite_run_id:
        raise ValueError("suite config suite_run_id 与 diagnostic 不一致。")
    config_acceptance_raw = _require_mapping(
        suite_config.get("acceptance_policy"),
        field_name="suite config acceptance_policy",
    )
    config_acceptance = _acceptance_policy(config_acceptance_raw)
    raw_cases = source_report.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("source report cases 必须是数组。")
    source_cases: dict[str, Mapping[str, Any]] = {}
    for raw_case in raw_cases:
        item = _require_mapping(raw_case, field_name="source report case")
        case_id = _require_string(item.get("case_id"), field_name="source case_id")
        if case_id in source_cases:
            raise ValueError("source report case_id 不得重复。")
        source_cases[case_id] = item

    replay_policy = MeasurementSeedAdmissionPolicy(
        allowed_evidence_scopes=("offline_replay",)
    )
    replay_cases: list[M6_2SelectorReplayCase] = []
    for case in diagnostic.cases:
        if case.final.origin != "deterministic":
            continue
        source_case = source_cases.get(case.case_id)
        if source_case is None:
            raise ValueError(f"source report 缺少 diagnostic case：{case.case_id}。")
        ai_on = _require_mapping(
            source_case.get("ai_on"), field_name=f"{case.case_id}.ai_on"
        )
        project_id = _require_string(
            ai_on.get("project_id"), field_name=f"{case.case_id}.project_id"
        )
        run_id = _require_string(
            ai_on.get("run_id"), field_name=f"{case.case_id}.run_id"
        )
        if (
            ai_on.get("initial_candidate_id") != case.initial.candidate_id
            or ai_on.get("final_candidate_id") != case.final.candidate_id
            or ai_on.get("evidence_path") != case.run_evidence_ref
        ):
            raise ValueError(
                f"{case.case_id} source report 与 diagnostic 候选绑定不一致。"
            )
        run_root = _safe_path(
            artifacts,
            f"{project_id}/{run_id}",
            field_name=f"{case.case_id}.run_root",
        )
        evidence_path = _safe_path(
            suite,
            case.run_evidence_ref,
            field_name=f"{case.case_id}.run_evidence_ref",
        )
        if _sha256_path(evidence_path) != case.run_evidence_sha256:
            raise ValueError(f"{case.case_id} run-evidence hash 不一致。")
        run_evidence = _read_json(evidence_path)
        if (
            run_evidence.get("project_id") != project_id
            or run_evidence.get("run_id") != run_id
        ):
            raise ValueError(f"{case.case_id} run-evidence 身份不一致。")
        raw_records = run_evidence.get("candidate_records")
        if not isinstance(raw_records, list):
            raise ValueError(f"{case.case_id} candidate_records 必须是数组。")
        initial_record = _find_candidate(
            raw_records,
            case.initial.candidate_id,
            field_name=f"{case.case_id}.initial",
        )
        final_record = _find_candidate(
            raw_records,
            case.final.candidate_id,
            field_name=f"{case.case_id}.final",
        )
        initial = _load_verified_candidate(
            run_root=run_root,
            raw_record=initial_record,
            diagnostic=case,
            role="initial",
        )
        seed = _load_verified_candidate(
            run_root=run_root,
            raw_record=final_record,
            diagnostic=case,
            role="final",
        )
        if (
            initial.origin != "model"
            or seed.generator_version != "measurement_affine_seed_v1"
        ):
            raise ValueError("replay 只接受 model initial → versioned deterministic seed。")
        run_acceptance_raw = _require_mapping(
            run_evidence.get("acceptance_policy"),
            field_name=f"{case.case_id}.acceptance_policy",
        )
        if dict(run_acceptance_raw) != dict(config_acceptance_raw):
            raise ValueError(
                f"{case.case_id} run-evidence policy 与 suite config 不一致。"
            )
        acceptance = _acceptance_policy(run_acceptance_raw)
        if acceptance != config_acceptance:
            raise ValueError(f"{case.case_id} acceptance policy 解析结果不一致。")
        baseline = select_current_best(initial, seed, acceptance)
        target = TargetStructureFacts(
            topology=case.final_capability.expected_topology,
            instance_count=case.final_capability.expected_instance_count,
            hole_count=case.final_capability.expected_hole_count,
            required_layers=case.final_capability.expected_required_layers,
        )
        assert seed.render_sha256 is not None
        admission_evidence = build_generator_admission_evidence(
            target,
            origin=seed.origin,
            generator_version=seed.generator_version,
            evidence_scope="offline_replay",
            evidence_ref=f"m6_2_diagnostic:{diagnostic.report_hash}",
            evidence_sha256=diagnostic_document_sha256,
            target_source_sha256=case.source_input_sha256,
            normalized_reference_sha256=case.normalized_reference_sha256,
            candidate_id=seed.candidate_id,
            candidate_glsl_sha256=seed.glsl_sha256,
            candidate_render_sha256=seed.render_sha256,
        )
        if admission_evidence.assessment != case.final_capability:
            raise ValueError(f"{case.case_id} replay 与 capability-v2 策略漂移。")
        admission = select_current_best(
            initial,
            seed,
            acceptance,
            admission_policy=replay_policy,
            admission_evidence=admission_evidence,
        )
        replay_cases.append(
            M6_2SelectorReplayCase(
                case_id=case.case_id,
                human_preference=case.human_preference,
                initial_candidate_id=initial.candidate_id,
                seed_candidate_id=seed.candidate_id,
                seed_generator_version="measurement_affine_seed_v1",
                capability_status=case.final_capability.status,
                capability_reason_codes=case.final_capability.reason_codes,
                baseline_decision=ReplaySelectionDecision.from_decision(baseline),
                admission_decision=ReplaySelectionDecision.from_decision(admission),
            )
        )
    if not replay_cases:
        raise ValueError("diagnostic 中没有可重放的 deterministic seed。")

    payload: dict[str, Any] = {
        "schema_version": M6_2_SELECTOR_REPLAY_SCHEMA_VERSION,
        "selection_point": "initial_to_affine_seed_counterfactual",
        "production_enabled": False,
        "source_suite_run_id": diagnostic.source.suite_run_id,
        "source_diagnostic_report_hash": diagnostic.report_hash,
        "source_diagnostic_document_sha256": diagnostic_document_sha256,
        "source_config_sha256": actual_config_sha256,
        "admission_policy_version": replay_policy.policy_version,
        "capability_policy_version": replay_policy.capability_policy_version,
        "case_count": len(replay_cases),
        "baseline_accepted_count": sum(
            item.baseline_decision.accepted for item in replay_cases
        ),
        "admission_rejected_count": sum(
            not item.admission_decision.accepted for item in replay_cases
        ),
        "initial_preferred_unsupported_rejected_count": sum(
            item.human_preference == "initial"
            and item.capability_status == "unsupported"
            and not item.admission_decision.accepted
            for item in replay_cases
        ),
        "supported_admitted_count": sum(
            item.capability_status == "supported"
            and item.admission_decision.accepted
            for item in replay_cases
        ),
        "cases": tuple(replay_cases),
    }
    payload["report_hash"] = compute_m6_2_selector_replay_hash(payload)
    return M6_2SelectorReplayReport.model_validate(payload, strict=True)


__all__ = [
    "M6_2_SELECTOR_REPLAY_SCHEMA_VERSION",
    "M6_2SelectorReplayCase",
    "M6_2SelectorReplayReport",
    "ReplaySelectionDecision",
    "build_m6_2_selector_replay_report",
    "compute_m6_2_selector_replay_hash",
]
