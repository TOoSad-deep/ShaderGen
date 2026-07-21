"""V2 Candidate attempt、Renderer request 与阶段证据的严格 Artifact 闭包。"""
# ruff: noqa: D103, D401, D415

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, Literal

from pydantic import Field, model_validator

from shaderforge.contracts import FrozenModel, NonEmptyString, Sha256Hex
from shaderforge.contracts.canonical import canonical_sha256
from shaderforge.evaluation.models_v2 import CandidateAttemptRecord
from shaderforge.store import ArtifactCatalog, ArtifactRefV2, ArtifactResolver

CANDIDATE_ATTEMPT_ARTIFACT_KIND = "candidate_attempt_record"
CANDIDATE_ATTEMPT_SCHEMA_VERSION = "candidate_attempt_v1"
CANDIDATE_ATTEMPT_EVIDENCE_ARTIFACT_KIND = "candidate_attempt_evidence"
CANDIDATE_ATTEMPT_EVIDENCE_SCHEMA_VERSION = "candidate_attempt_evidence_v1"
RENDERER_REQUEST_ARTIFACT_KIND = "renderer_request_receipt"
RENDERER_REQUEST_SCHEMA_VERSION = "renderer_request_receipt_v1"
RENDERER_REQUEST_SCHEMA_VERSION_V2 = "renderer_request_receipt_v2"
_JSON_CONTENT_TYPE = "application/json"


class CandidateAttemptEvidenceV1(FrozenModel):
    """绑定 attempt、阶段与输入身份的不可变错误/调用证据。"""

    schema_version: Literal["candidate_attempt_evidence_v1"] = (
        "candidate_attempt_evidence_v1"
    )
    run_id: NonEmptyString
    attempt_id: NonEmptyString
    target_hypothesis_hash: Sha256Hex
    semantic_genome_hash: Sha256Hex
    stage: Literal["compile", "render", "evaluate", "materialize"]
    outcome: Literal["transient_failure", "failure", "success", "unknown"]
    error_code: NonEmptyString | None
    renderer_request_hash: Sha256Hex | None = None
    call_ordinal: int | None = Field(default=None, ge=1, le=2)

    @model_validator(mode="after")
    def _validate_outcome(self) -> CandidateAttemptEvidenceV1:
        if self.outcome == "success" and self.error_code is not None:
            raise ValueError("成功 attempt evidence 不得包含 error_code。")
        if self.outcome != "success" and self.error_code is None:
            raise ValueError("失败 attempt evidence 必须包含 error_code。")
        if (self.renderer_request_hash is None) != (self.call_ordinal is None):
            raise ValueError("Renderer request hash/call ordinal 必须同时出现。")
        if self.stage != "render" and self.renderer_request_hash is not None:
            raise ValueError("非 Renderer evidence 不得伪造 request/call。")
        return self


class RendererRequestReceiptV1(FrozenModel):
    """Renderer 单次逻辑请求的稳定、可重放 identity。"""

    schema_version: Literal["renderer_request_receipt_v1"] = (
        "renderer_request_receipt_v1"
    )
    hash_version: Literal["renderer_request_hash_v1"] = "renderer_request_hash_v1"
    run_id: NonEmptyString
    attempt_id: NonEmptyString
    target_hypothesis_hash: Sha256Hex
    semantic_genome_hash: Sha256Hex
    compilation_ref: ArtifactRefV2
    glsl_ref: ArtifactRefV2
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    request_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_hash(self) -> RendererRequestReceiptV1:
        expected = compute_renderer_request_hash(self)
        if self.request_hash != expected:
            raise ValueError("Renderer request_hash 与冻结字段不一致。")
        return self


class RendererRequestReceiptV2(FrozenModel):
    """V2.4 每个 beauty capture/diagnostic pass 的稳定逻辑请求。"""

    schema_version: Literal["renderer_request_receipt_v2"] = (
        "renderer_request_receipt_v2"
    )
    hash_version: Literal["renderer_request_hash_v2"] = "renderer_request_hash_v2"
    run_id: NonEmptyString
    attempt_id: NonEmptyString
    target_hypothesis_hash: Sha256Hex
    semantic_genome_hash: Sha256Hex
    compilation_ref: ArtifactRefV2
    glsl_ref: ArtifactRefV2
    render_profile: Literal[
        "beauty_full_v1",
        "subject_visible_delta_full_v1",
        "instance_visible_delta_full_v1",
        "layer_visible_delta_lowres_v1",
    ]
    logical_request_ordinal: int = Field(ge=1)
    beauty_capture_index: int | None = Field(default=None, ge=0, le=4)
    diagnostic_pass_id: NonEmptyString | None = None
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    request_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_identity(self) -> RendererRequestReceiptV2:
        if self.render_profile == "beauty_full_v1":
            if self.beauty_capture_index is None or self.diagnostic_pass_id is not None:
                raise ValueError("Beauty request 必须且只能绑定 capture index。")
        elif self.diagnostic_pass_id is None or self.beauty_capture_index is not None:
            raise ValueError("Diagnostic request 必须且只能绑定 pass id。")
        if self.request_hash != compute_renderer_request_hash(self):
            raise ValueError("Renderer request_hash 与 V2 冻结字段不一致。")
        return self


class LoadedCandidateAttempt(FrozenModel):
    """完成 strict recovery 的 attempt 及其证据。"""

    attempt_ref: ArtifactRefV2
    attempt: CandidateAttemptRecord
    evidence: tuple[CandidateAttemptEvidenceV1, ...] = Field(min_length=1)


def compute_renderer_request_hash(
    value: RendererRequestReceiptV1 | RendererRequestReceiptV2 | dict[str, Any],
) -> str:
    if isinstance(value, (RendererRequestReceiptV1, RendererRequestReceiptV2)):
        payload = value.model_dump(mode="python", exclude={"request_hash"})
    else:
        schema_version = value.get(
            "schema_version", RENDERER_REQUEST_SCHEMA_VERSION
        )
        if schema_version not in {
            RENDERER_REQUEST_SCHEMA_VERSION,
            RENDERER_REQUEST_SCHEMA_VERSION_V2,
        }:
            raise ValueError("Renderer request schema_version 不受支持。")
        hash_version = value.get(
            "hash_version",
            "renderer_request_hash_v2"
            if schema_version == RENDERER_REQUEST_SCHEMA_VERSION_V2
            else "renderer_request_hash_v1",
        )
        payload = {
            "schema_version": schema_version,
            "hash_version": hash_version,
            **{
                key: item
                for key, item in value.items()
                if key not in {"schema_version", "hash_version", "request_hash"}
            },
        }
    return canonical_sha256(payload)


def _read_exact(resolver: ArtifactResolver, ref: ArtifactRefV2) -> bytes:
    if resolver.resolve(ref.artifact_id) != ref:
        raise ValueError("Attempt Artifact resolver 身份不一致。")
    data = resolver.read_bytes(ref.artifact_id)
    if len(data) != ref.size_bytes or sha256(data).hexdigest() != ref.sha256:
        raise ValueError("Attempt Artifact bytes 与 ref 不一致。")
    return data


def _require_metadata(ref: ArtifactRefV2, *, kind: str, schema_version: str) -> None:
    if (
        ref.kind != kind
        or ref.schema_version != schema_version
        or ref.content_type != _JSON_CONTENT_TYPE
    ):
        raise ValueError("Attempt Artifact 元数据不符合冻结契约。")


def _strict_json_model(
    payload: bytes,
    model_type: type[CandidateAttemptRecord]
    | type[CandidateAttemptEvidenceV1]
    | type[RendererRequestReceiptV1]
    | type[RendererRequestReceiptV2],
) -> (
    CandidateAttemptRecord
    | CandidateAttemptEvidenceV1
    | RendererRequestReceiptV1
    | RendererRequestReceiptV2
):
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"Attempt Artifact 包含重复 JSON key：{key}。")
            value[key] = item
        return value

    json.loads(
        payload,
        object_pairs_hook=reject_duplicates,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"Attempt Artifact 包含非法常量：{value}。")
        ),
    )
    return model_type.model_validate_json(payload, strict=True)


def materialize_renderer_request(
    *,
    catalog: ArtifactCatalog,
    run_id: str,
    receipt: RendererRequestReceiptV1 | RendererRequestReceiptV2,
) -> ArtifactRefV2:
    if receipt.run_id != run_id:
        raise ValueError("Renderer request run_id 不一致。")
    return catalog.put(
        run_id=run_id,
        kind=RENDERER_REQUEST_ARTIFACT_KIND,
        schema_version=receipt.schema_version,
        content_type=_JSON_CONTENT_TYPE,
        data=receipt.model_dump_json().encode("utf-8"),
    )


def load_renderer_request(
    ref: ArtifactRefV2, *, resolver: ArtifactResolver, run_id: str
) -> RendererRequestReceiptV1 | RendererRequestReceiptV2:
    _require_metadata(
        ref,
        kind=RENDERER_REQUEST_ARTIFACT_KIND,
        schema_version=ref.schema_version,
    )
    if ref.schema_version == RENDERER_REQUEST_SCHEMA_VERSION:
        loaded = _strict_json_model(
            _read_exact(resolver, ref), RendererRequestReceiptV1
        )
        assert isinstance(loaded, RendererRequestReceiptV1)
    elif ref.schema_version == RENDERER_REQUEST_SCHEMA_VERSION_V2:
        loaded = _strict_json_model(
            _read_exact(resolver, ref), RendererRequestReceiptV2
        )
        assert isinstance(loaded, RendererRequestReceiptV2)
    else:
        raise ValueError("Renderer request schema_version 不受支持。")
    if loaded.run_id != run_id:
        raise ValueError("Renderer request 不属于当前 run。")
    return loaded


def materialize_attempt_evidence(
    *, catalog: ArtifactCatalog, run_id: str, evidence: CandidateAttemptEvidenceV1
) -> ArtifactRefV2:
    if evidence.run_id != run_id:
        raise ValueError("Attempt evidence run_id 不一致。")
    return catalog.put(
        run_id=run_id,
        kind=CANDIDATE_ATTEMPT_EVIDENCE_ARTIFACT_KIND,
        schema_version=CANDIDATE_ATTEMPT_EVIDENCE_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
        data=evidence.model_dump_json().encode("utf-8"),
    )


def load_attempt_evidence(
    ref: ArtifactRefV2, *, resolver: ArtifactResolver, run_id: str
) -> CandidateAttemptEvidenceV1:
    _require_metadata(
        ref,
        kind=CANDIDATE_ATTEMPT_EVIDENCE_ARTIFACT_KIND,
        schema_version=CANDIDATE_ATTEMPT_EVIDENCE_SCHEMA_VERSION,
    )
    loaded = _strict_json_model(_read_exact(resolver, ref), CandidateAttemptEvidenceV1)
    assert isinstance(loaded, CandidateAttemptEvidenceV1)
    if loaded.run_id != run_id:
        raise ValueError("Candidate attempt evidence 不属于当前 run。")
    return loaded


def materialize_candidate_attempt(
    *, catalog: ArtifactCatalog, run_id: str, attempt: CandidateAttemptRecord
) -> ArtifactRefV2:
    if attempt.run_id != run_id:
        raise ValueError("CandidateAttemptRecord run_id 不一致。")
    if len({ref.artifact_id for ref in attempt.evidence_refs}) != len(
        attempt.evidence_refs
    ):
        raise ValueError("CandidateAttemptRecord evidence_refs 不得重复。")
    ref = catalog.put(
        run_id=run_id,
        kind=CANDIDATE_ATTEMPT_ARTIFACT_KIND,
        schema_version=CANDIDATE_ATTEMPT_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
        data=attempt.model_dump_json().encode("utf-8"),
    )
    load_candidate_attempt(ref, resolver=catalog, run_id=run_id)
    return ref


def load_candidate_attempt(
    ref: ArtifactRefV2,
    *,
    resolver: ArtifactResolver,
    run_id: str,
    expected_target_hypothesis_hash: str | None = None,
    expected_semantic_genome_hash: str | None = None,
) -> LoadedCandidateAttempt:
    _require_metadata(
        ref,
        kind=CANDIDATE_ATTEMPT_ARTIFACT_KIND,
        schema_version=CANDIDATE_ATTEMPT_SCHEMA_VERSION,
    )
    loaded = _strict_json_model(_read_exact(resolver, ref), CandidateAttemptRecord)
    assert isinstance(loaded, CandidateAttemptRecord)
    if loaded.run_id != run_id:
        raise ValueError("CandidateAttemptRecord 不属于当前 run。")
    if (
        expected_target_hypothesis_hash is not None
        and loaded.target_hypothesis_hash != expected_target_hypothesis_hash
    ):
        raise ValueError("CandidateAttemptRecord target hypothesis identity 不一致。")
    if (
        expected_semantic_genome_hash is not None
        and loaded.semantic_genome_hash != expected_semantic_genome_hash
    ):
        raise ValueError("CandidateAttemptRecord semantic genome identity 不一致。")
    if len({item.artifact_id for item in loaded.evidence_refs}) != len(
        loaded.evidence_refs
    ):
        raise ValueError("CandidateAttemptRecord evidence_refs 不得重复。")
    evidence: list[CandidateAttemptEvidenceV1] = []
    for evidence_ref in loaded.evidence_refs:
        _require_metadata(
            evidence_ref,
            kind=CANDIDATE_ATTEMPT_EVIDENCE_ARTIFACT_KIND,
            schema_version=CANDIDATE_ATTEMPT_EVIDENCE_SCHEMA_VERSION,
        )
        value = load_attempt_evidence(evidence_ref, resolver=resolver, run_id=run_id)
        if (
            value.run_id != loaded.run_id
            or value.attempt_id != loaded.attempt_id
            or value.target_hypothesis_hash != loaded.target_hypothesis_hash
            or value.semantic_genome_hash != loaded.semantic_genome_hash
        ):
            raise ValueError("CandidateAttemptRecord 与 evidence identity 不一致。")
        evidence.append(value)
    if not any(item.outcome != "success" for item in evidence):
        raise ValueError("失败 attempt closure 必须至少包含一项错误 evidence。")
    return LoadedCandidateAttempt(
        attempt_ref=ref,
        attempt=loaded,
        evidence=tuple(evidence),
    )


__all__ = [
    "CANDIDATE_ATTEMPT_ARTIFACT_KIND",
    "CANDIDATE_ATTEMPT_EVIDENCE_ARTIFACT_KIND",
    "CANDIDATE_ATTEMPT_EVIDENCE_SCHEMA_VERSION",
    "CANDIDATE_ATTEMPT_SCHEMA_VERSION",
    "RENDERER_REQUEST_ARTIFACT_KIND",
    "RENDERER_REQUEST_SCHEMA_VERSION",
    "CandidateAttemptEvidenceV1",
    "LoadedCandidateAttempt",
    "RendererRequestReceiptV1",
    "RendererRequestReceiptV2",
    "compute_renderer_request_hash",
    "load_candidate_attempt",
    "load_attempt_evidence",
    "load_renderer_request",
    "materialize_attempt_evidence",
    "materialize_candidate_attempt",
    "materialize_renderer_request",
]
