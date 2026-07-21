"""由可重放 runtime Artifact 派生的 Selector admission capability。."""

from __future__ import annotations

from typing import Final

from shaderforge.evaluation.admission import (
    GeneratorAdmissionDecision,
    GeneratorAdmissionEvidence,
    MeasurementSeedAdmissionPolicy,
    TargetStructureFacts,
    build_generator_admission_evidence,
)
from shaderforge.evaluation.candidate_artifacts import (
    load_candidate_artifact_bundle as load_candidate_artifacts,
)
from shaderforge.evaluation.runtime_structure_artifacts import (
    RuntimeTargetStructureArtifactBundle,
    load_runtime_target_structure_artifacts,
)
from shaderforge.store import ArtifactRefV2, ArtifactResolver

_RUNTIME_STRUCTURE_SEAL: Final[object] = object()
_TRUSTED_SELECTOR_INPUT_SEAL: Final[object] = object()


class RuntimeAdmissionRejected(ValueError):
    """Runtime admission adapter 的稳定 fail-closed 错误。."""

    def __init__(self, code: str) -> None:
        """保存可稳定断言的拒绝 code。."""
        super().__init__(code)
        self.code = code


class VerifiedRuntimeStructureAdmission:
    """只能由 resolver-aware structure 恢复入口产生的进程内 capability。."""

    __slots__ = ("_bundle", "_seal")
    _bundle: RuntimeTargetStructureArtifactBundle
    _seal: object

    def __init__(self) -> None:
        """拒绝绕过 resolver-aware factory 的直接构造。."""
        raise TypeError(
            "VerifiedRuntimeStructureAdmission 只能由 "
            "load_verified_runtime_structure_admission() 构造。"
        )

    def __setattr__(self, name: str, value: object) -> None:
        """拒绝 capability 构造后的字段替换。."""
        del name, value
        raise AttributeError("Verified runtime structure capability 不可修改。")

    @property
    def envelope_ref(self) -> ArtifactRefV2:
        """返回已经重放验证的 envelope 引用。."""
        return self._bundle.envelope_ref

    @property
    def target(self) -> TargetStructureFacts:
        """返回 verifier 重算的结构事实。."""
        target = self._bundle.verification.target
        if target is None:  # pragma: no cover - seal invariant
            raise RuntimeError("密封的 structure capability 缺少 target。")
        return target

    @property
    def target_source_sha256(self) -> str:
        """返回 verifier 绑定的原始目标摘要。."""
        return self._bundle.verification.target_source_sha256

    @property
    def normalized_reference_sha256(self) -> str:
        """返回 verifier 实际读取的规范化参考图摘要。."""
        return self._bundle.evidence.normalized_reference_ref.sha256

    @property
    def target_hypothesis_id(self) -> str:
        """返回已验证的目标假设 id。."""
        return self._bundle.verification.target_hypothesis_id

    @property
    def target_hypothesis_hash(self) -> str:
        """返回已验证的目标假设摘要。."""
        return self._bundle.verification.target_hypothesis_hash

    @classmethod
    def _from_bundle(
        cls,
        bundle: RuntimeTargetStructureArtifactBundle,
        *,
        factory_token: object,
    ) -> VerifiedRuntimeStructureAdmission:
        if factory_token is not _RUNTIME_STRUCTURE_SEAL:
            raise ValueError("runtime_structure_factory_token_invalid")
        value = object.__new__(cls)
        object.__setattr__(value, "_bundle", bundle)
        object.__setattr__(value, "_seal", _RUNTIME_STRUCTURE_SEAL)
        return value

    def _is_sealed(self) -> bool:
        return getattr(self, "_seal", None) is _RUNTIME_STRUCTURE_SEAL


class TrustedRuntimeSelectorInput:
    """Selector 可消费但调用方不能用裸 evidence 字段构造的 capability。."""

    __slots__ = (
        "_candidate_glsl_ref",
        "_candidate_ref",
        "_candidate_generator_id",
        "_candidate_provenance_ref",
        "_candidate_render_ref",
        "_evidence",
        "_seal",
        "_structure_envelope_ref",
    )
    _candidate_glsl_ref: ArtifactRefV2
    _candidate_ref: ArtifactRefV2
    _candidate_generator_id: str
    _candidate_provenance_ref: ArtifactRefV2
    _candidate_render_ref: ArtifactRefV2
    _evidence: GeneratorAdmissionEvidence
    _seal: object
    _structure_envelope_ref: ArtifactRefV2

    def __init__(self) -> None:
        """拒绝绕过 runtime adapter 的直接构造。."""
        raise TypeError(
            "TrustedRuntimeSelectorInput 只能由 resolver-aware runtime adapter 构造。"
        )

    def __setattr__(self, name: str, value: object) -> None:
        """拒绝 trusted input 构造后的字段替换。."""
        del name, value
        raise AttributeError("Trusted runtime Selector input 不可修改。")

    @property
    def candidate_id(self) -> str:
        """返回与真实候选 Artifact 绑定的 candidate id。."""
        return self._evidence.candidate_id

    @property
    def candidate_glsl_ref(self) -> ArtifactRefV2:
        """返回 adapter 实际读取的 GLSL 引用。."""
        return self._candidate_glsl_ref

    @property
    def candidate_ref(self) -> ArtifactRefV2:
        """返回 adapter 完整恢复的 Candidate root 引用。."""
        return self._candidate_ref

    @property
    def candidate_generator_id(self) -> str:
        """返回持久化 provenance 中的 generator id。."""
        return self._candidate_generator_id

    @property
    def candidate_render_ref(self) -> ArtifactRefV2:
        """返回 adapter 实际读取的 render 引用。."""
        return self._candidate_render_ref

    @property
    def candidate_provenance_ref(self) -> ArtifactRefV2:
        """返回 adapter 实际读取的 provenance 引用。."""
        return self._candidate_provenance_ref

    @property
    def structure_envelope_ref(self) -> ArtifactRefV2:
        """返回 adapter 重放过的 structure envelope 引用。."""
        return self._structure_envelope_ref

    @classmethod
    def _from_verified_artifacts(
        cls,
        *,
        evidence: GeneratorAdmissionEvidence,
        structure_envelope_ref: ArtifactRefV2,
        candidate_ref: ArtifactRefV2,
        candidate_glsl_ref: ArtifactRefV2,
        candidate_render_ref: ArtifactRefV2,
        candidate_provenance_ref: ArtifactRefV2,
        candidate_generator_id: str,
        factory_token: object,
    ) -> TrustedRuntimeSelectorInput:
        if factory_token is not _TRUSTED_SELECTOR_INPUT_SEAL:
            raise ValueError("trusted_selector_input_factory_token_invalid")
        value = object.__new__(cls)
        object.__setattr__(value, "_evidence", evidence)
        object.__setattr__(
            value,
            "_structure_envelope_ref",
            structure_envelope_ref,
        )
        object.__setattr__(value, "_candidate_ref", candidate_ref)
        object.__setattr__(value, "_candidate_glsl_ref", candidate_glsl_ref)
        object.__setattr__(value, "_candidate_render_ref", candidate_render_ref)
        object.__setattr__(
            value,
            "_candidate_provenance_ref",
            candidate_provenance_ref,
        )
        object.__setattr__(value, "_candidate_generator_id", candidate_generator_id)
        object.__setattr__(value, "_seal", _TRUSTED_SELECTOR_INPUT_SEAL)
        return value

    def _is_sealed(self) -> bool:
        return getattr(self, "_seal", None) is _TRUSTED_SELECTOR_INPUT_SEAL


def load_verified_runtime_structure_admission(
    envelope_ref: ArtifactRefV2,
    *,
    resolver: ArtifactResolver,
    run_id: str,
) -> VerifiedRuntimeStructureAdmission:
    """恢复并重放 structure envelope；非成功结论不产生 capability。."""
    bundle = load_runtime_target_structure_artifacts(
        envelope_ref,
        resolver=resolver,
        run_id=run_id,
    )
    if bundle.verification.status != "structure_verified":
        raise ValueError("runtime_structure_not_verified")
    if bundle.verification.target is None:
        raise ValueError("runtime_structure_verified_target_missing")
    return VerifiedRuntimeStructureAdmission._from_bundle(
        bundle,
        factory_token=_RUNTIME_STRUCTURE_SEAL,
    )


def load_trusted_runtime_selector_input(
    structure_envelope_ref: ArtifactRefV2,
    candidate_ref: ArtifactRefV2,
    *,
    resolver: ArtifactResolver,
    run_id: str,
) -> TrustedRuntimeSelectorInput:
    """恢复 structure/Candidate V2.4 全闭包后构造 Selector capability。.

    Candidate 必须包含五次 actual beauty、全部 diagnostics、repeatability 与
    RenderedStructure verification；旧 opaque/单 render Candidate 不产生能力。
    """
    try:
        structure = load_verified_runtime_structure_admission(
            structure_envelope_ref,
            resolver=resolver,
            run_id=run_id,
        )
    except (FileNotFoundError, OSError, TypeError, ValueError) as exc:
        raise RuntimeAdmissionRejected("runtime_structure_recovery_failed") from exc
    try:
        candidate_bundle = load_candidate_artifacts(
            candidate_ref,
            resolver=resolver,
            run_id=run_id,
        )
    except (FileNotFoundError, OSError, TypeError, ValueError) as exc:
        raise RuntimeAdmissionRejected("runtime_candidate_recovery_failed") from exc

    candidate = candidate_bundle.candidate
    provenance = candidate_bundle.provenance
    if (
        candidate.target_hypothesis_id != structure.target_hypothesis_id
        or candidate.target_hypothesis_hash != structure.target_hypothesis_hash
    ):
        raise RuntimeAdmissionRejected("runtime_candidate_target_identity_mismatch")
    if provenance.origin != "deterministic":
        raise RuntimeAdmissionRejected("runtime_candidate_origin_not_deterministic")
    if len(candidate.render_refs) != 5:
        raise RuntimeAdmissionRejected("runtime_candidate_render_identity_ambiguous")

    # 这个比较故意使用字符串而不是依赖当前 Literal；后续可以扩展状态，
    # 但旧 opaque 状态在任何情况下都不能生成 trusted input。
    if (
        str(candidate_bundle.semantic_validation_status)
        != "admissible_v2_4_rendered_structure_verified"
    ):
        raise RuntimeAdmissionRejected("runtime_candidate_typed_semantics_not_verified")

    render_ref = candidate.render_refs[0]
    evidence = build_generator_admission_evidence(
        structure.target,
        origin=provenance.origin,
        generator_version=provenance.generator_version,
        evidence_scope="runtime_verified",
        evidence_ref=structure.envelope_ref.artifact_id,
        evidence_sha256=structure.envelope_ref.sha256,
        target_source_sha256=structure.target_source_sha256,
        normalized_reference_sha256=structure.normalized_reference_sha256,
        candidate_id=candidate.candidate_id,
        candidate_glsl_sha256=candidate.glsl_ref.sha256,
        candidate_render_sha256=render_ref.sha256,
    )
    return TrustedRuntimeSelectorInput._from_verified_artifacts(
        evidence=evidence,
        structure_envelope_ref=structure.envelope_ref,
        candidate_ref=candidate_bundle.candidate_ref,
        candidate_glsl_ref=candidate.glsl_ref,
        candidate_render_ref=render_ref,
        candidate_provenance_ref=candidate.provenance_ref,
        candidate_generator_id=provenance.generator_id,
        factory_token=_TRUSTED_SELECTOR_INPUT_SEAL,
    )


def decide_trusted_runtime_admission(
    *,
    candidate_id: str,
    candidate_glsl_sha256: str,
    candidate_glsl_ref: str,
    candidate_render_sha256: str | None,
    candidate_render_ref: str | None,
    candidate_provenance_ref: str,
    candidate_origin: str,
    candidate_generator_version: str | None,
    trusted_input: TrustedRuntimeSelectorInput,
    policy: MeasurementSeedAdmissionPolicy,
) -> GeneratorAdmissionDecision:
    """只消费 adapter 密封输出，拒绝裸 runtime evidence 的等价字段。."""
    if (
        type(trusted_input) is not TrustedRuntimeSelectorInput
        or not trusted_input._is_sealed()
    ):
        return GeneratorAdmissionDecision(
            status="unknown",
            reason_codes=("runtime_selector_input_not_trusted",),
            policy_version=policy.policy_version,
        )
    evidence = trusted_input._evidence
    if evidence.evidence_scope != "runtime_verified":
        return GeneratorAdmissionDecision(
            status="unknown",
            reason_codes=("runtime_selector_input_scope_mismatch",),
            policy_version=policy.policy_version,
        )
    if (
        evidence.admission_policy_version != policy.policy_version
        or evidence.capability_policy_version != policy.capability_policy_version
    ):
        return GeneratorAdmissionDecision(
            status="unknown",
            reason_codes=("runtime_selector_input_policy_version_mismatch",),
            policy_version=policy.policy_version,
        )
    if evidence.evidence_scope not in policy.allowed_evidence_scopes:
        return GeneratorAdmissionDecision(
            status="unknown",
            reason_codes=("generator_admission_evidence_scope_not_allowed",),
            policy_version=policy.policy_version,
        )
    if (
        candidate_origin != "deterministic"
        or evidence.candidate_id != candidate_id
        or evidence.candidate_glsl_sha256 != candidate_glsl_sha256
        or evidence.candidate_render_sha256 != candidate_render_sha256
        or trusted_input.candidate_glsl_ref.artifact_id != candidate_glsl_ref
        or trusted_input.candidate_render_ref.artifact_id != candidate_render_ref
        or trusted_input.candidate_provenance_ref.artifact_id
        != candidate_provenance_ref
        or evidence.origin != candidate_origin
        or evidence.generator_version != candidate_generator_version
    ):
        return GeneratorAdmissionDecision(
            status="unknown",
            reason_codes=("generator_admission_identity_mismatch",),
            policy_version=policy.policy_version,
        )
    if evidence.assessment.status == "supported":
        return GeneratorAdmissionDecision(
            status="admitted",
            reason_codes=evidence.assessment.reason_codes,
            policy_version=policy.policy_version,
        )
    return GeneratorAdmissionDecision(
        status=evidence.assessment.status,
        reason_codes=evidence.assessment.reason_codes,
        policy_version=policy.policy_version,
    )


__all__ = [
    "RuntimeAdmissionRejected",
    "TrustedRuntimeSelectorInput",
    "VerifiedRuntimeStructureAdmission",
    "decide_trusted_runtime_admission",
    "load_trusted_runtime_selector_input",
    "load_verified_runtime_structure_admission",
]
