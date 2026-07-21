"""F09 M6.2 的结构能力错配诊断与只读证据报告."""

from __future__ import annotations

import json
import math
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shaderforge.benchmark.gate import decode_human_preferences
from shaderforge.benchmark.v2_dataset import LoadedV2Dataset, V2DatasetSample
from shaderforge.contracts.canonical import canonical_json_bytes
from shaderforge.evaluation.admission import (
    DETERMINISTIC_GENERATOR_CAPABILITY_POLICY_VERSION,
    StructureCapabilityAssessment,
    TargetStructureFacts,
    assess_target_structure_capability,
)

M6_2_DIAGNOSTIC_SCHEMA_VERSION: Literal[
    "png_to_shader_m6_2_structure_diagnostic_v2"
] = "png_to_shader_m6_2_structure_diagnostic_v2"
M6_2_CAPABILITY_POLICY_VERSION = (
    DETERMINISTIC_GENERATOR_CAPABILITY_POLICY_VERSION
)

HumanPreference = Literal["initial", "final", "tie"]
CandidateOrigin = Literal["model", "deterministic"]

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class _StrictModel(BaseModel):
    """诊断 Artifact 共用的严格不可变模型."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CandidateDiagnosticEvidence(_StrictModel):
    """一个候选的内容身份与来源证据."""

    candidate_id: str = Field(min_length=1)
    origin: CandidateOrigin
    generator_version: str | None
    render_path: str = Field(min_length=1)
    artifact_render_ref: str = Field(min_length=1)
    render_sha256: str = Field(pattern=_SHA256_PATTERN)
    glsl_ref: str = Field(min_length=1)
    glsl_sha256: str = Field(pattern=_SHA256_PATTERN)
    provenance_ref: str = Field(min_length=1)
    provenance_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _validate_generator_identity(self) -> CandidateDiagnosticEvidence:
        if self.origin == "deterministic" and not self.generator_version:
            raise ValueError("deterministic 候选必须提供 generator_version。")
        if self.origin == "model" and self.generator_version is not None:
            raise ValueError("model 候选不得伪装为 deterministic generator。")
        return self


class M6_2CaseDiagnostic(_StrictModel):
    """一个正式 M5 case 的候选、人工偏好和结构错配绑定."""

    case_id: str = Field(min_length=1)
    target_image_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_input_ref: Literal["input/source.bin"]
    source_input_sha256: str = Field(pattern=_SHA256_PATTERN)
    normalized_reference_ref: Literal["input/reference.png"]
    normalized_reference_sha256: str = Field(pattern=_SHA256_PATTERN)
    run_evidence_ref: str = Field(min_length=1)
    run_evidence_sha256: str = Field(pattern=_SHA256_PATTERN)
    human_preference: HumanPreference
    initial_objective_total_loss: float = Field(allow_inf_nan=False, ge=0.0)
    final_objective_total_loss: float = Field(allow_inf_nan=False, ge=0.0)
    objective_improvement: float = Field(allow_inf_nan=False)
    initial: CandidateDiagnosticEvidence
    final: CandidateDiagnosticEvidence
    final_capability: StructureCapabilityAssessment

    @model_validator(mode="after")
    def _validate_case_bindings(self) -> M6_2CaseDiagnostic:
        if self.target_image_sha256 != self.source_input_sha256:
            raise ValueError("dataset target 与 run source input 内容不一致。")
        expected_improvement = (
            self.initial_objective_total_loss - self.final_objective_total_loss
        )
        if not math.isclose(
            self.objective_improvement,
            expected_improvement,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("objective_improvement 与 initial-final 不一致。")
        return self


class M6_2SourceAnchors(_StrictModel):
    """诊断所消费的只读输入内容锚点."""

    suite_run_id: str = Field(min_length=1)
    source_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    assignments_sha256: str = Field(pattern=_SHA256_PATTERN)
    human_review_sha256: str = Field(pattern=_SHA256_PATTERN)
    dataset_manifest_id: str = Field(min_length=1)
    dataset_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)


class M6_2StructureDiagnosticReport(_StrictModel):
    """可重复生成且不改变旧 run 的 M6.2 结构诊断报告."""

    schema_version: Literal["png_to_shader_m6_2_structure_diagnostic_v2"]
    capability_policy_version: Literal["deterministic_generator_capability_v2"]
    source: M6_2SourceAnchors
    case_count: int = Field(ge=1)
    initial_preferred_count: int = Field(ge=0)
    capability_unsupported_count: int = Field(ge=0)
    initial_preferred_capability_unsupported_count: int = Field(ge=0)
    cases: tuple[M6_2CaseDiagnostic, ...] = Field(min_length=1)
    report_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _validate_summary(self) -> M6_2StructureDiagnosticReport:
        if self.case_count != len(self.cases):
            raise ValueError("case_count 与 cases 数量不一致。")
        case_ids = [item.case_id for item in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("诊断 case_id 不得重复。")
        initial_count = sum(item.human_preference == "initial" for item in self.cases)
        unsupported_count = sum(
            item.final_capability.status == "unsupported" for item in self.cases
        )
        initial_unsupported_count = sum(
            item.human_preference == "initial"
            and item.final_capability.status == "unsupported"
            for item in self.cases
        )
        if self.initial_preferred_count != initial_count:
            raise ValueError("initial_preferred_count 与逐例诊断不一致。")
        if self.capability_unsupported_count != unsupported_count:
            raise ValueError("capability_unsupported_count 与逐例诊断不一致。")
        if (
            self.initial_preferred_capability_unsupported_count
            != initial_unsupported_count
        ):
            raise ValueError("initial-win capability unsupported 汇总不一致。")
        expected_hash = compute_m6_2_report_hash(self.model_dump(mode="json"))
        if self.report_hash != expected_hash:
            raise ValueError("M6.2 diagnostic report_hash 不一致。")
        return self


def assess_generator_capability(
    sample: V2DatasetSample,
    *,
    origin: CandidateOrigin,
    generator_version: str | None,
) -> StructureCapabilityAssessment:
    """只用版本化标签与已知 generator 能力判断结构表达错配.

    ``supported`` 仅表示生成器能力覆盖标签，不表示渲染像素已经证明语义保真；
    model 或未知 generator 一律返回 ``unknown``，避免用猜测生成硬结论。
    """
    return assess_target_structure_capability(
        TargetStructureFacts(
            topology=sample.topology,
            instance_count=sample.instance_count,
            hole_count=sample.hole_count,
            required_layers=sample.required_layers,
        ),
        origin=origin,
        generator_version=generator_version,
    )


def compute_m6_2_report_hash(value: Mapping[str, Any]) -> str:
    """计算排除自身字段后的稳定报告 hash."""
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


def _require_loss(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} 必须是有限非负数。")
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise ValueError(f"{field_name} 必须是有限非负数。")
    return result


def _candidate_record(
    records: tuple[Mapping[str, Any], ...], candidate_id: str
) -> Mapping[str, Any]:
    matches = [item for item in records if item.get("candidate_id") == candidate_id]
    if len(matches) != 1:
        raise ValueError(f"候选记录必须唯一存在：{candidate_id}。")
    return matches[0]


def _candidate_evidence(
    *,
    suite_root: Path,
    run_root: Path,
    normalized_reference_sha256: str,
    render_relative_path: str,
    record: Mapping[str, Any],
) -> CandidateDiagnosticEvidence:
    candidate_id = _require_string(
        record.get("candidate_id"), field_name="candidate_id"
    )
    raw_origin = record.get("origin", "model")
    if raw_origin not in {"model", "deterministic"}:
        raise ValueError(f"未知 candidate origin：{raw_origin}。")
    origin: CandidateOrigin = raw_origin
    generator_version_raw = record.get("generator_version")
    generator_version = (
        None
        if generator_version_raw is None
        else _require_string(
            generator_version_raw,
            field_name=f"{candidate_id}.generator_version",
        )
    )
    expected_render_sha = _require_string(
        record.get("render_sha256"), field_name=f"{candidate_id}.render_sha256"
    )
    render_path = _safe_path(
        suite_root,
        render_relative_path,
        field_name=f"{candidate_id}.render_path",
    )
    actual_render_sha = _sha256_path(render_path)
    if actual_render_sha != expected_render_sha:
        raise ValueError(f"{candidate_id} benchmark render 与 CandidateRecord 不一致。")

    artifact_render_ref = _require_string(
        record.get("render_ref"), field_name=f"{candidate_id}.render_ref"
    )
    artifact_render_path = _safe_path(
        run_root,
        artifact_render_ref,
        field_name=f"{candidate_id}.render_ref",
    )
    if _sha256_path(artifact_render_path) != expected_render_sha:
        raise ValueError(f"{candidate_id} Artifact render 与 CandidateRecord 不一致。")

    glsl_ref = _require_string(
        record.get("glsl_ref"), field_name=f"{candidate_id}.glsl_ref"
    )
    expected_glsl_sha = _require_string(
        record.get("glsl_sha256"), field_name=f"{candidate_id}.glsl_sha256"
    )
    glsl_path = _safe_path(
        run_root,
        glsl_ref,
        field_name=f"{candidate_id}.glsl_ref",
    )
    if _sha256_path(glsl_path) != expected_glsl_sha:
        raise ValueError(f"{candidate_id} GLSL 与 CandidateRecord 不一致。")

    provenance_ref = _require_string(
        record.get("provenance_ref"), field_name=f"{candidate_id}.provenance_ref"
    )
    provenance_path = _safe_path(
        run_root,
        provenance_ref,
        field_name=f"{candidate_id}.provenance_ref",
    )
    provenance = _read_json(provenance_path)
    if provenance.get("glsl_sha256") != expected_glsl_sha:
        raise ValueError(f"{candidate_id} provenance GLSL 身份不一致。")
    if origin == "deterministic":
        if provenance.get("generator_version") != generator_version:
            raise ValueError(f"{candidate_id} generator provenance 身份不一致。")
        if provenance.get("origin") != "deterministic":
            raise ValueError(f"{candidate_id} deterministic provenance 缺失。")
        if provenance.get("reference_sha256") != normalized_reference_sha256:
            raise ValueError(f"{candidate_id} normalized reference 身份不一致。")
    return CandidateDiagnosticEvidence(
        candidate_id=candidate_id,
        origin=origin,
        generator_version=generator_version,
        render_path=render_relative_path,
        artifact_render_ref=artifact_render_ref,
        render_sha256=actual_render_sha,
        glsl_ref=glsl_ref,
        glsl_sha256=expected_glsl_sha,
        provenance_ref=provenance_ref,
        provenance_sha256=_sha256_path(provenance_path),
    )


def build_m6_2_structure_diagnostic_report(
    *,
    suite_root: str | Path,
    artifact_root: str | Path,
    dataset: LoadedV2Dataset,
) -> M6_2StructureDiagnosticReport:
    """只读加载正式 run，并绑定结构标签、Candidate 与人工偏好."""
    suite = Path(suite_root).resolve()
    artifacts = Path(artifact_root).resolve()
    report_path = suite / "report.json"
    assignments_path = suite / "blind-review/assignments.private.json"
    review_path = suite / "blind-review/human-review.json"
    source_report = _read_json(report_path)
    assignments = _read_json(assignments_path)
    human_review = _read_json(review_path)
    suite_run_id = _require_string(
        source_report.get("suite_run_id"), field_name="report.suite_run_id"
    )
    development = dataset.manifest.split("development")
    samples = {sample.case_id: sample for sample in development.samples}
    raw_cases = source_report.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("source report cases 不能为空。")
    source_cases = tuple(
        _require_mapping(raw, field_name="report case") for raw in raw_cases
    )
    preferences = decode_human_preferences(
        human_review,
        assignments,
        case_results=source_cases,
        expected_suite_run_id=suite_run_id,
    )
    case_ids = [
        _require_string(
            raw.get("case_id"),
            field_name="report case_id",
        )
        for raw in source_cases
    ]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("source report case_id 不得重复。")
    if set(case_ids) != set(preferences):
        raise ValueError("source report 与人工评审 case 集合不一致。")
    missing_labels = sorted(set(case_ids) - set(samples))
    if missing_labels:
        raise ValueError(f"development 标签缺失：{', '.join(missing_labels)}。")

    cases: list[M6_2CaseDiagnostic] = []
    for source_case in source_cases:
        case_id = _require_string(source_case.get("case_id"), field_name="case_id")
        ai_on = _require_mapping(source_case.get("ai_on"), field_name=f"{case_id}.ai_on")
        project_id = _require_string(
            ai_on.get("project_id"), field_name=f"{case_id}.project_id"
        )
        run_id = _require_string(ai_on.get("run_id"), field_name=f"{case_id}.run_id")
        run_root = _safe_path(
            artifacts,
            f"{project_id}/{run_id}",
            field_name=f"{case_id}.run_artifact_root",
        )
        sample = samples[case_id]
        source_input_ref: Literal["input/source.bin"] = "input/source.bin"
        source_input_path = _safe_path(
            run_root,
            source_input_ref,
            field_name=f"{case_id}.source_input_ref",
        )
        source_input_sha256 = _sha256_path(source_input_path)
        if source_input_sha256 != sample.sha256:
            raise ValueError(f"{case_id} dataset label 与 source input 内容不一致。")
        normalized_reference_ref: Literal["input/reference.png"] = (
            "input/reference.png"
        )
        normalized_reference_path = _safe_path(
            run_root,
            normalized_reference_ref,
            field_name=f"{case_id}.normalized_reference_ref",
        )
        normalized_reference_sha256 = _sha256_path(normalized_reference_path)
        evidence_relative = _require_string(
            ai_on.get("evidence_path"), field_name=f"{case_id}.evidence_path"
        )
        evidence_path = _safe_path(
            suite,
            evidence_relative,
            field_name=f"{case_id}.evidence_path",
        )
        evidence = _read_json(evidence_path)
        if evidence.get("project_id") != project_id or evidence.get("run_id") != run_id:
            raise ValueError(f"{case_id} run evidence 身份不一致。")
        raw_records = evidence.get("candidate_records")
        if not isinstance(raw_records, list) or not raw_records:
            raise ValueError(f"{case_id} candidate_records 不能为空。")
        records = tuple(
            _require_mapping(item, field_name=f"{case_id}.candidate_record")
            for item in raw_records
        )
        initial_id = _require_string(
            ai_on.get("initial_candidate_id"), field_name=f"{case_id}.initial_id"
        )
        final_id = _require_string(
            ai_on.get("final_candidate_id"), field_name=f"{case_id}.final_id"
        )
        initial_record = _candidate_record(records, initial_id)
        final_record = _candidate_record(records, final_id)
        initial = _candidate_evidence(
            suite_root=suite,
            run_root=run_root,
            normalized_reference_sha256=normalized_reference_sha256,
            render_relative_path=_require_string(
                ai_on.get("initial_render_path"),
                field_name=f"{case_id}.initial_render_path",
            ),
            record=initial_record,
        )
        final = _candidate_evidence(
            suite_root=suite,
            run_root=run_root,
            normalized_reference_sha256=normalized_reference_sha256,
            render_relative_path=_require_string(
                ai_on.get("final_render_path"),
                field_name=f"{case_id}.final_render_path",
            ),
            record=final_record,
        )
        initial_loss = _require_loss(
            ai_on.get("initial_objective_total_loss"),
            field_name=f"{case_id}.initial_objective_total_loss",
        )
        final_loss = _require_loss(
            ai_on.get("final_objective_total_loss"),
            field_name=f"{case_id}.final_objective_total_loss",
        )
        cases.append(
            M6_2CaseDiagnostic(
                case_id=case_id,
                target_image_sha256=sample.sha256,
                source_input_ref=source_input_ref,
                source_input_sha256=source_input_sha256,
                normalized_reference_ref=normalized_reference_ref,
                normalized_reference_sha256=normalized_reference_sha256,
                run_evidence_ref=evidence_relative,
                run_evidence_sha256=_sha256_path(evidence_path),
                human_preference=preferences[case_id],
                initial_objective_total_loss=initial_loss,
                final_objective_total_loss=final_loss,
                objective_improvement=initial_loss - final_loss,
                initial=initial,
                final=final,
                final_capability=assess_generator_capability(
                    sample,
                    origin=final.origin,
                    generator_version=final.generator_version,
                ),
            )
        )

    report_payload: dict[str, Any] = {
        "schema_version": M6_2_DIAGNOSTIC_SCHEMA_VERSION,
        "capability_policy_version": M6_2_CAPABILITY_POLICY_VERSION,
        "source": {
            "suite_run_id": suite_run_id,
            "source_report_sha256": _sha256_path(report_path),
            "assignments_sha256": _sha256_path(assignments_path),
            "human_review_sha256": _sha256_path(review_path),
            "dataset_manifest_id": dataset.manifest.manifest_id,
            "dataset_manifest_sha256": _sha256_path(dataset.manifest_path),
        },
        "case_count": len(cases),
        "initial_preferred_count": sum(
            item.human_preference == "initial" for item in cases
        ),
        "capability_unsupported_count": sum(
            item.final_capability.status == "unsupported" for item in cases
        ),
        "initial_preferred_capability_unsupported_count": sum(
            item.human_preference == "initial"
            and item.final_capability.status == "unsupported"
            for item in cases
        ),
        "cases": tuple(cases),
    }
    report_payload["report_hash"] = compute_m6_2_report_hash(report_payload)
    return M6_2StructureDiagnosticReport.model_validate(report_payload, strict=True)


__all__ = [
    "M6_2_CAPABILITY_POLICY_VERSION",
    "M6_2_DIAGNOSTIC_SCHEMA_VERSION",
    "CandidateDiagnosticEvidence",
    "M6_2CaseDiagnostic",
    "M6_2SourceAnchors",
    "M6_2StructureDiagnosticReport",
    "StructureCapabilityAssessment",
    "assess_generator_capability",
    "build_m6_2_structure_diagnostic_report",
    "compute_m6_2_report_hash",
]
