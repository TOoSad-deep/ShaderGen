"""Runtime Target structure evidence/verification 的可恢复持久化封套。."""

from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, model_validator

from shaderforge.contracts import FrozenModel, NonEmptyString, Sha256Hex
from shaderforge.contracts.canonical import canonical_json_bytes, canonical_sha256
from shaderforge.evaluation.runtime_structure import (
    RUNTIME_TARGET_STRUCTURE_EVIDENCE_SCHEMA_VERSION,
    RUNTIME_TARGET_STRUCTURE_VERIFICATION_SCHEMA_VERSION,
    RUNTIME_TARGET_STRUCTURE_VERIFIER_VERSION,
    RuntimeTargetStructureEvidence,
    RuntimeTargetStructureVerification,
    VerificationStatus,
    verify_runtime_target_structure,
)
from shaderforge.store import ArtifactCatalog, ArtifactRefV2, ArtifactResolver

RUNTIME_TARGET_STRUCTURE_ARTIFACT_ENVELOPE_SCHEMA_VERSION: Literal[
    "runtime_target_structure_artifact_envelope_v2"
] = "runtime_target_structure_artifact_envelope_v2"

RUNTIME_TARGET_STRUCTURE_EVIDENCE_ARTIFACT_KIND: Literal[
    "runtime_target_structure_evidence"
] = "runtime_target_structure_evidence"
RUNTIME_TARGET_STRUCTURE_VERIFICATION_ARTIFACT_KIND: Literal[
    "runtime_target_structure_verification"
] = "runtime_target_structure_verification"
RUNTIME_TARGET_STRUCTURE_ARTIFACT_ENVELOPE_KIND: Literal[
    "runtime_target_structure_artifact_envelope"
] = "runtime_target_structure_artifact_envelope"
_JSON_CONTENT_TYPE: Literal["application/json"] = "application/json"

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class RuntimeTargetStructureArtifactEnvelope(FrozenModel):
    """把一个 run 的 evidence、verification 与交叉身份冻结为恢复入口。."""

    schema_version: Literal["runtime_target_structure_artifact_envelope_v2"] = (
        RUNTIME_TARGET_STRUCTURE_ARTIFACT_ENVELOPE_SCHEMA_VERSION
    )
    verifier_version: Literal["runtime_target_structure_verifier_v2"] = (
        RUNTIME_TARGET_STRUCTURE_VERIFIER_VERSION
    )
    run_id: NonEmptyString
    evidence_ref: ArtifactRefV2
    evidence_canonical_sha256: Sha256Hex
    verification_ref: ArtifactRefV2
    verification_status: VerificationStatus
    target_source_sha256: Sha256Hex
    target_hypothesis_id: NonEmptyString
    target_hypothesis_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_refs(self) -> RuntimeTargetStructureArtifactEnvelope:
        _require_ref_metadata(
            self.evidence_ref,
            kind=RUNTIME_TARGET_STRUCTURE_EVIDENCE_ARTIFACT_KIND,
            schema_version=RUNTIME_TARGET_STRUCTURE_EVIDENCE_SCHEMA_VERSION,
        )
        _require_ref_metadata(
            self.verification_ref,
            kind=RUNTIME_TARGET_STRUCTURE_VERIFICATION_ARTIFACT_KIND,
            schema_version=RUNTIME_TARGET_STRUCTURE_VERIFICATION_SCHEMA_VERSION,
        )
        return self


class RuntimeTargetStructureArtifactBundle(FrozenModel):
    """已校验且可重放的 runtime structure 持久化结果。."""

    envelope_ref: ArtifactRefV2
    envelope: RuntimeTargetStructureArtifactEnvelope
    evidence: RuntimeTargetStructureEvidence
    verification: RuntimeTargetStructureVerification

    @model_validator(mode="after")
    def _validate_bundle(self) -> RuntimeTargetStructureArtifactBundle:
        _require_ref_metadata(
            self.envelope_ref,
            kind=RUNTIME_TARGET_STRUCTURE_ARTIFACT_ENVELOPE_KIND,
            schema_version=RUNTIME_TARGET_STRUCTURE_ARTIFACT_ENVELOPE_SCHEMA_VERSION,
        )
        _validate_cross_identity(self.envelope, self.evidence, self.verification)
        return self


def _require_ref_metadata(
    ref: ArtifactRefV2,
    *,
    kind: str,
    schema_version: str,
) -> None:
    if (
        ref.kind != kind
        or ref.schema_version != schema_version
        or ref.content_type != _JSON_CONTENT_TYPE
    ):
        raise ValueError(
            "Runtime structure ArtifactRef kind/schema/content-type 不符合契约。"
        )


def _read_exact(resolver: ArtifactResolver, ref: ArtifactRefV2) -> bytes:
    resolved = resolver.resolve(ref.artifact_id)
    if resolved != ref:
        raise ValueError("Artifact resolver 返回的引用身份不一致。")
    data = resolver.read_bytes(ref.artifact_id)
    if not isinstance(data, bytes):
        raise TypeError("Artifact resolver 必须返回 bytes。")
    if len(data) != ref.size_bytes:
        raise ValueError("Artifact bytes 长度与引用不一致。")
    if sha256(data).hexdigest() != ref.sha256:
        raise ValueError("Artifact bytes SHA-256 与引用不一致。")
    return data


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Runtime structure JSON 包含重复 key：{key}。")
        value[key] = item
    return value


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"Runtime structure JSON 拒绝非有限数值：{value}。")


def _parse_strict_json(data: bytes, model_type: type[_ModelT]) -> _ModelT:
    """先拒绝 duplicate/non-finite，再交给 Pydantic JSON strict 模式。."""
    try:
        decoded = data.decode("utf-8")
        parsed = json.loads(
            decoded,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Runtime structure Artifact 不是合法 UTF-8 JSON。") from exc
    if not isinstance(parsed, Mapping):
        raise ValueError("Runtime structure Artifact 必须是 JSON object。")
    return model_type.model_validate_json(data, strict=True)


def _validate_cross_identity(
    envelope: RuntimeTargetStructureArtifactEnvelope,
    evidence: RuntimeTargetStructureEvidence,
    verification: RuntimeTargetStructureVerification,
) -> None:
    evidence_hash = canonical_sha256(evidence)
    if envelope.evidence_canonical_sha256 != evidence_hash:
        raise ValueError("Envelope 与 evidence canonical SHA-256 不一致。")
    if verification.evidence_sha256 != evidence_hash:
        raise ValueError("Verification 未绑定当前 evidence canonical SHA-256。")
    if envelope.verifier_version != evidence.verifier_version:
        raise ValueError("Envelope 与 evidence verifier_version 不一致。")
    if envelope.verifier_version != verification.verifier_version:
        raise ValueError("Envelope 与 verification verifier_version 不一致。")
    if envelope.verification_status != verification.status:
        raise ValueError("Envelope 与 verification status 不一致。")
    evidence_identity = (
        evidence.target_source_sha256,
        evidence.target_hypothesis_id,
        evidence.target_hypothesis_hash,
    )
    verification_identity = (
        verification.target_source_sha256,
        verification.target_hypothesis_id,
        verification.target_hypothesis_hash,
    )
    envelope_identity = (
        envelope.target_source_sha256,
        envelope.target_hypothesis_id,
        envelope.target_hypothesis_hash,
    )
    if evidence_identity != verification_identity:
        raise ValueError("Verification 与 evidence 的 target 身份不一致。")
    if envelope_identity != evidence_identity:
        raise ValueError("Envelope 与 evidence 的 target 身份不一致。")


def materialize_runtime_target_structure_artifacts(
    *,
    catalog: ArtifactCatalog,
    run_id: str,
    evidence: RuntimeTargetStructureEvidence,
) -> RuntimeTargetStructureArtifactBundle:
    """执行 verifier 并物化 evidence、结论和 run 级恢复封套。."""
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id 不能为空。")

    verification = verify_runtime_target_structure(evidence, resolver=catalog)
    evidence_hash = canonical_sha256(evidence)
    if verification.evidence_sha256 != evidence_hash:
        raise ValueError("Verifier 返回的 evidence canonical SHA-256 不一致。")

    evidence_ref = catalog.put(
        run_id=run_id,
        kind=RUNTIME_TARGET_STRUCTURE_EVIDENCE_ARTIFACT_KIND,
        schema_version=RUNTIME_TARGET_STRUCTURE_EVIDENCE_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
        data=canonical_json_bytes(evidence),
    )
    verification_ref = catalog.put(
        run_id=run_id,
        kind=RUNTIME_TARGET_STRUCTURE_VERIFICATION_ARTIFACT_KIND,
        schema_version=RUNTIME_TARGET_STRUCTURE_VERIFICATION_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
        data=canonical_json_bytes(verification),
    )
    envelope = RuntimeTargetStructureArtifactEnvelope(
        run_id=run_id,
        evidence_ref=evidence_ref,
        evidence_canonical_sha256=evidence_hash,
        verification_ref=verification_ref,
        verification_status=verification.status,
        target_source_sha256=evidence.target_source_sha256,
        target_hypothesis_id=evidence.target_hypothesis_id,
        target_hypothesis_hash=evidence.target_hypothesis_hash,
    )
    _validate_cross_identity(envelope, evidence, verification)
    envelope_ref = catalog.put(
        run_id=run_id,
        kind=RUNTIME_TARGET_STRUCTURE_ARTIFACT_ENVELOPE_KIND,
        schema_version=RUNTIME_TARGET_STRUCTURE_ARTIFACT_ENVELOPE_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
        data=canonical_json_bytes(envelope),
    )
    return RuntimeTargetStructureArtifactBundle(
        envelope_ref=envelope_ref,
        envelope=envelope,
        evidence=evidence,
        verification=verification,
    )


def load_runtime_target_structure_artifacts(
    envelope_ref: ArtifactRefV2,
    *,
    resolver: ArtifactResolver,
    run_id: str,
) -> RuntimeTargetStructureArtifactBundle:
    """恢复持久化结果，并从原始 refs 重跑 verifier 后逐字段比对结论。."""
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id 不能为空。")
    _require_ref_metadata(
        envelope_ref,
        kind=RUNTIME_TARGET_STRUCTURE_ARTIFACT_ENVELOPE_KIND,
        schema_version=RUNTIME_TARGET_STRUCTURE_ARTIFACT_ENVELOPE_SCHEMA_VERSION,
    )
    envelope = _parse_strict_json(
        _read_exact(resolver, envelope_ref),
        RuntimeTargetStructureArtifactEnvelope,
    )
    if envelope.run_id != run_id:
        raise ValueError("Envelope 不属于请求恢复的 run_id。")

    evidence = _parse_strict_json(
        _read_exact(resolver, envelope.evidence_ref),
        RuntimeTargetStructureEvidence,
    )
    verification = _parse_strict_json(
        _read_exact(resolver, envelope.verification_ref),
        RuntimeTargetStructureVerification,
    )
    _validate_cross_identity(envelope, evidence, verification)

    replayed = verify_runtime_target_structure(evidence, resolver=resolver)
    if replayed.model_dump(mode="python") != verification.model_dump(mode="python"):
        raise ValueError("持久化 verification 与恢复时重算结论逐字段不一致。")
    return RuntimeTargetStructureArtifactBundle(
        envelope_ref=envelope_ref,
        envelope=envelope,
        evidence=evidence,
        verification=verification,
    )


__all__ = [
    "RUNTIME_TARGET_STRUCTURE_ARTIFACT_ENVELOPE_KIND",
    "RUNTIME_TARGET_STRUCTURE_ARTIFACT_ENVELOPE_SCHEMA_VERSION",
    "RUNTIME_TARGET_STRUCTURE_EVIDENCE_ARTIFACT_KIND",
    "RUNTIME_TARGET_STRUCTURE_VERIFICATION_ARTIFACT_KIND",
    "RuntimeTargetStructureArtifactBundle",
    "RuntimeTargetStructureArtifactEnvelope",
    "load_runtime_target_structure_artifacts",
    "materialize_runtime_target_structure_artifacts",
]
