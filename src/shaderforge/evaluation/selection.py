"""候选记录与 current_best 的确定性选择规则."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from shaderforge.contracts import AcceptancePolicy
from shaderforge.evaluation.admission import (
    AdmissionStatus,
    GeneratorAdmissionEvidence,
    MeasurementSeedAdmissionPolicy,
    decide_generator_admission,
)
from shaderforge.evaluation.models import ScoreBreakdownV1
from shaderforge.evaluation.oracle import max_protected_regression

if TYPE_CHECKING:
    from shaderforge.evaluation.runtime_admission import TrustedRuntimeSelectorInput

SelectionReason = Literal[
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
CandidateOrigin = Literal["model", "deterministic"]


def _candidate_origin(value: Any) -> CandidateOrigin:
    """把持久化来源收紧为受支持的候选来源."""
    if value == "model":
        return "model"
    if value == "deterministic":
        return "deterministic"
    raise ValueError("candidate origin 必须是 model 或 deterministic。")


def _score_from_dict(value: dict[str, Any] | None) -> ScoreBreakdownV1 | None:
    if value is None:
        return None
    return ScoreBreakdownV1(
        metric_version=str(value["metric_version"]),
        total_loss=float(value["total_loss"]),
        global_rmse=float(value["global_rmse"]),
        global_mae=float(value["global_mae"]),
        edge_loss=float(value["edge_loss"]),
        geometry_loss=(
            None
            if value.get("geometry_loss") is None
            else float(value["geometry_loss"])
        ),
        representative_pixel_loss=float(value["representative_pixel_loss"]),
        roi_losses=tuple(
            (str(key), float(loss))
            for key, loss in dict(value.get("roi_losses", {})).items()
        ),
        protected_region_losses=tuple(
            (str(key), float(loss))
            for key, loss in dict(value.get("protected_region_losses", {})).items()
        ),
        effective_weights=tuple(
            (str(key), float(weight))
            for key, weight in dict(value.get("effective_weights", {})).items()
        ),
        diagnostics=tuple(str(item) for item in value.get("diagnostics", ())),
    )


@dataclass(frozen=True)
class CandidateRecord:
    """把同一候选的源码、渲染、评分和 provenance 强绑定."""

    candidate_id: str
    parent_candidate_id: str | None
    glsl_sha256: str
    glsl_ref: str
    author_ref: str
    provenance_ref: str
    compile_ref: str | None
    render_ref: str | None
    render_sha256: str | None
    metrics_ref: str | None
    review_ref: str | None
    iteration: int
    changed_problem_domain: str
    prompt_version: str
    model_ref: str
    score_summary: ScoreBreakdownV1 | None
    hard_constraints_passed: bool
    origin: CandidateOrigin = "model"
    generator_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """返回 Artifact 与 LangGraph State 友好的普通字典."""
        return {
            "candidate_id": self.candidate_id,
            "parent_candidate_id": self.parent_candidate_id,
            "glsl_sha256": self.glsl_sha256,
            "glsl_ref": self.glsl_ref,
            "author_ref": self.author_ref,
            "provenance_ref": self.provenance_ref,
            "compile_ref": self.compile_ref,
            "render_ref": self.render_ref,
            "render_sha256": self.render_sha256,
            "metrics_ref": self.metrics_ref,
            "review_ref": self.review_ref,
            "iteration": self.iteration,
            "changed_problem_domain": self.changed_problem_domain,
            "prompt_version": self.prompt_version,
            "model_ref": self.model_ref,
            "score_summary": (
                self.score_summary.to_dict() if self.score_summary else None
            ),
            "hard_constraints_passed": self.hard_constraints_passed,
            "origin": self.origin,
            "generator_version": self.generator_version,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CandidateRecord:
        """从持久化 manifest 恢复并规范化候选记录."""
        return cls(
            candidate_id=str(value["candidate_id"]),
            parent_candidate_id=(
                None
                if value.get("parent_candidate_id") is None
                else str(value["parent_candidate_id"])
            ),
            glsl_sha256=str(value["glsl_sha256"]),
            glsl_ref=str(value["glsl_ref"]),
            author_ref=str(value["author_ref"]),
            provenance_ref=str(value["provenance_ref"]),
            compile_ref=(
                None if value.get("compile_ref") is None else str(value["compile_ref"])
            ),
            render_ref=(
                None if value.get("render_ref") is None else str(value["render_ref"])
            ),
            render_sha256=(
                None
                if value.get("render_sha256") is None
                else str(value["render_sha256"])
            ),
            metrics_ref=(
                None if value.get("metrics_ref") is None else str(value["metrics_ref"])
            ),
            review_ref=(
                None if value.get("review_ref") is None else str(value["review_ref"])
            ),
            iteration=int(value["iteration"]),
            changed_problem_domain=str(value["changed_problem_domain"]),
            prompt_version=str(value["prompt_version"]),
            model_ref=str(value["model_ref"]),
            score_summary=_score_from_dict(value.get("score_summary")),
            hard_constraints_passed=bool(value["hard_constraints_passed"]),
            origin=_candidate_origin(value.get("origin", "model")),
            generator_version=(
                None
                if value.get("generator_version") is None
                else str(value["generator_version"])
            ),
        )


@dataclass(frozen=True)
class CurrentBestDecision:
    """一次候选接受判断及其可审计依据."""

    accepted: bool
    reason: SelectionReason
    total_improvement: float | None
    max_protected_regression: float | None
    admission_status: AdmissionStatus | None = None
    admission_policy_version: str | None = None
    admission_reason_codes: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        """返回可写入候选 manifest 的选择证据."""
        value: dict[str, Any] = {
            "accepted": self.accepted,
            "reason": self.reason,
            "total_improvement": self.total_improvement,
            "max_protected_regression": self.max_protected_regression,
        }
        if self.admission_status is not None:
            value.update(
                {
                    "admission_status": self.admission_status,
                    "admission_policy_version": self.admission_policy_version,
                    "admission_reason_codes": self.admission_reason_codes,
                }
            )
        return value


def select_current_best(
    current_best: CandidateRecord | None,
    candidate: CandidateRecord,
    policy: AcceptancePolicy,
    *,
    admission_policy: MeasurementSeedAdmissionPolicy | None = None,
    admission_evidence: GeneratorAdmissionEvidence | None = None,
    trusted_runtime_admission: TrustedRuntimeSelectorInput | None = None,
) -> CurrentBestDecision:
    """按硬约束、可选 admission、总损失改善和保护区退化决定晋级.

    ``admission_policy`` 缺省为 ``None``，因此现有生产调用保持原选择语义；
    裸 runtime evidence 始终拒绝；只有 resolver-aware adapter 的密封输出可解锁。
    """
    if admission_evidence is not None and trusted_runtime_admission is not None:
        raise ValueError(
            "admission_evidence 与 trusted_runtime_admission 不得同时提供。"
        )
    if (
        admission_evidence is not None or trusted_runtime_admission is not None
    ) and admission_policy is None:
        raise ValueError("admission_evidence 必须同时提供 admission_policy。")
    if not candidate.hard_constraints_passed:
        return CurrentBestDecision(False, "hard_constraints_failed", None, None)
    if candidate.score_summary is None:
        return CurrentBestDecision(False, "score_missing", None, None)
    if current_best is not None and current_best.score_summary is None:
        return CurrentBestDecision(False, "current_best_score_missing", None, None)

    admission = None
    if admission_policy is not None:
        if trusted_runtime_admission is None:
            admission = decide_generator_admission(
                candidate_id=candidate.candidate_id,
                candidate_glsl_sha256=candidate.glsl_sha256,
                candidate_render_sha256=candidate.render_sha256,
                candidate_origin=candidate.origin,
                candidate_generator_version=candidate.generator_version,
                evidence=admission_evidence,
                policy=admission_policy,
            )
        else:
            from shaderforge.evaluation.runtime_admission import (
                decide_trusted_runtime_admission,
            )

            admission = decide_trusted_runtime_admission(
                candidate_id=candidate.candidate_id,
                candidate_glsl_sha256=candidate.glsl_sha256,
                candidate_glsl_ref=candidate.glsl_ref,
                candidate_render_sha256=candidate.render_sha256,
                candidate_render_ref=candidate.render_ref,
                candidate_provenance_ref=candidate.provenance_ref,
                candidate_origin=candidate.origin,
                candidate_generator_version=candidate.generator_version,
                trusted_input=trusted_runtime_admission,
                policy=admission_policy,
            )
        if candidate.origin == "model":
            admission = None
        elif not admission.admitted:
            reason: SelectionReason = (
                "generator_capability_unsupported"
                if admission.status == "unsupported"
                else "generator_capability_unknown"
            )
            return CurrentBestDecision(
                False,
                reason,
                None,
                None,
                admission_status=admission.status,
                admission_policy_version=admission.policy_version,
                admission_reason_codes=admission.reason_codes,
            )

    def decision(
        accepted: bool,
        reason: SelectionReason,
        total_improvement: float | None,
        max_protected_regression: float | None,
    ) -> CurrentBestDecision:
        return CurrentBestDecision(
            accepted,
            reason,
            total_improvement,
            max_protected_regression,
            admission_status=None if admission is None else admission.status,
            admission_policy_version=(
                None if admission is None else admission.policy_version
            ),
            admission_reason_codes=(
                None if admission is None else admission.reason_codes
            ),
        )

    if current_best is None:
        return decision(True, "first_valid_candidate", None, 0.0)
    assert current_best.score_summary is not None

    improvement = (
        current_best.score_summary.total_loss - candidate.score_summary.total_loss
    )
    if improvement < policy.min_total_improvement:
        return decision(
            False,
            "insufficient_total_improvement",
            improvement,
            None,
        )

    previous_regions = set(current_best.score_summary.protected_region_loss_map)
    candidate_regions = set(candidate.score_summary.protected_region_loss_map)
    if not previous_regions.issubset(candidate_regions):
        return decision(
            False,
            "protected_evidence_missing",
            improvement,
            None,
        )

    regression = max_protected_regression(
        current_best.score_summary,
        candidate.score_summary,
    )
    if regression > policy.max_protected_regression:
        return decision(
            False,
            "protected_region_regression",
            improvement,
            regression,
        )
    return decision(True, "improved", improvement, regression)
