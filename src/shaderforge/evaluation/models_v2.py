"""V2 不可变 Candidate/Attempt 记录契约。."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field, model_validator

from shaderforge.contracts import (
    FrozenModel,
    NonEmptyString,
    Sha256Hex,
    canonical_sha256,
)
from shaderforge.store import ArtifactRefV2

CANDIDATE_RECORD_HASH_VERSION = "candidate_record_hash_v3"
CANDIDATE_PROVENANCE_HASH_VERSION: Literal["candidate_provenance_hash_v3"] = (
    "candidate_provenance_hash_v3"
)


class CandidateRecordV2(FrozenModel):
    """compile/render/evaluate 完成后一次写入的 Candidate。."""

    schema_version: Literal["candidate_record_v3"] = "candidate_record_v3"
    candidate_id: NonEmptyString
    run_id: NonEmptyString
    parent_candidate_id: NonEmptyString | None
    target_hypothesis_id: NonEmptyString
    target_hypothesis_hash: Sha256Hex
    constraint_set_hash: Sha256Hex
    intent_ref: ArtifactRefV2
    genome_ref: ArtifactRefV2
    topology_hash: Sha256Hex
    parameter_layout_hash: Sha256Hex
    semantic_genome_hash: Sha256Hex
    compilation_ref: ArtifactRefV2
    diagnostic_compilation_ref: ArtifactRefV2
    glsl_ref: ArtifactRefV2
    render_refs: tuple[ArtifactRefV2, ...] = Field(min_length=5, max_length=5)
    render_plan_ref: ArtifactRefV2
    render_progress_ref: ArtifactRefV2
    render_repeatability_ref: ArtifactRefV2
    rendered_structure_evidence_ref: ArtifactRefV2
    rendered_structure_verification_ref: ArtifactRefV2
    constraint_evaluation_ref: ArtifactRefV2
    evaluation_refs: tuple[ArtifactRefV2, ...] = Field(min_length=5, max_length=5)
    provenance_ref: ArtifactRefV2
    record_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_record_hash(self) -> CandidateRecordV2:
        if self.parent_candidate_id == self.candidate_id:
            raise ValueError("Candidate 不得把自身声明为 parent。")
        if len(self.render_refs) != len(self.evaluation_refs):
            raise ValueError("五次 beauty render/evaluation 必须一一对应。")
        if self.record_hash != compute_candidate_record_hash(self):
            raise ValueError("Candidate record_hash 与不可变记录内容不一致。")
        return self


class CandidateProvenanceV2(FrozenModel):
    """Candidate 全证据谱系；opaque V2.2 输出只证明内容完整性。."""

    schema_version: Literal["candidate_provenance_v3"] = "candidate_provenance_v3"
    hash_version: Literal["candidate_provenance_hash_v3"] = (
        CANDIDATE_PROVENANCE_HASH_VERSION
    )
    run_id: NonEmptyString
    candidate_id: NonEmptyString
    parent_candidate_id: NonEmptyString | None
    origin: Literal["model", "deterministic"]
    generator_id: NonEmptyString
    generator_version: NonEmptyString
    target_hypothesis_id: NonEmptyString
    target_hypothesis_hash: Sha256Hex
    constraint_set_hash: Sha256Hex
    intent_id: NonEmptyString
    intent_ref: ArtifactRefV2
    intent_sha256: Sha256Hex
    genome_id: NonEmptyString
    genome_ref: ArtifactRefV2
    genome_sha256: Sha256Hex
    topology_hash: Sha256Hex
    parameter_layout_hash: Sha256Hex
    semantic_genome_hash: Sha256Hex
    compilation_ref: ArtifactRefV2
    compilation_sha256: Sha256Hex
    diagnostic_compilation_ref: ArtifactRefV2
    diagnostic_compilation_sha256: Sha256Hex
    glsl_ref: ArtifactRefV2
    glsl_sha256: Sha256Hex
    render_refs: tuple[ArtifactRefV2, ...] = Field(min_length=5, max_length=5)
    render_sha256s: tuple[Sha256Hex, ...] = Field(min_length=5, max_length=5)
    constraint_evaluation_ref: ArtifactRefV2
    constraint_evaluation_sha256: Sha256Hex
    evaluation_refs: tuple[ArtifactRefV2, ...] = Field(min_length=5, max_length=5)
    evaluation_sha256s: tuple[Sha256Hex, ...] = Field(min_length=5, max_length=5)
    render_plan_ref: ArtifactRefV2
    render_plan_sha256: Sha256Hex
    render_progress_ref: ArtifactRefV2
    render_progress_sha256: Sha256Hex
    render_repeatability_ref: ArtifactRefV2
    render_repeatability_sha256: Sha256Hex
    rendered_structure_evidence_ref: ArtifactRefV2
    rendered_structure_evidence_sha256: Sha256Hex
    rendered_structure_verification_ref: ArtifactRefV2
    rendered_structure_verification_sha256: Sha256Hex
    attempt_id: NonEmptyString | None = None
    renderer_request_refs: tuple[ArtifactRefV2, ...] = ()
    renderer_request_sha256s: tuple[Sha256Hex, ...] = ()
    attempt_evidence_refs: tuple[ArtifactRefV2, ...] = ()
    attempt_evidence_sha256s: tuple[Sha256Hex, ...] = ()
    downstream_semantic_validation: Literal[
        "opaque_content_verified_not_admissible_until_v2_2",
        "typed_candidate_semantics_v2_4_rendered_structure",
    ] = "opaque_content_verified_not_admissible_until_v2_2"
    record_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_provenance(self) -> CandidateProvenanceV2:
        if self.parent_candidate_id == self.candidate_id:
            raise ValueError("Candidate provenance 不得把自身声明为 parent。")
        scalar_bindings = (
            ("intent", self.intent_ref, self.intent_sha256),
            ("genome", self.genome_ref, self.genome_sha256),
            ("compilation", self.compilation_ref, self.compilation_sha256),
            (
                "diagnostic_compilation",
                self.diagnostic_compilation_ref,
                self.diagnostic_compilation_sha256,
            ),
            ("glsl", self.glsl_ref, self.glsl_sha256),
            ("render_plan", self.render_plan_ref, self.render_plan_sha256),
            ("render_progress", self.render_progress_ref, self.render_progress_sha256),
            (
                "render_repeatability",
                self.render_repeatability_ref,
                self.render_repeatability_sha256,
            ),
            (
                "rendered_structure_evidence",
                self.rendered_structure_evidence_ref,
                self.rendered_structure_evidence_sha256,
            ),
            (
                "rendered_structure_verification",
                self.rendered_structure_verification_ref,
                self.rendered_structure_verification_sha256,
            ),
            (
                "constraint_evaluation",
                self.constraint_evaluation_ref,
                self.constraint_evaluation_sha256,
            ),
        )
        for name, ref, expected_hash in scalar_bindings:
            if ref.sha256 != expected_hash:
                raise ValueError(f"Candidate provenance {name} hash 与 ref 不一致。")
        for name, refs, hashes in (
            ("render", self.render_refs, self.render_sha256s),
            ("evaluation", self.evaluation_refs, self.evaluation_sha256s),
            (
                "attempt_evidence",
                self.attempt_evidence_refs,
                self.attempt_evidence_sha256s,
            ),
            (
                "renderer_request",
                self.renderer_request_refs,
                self.renderer_request_sha256s,
            ),
        ):
            if len(refs) != len(hashes):
                raise ValueError(f"Candidate provenance {name} ref/hash 数量不一致。")
            artifact_ids = [item.artifact_id for item in refs]
            if name in {"attempt_evidence", "renderer_request"} and len(
                artifact_ids
            ) != len(set(artifact_ids)):
                raise ValueError(f"Candidate provenance {name} refs 不得重复。")
            if tuple(item.sha256 for item in refs) != hashes:
                raise ValueError(f"Candidate provenance {name} hash 与 refs 不一致。")
        if self.attempt_id is not None:
            if not self.renderer_request_refs:
                raise ValueError("Candidate provenance attempt/request identity 必须完整。")
            if not self.attempt_evidence_refs:
                raise ValueError("Candidate provenance attempt 必须包含调用 evidence。")
        elif (
            self.renderer_request_refs
            or self.renderer_request_sha256s
            or self.attempt_evidence_refs
            or self.attempt_evidence_sha256s
        ):
            raise ValueError("Candidate provenance 无 attempt 时不得包含调用 evidence。")
        if self.record_hash != compute_candidate_provenance_hash(self):
            raise ValueError("Candidate provenance record_hash 不一致。")
        return self


class CandidateAttemptRecord(FrozenModel):
    """中途失败的不可变尝试记录，不复用 Candidate id。."""

    schema_version: Literal["candidate_attempt_v1"] = "candidate_attempt_v1"
    attempt_id: NonEmptyString
    run_id: NonEmptyString
    target_hypothesis_hash: Sha256Hex
    semantic_genome_hash: Sha256Hex
    status: Literal[
        "rejected",
        "compile_failed",
        "render_failed",
        "evaluation_failed",
    ]
    error_code: NonEmptyString
    evidence_refs: tuple[ArtifactRefV2, ...] = Field(min_length=1)


def compute_candidate_record_hash(
    record: CandidateRecordV2 | Mapping[str, Any],
) -> str:
    """计算排除自身字段的完整 Candidate record hash。."""
    if isinstance(record, CandidateRecordV2):
        value = record.model_dump(mode="python", exclude={"record_hash"})
    else:
        value = dict(record)
        value.pop("record_hash", None)
    return canonical_sha256(
        {"hash_version": CANDIDATE_RECORD_HASH_VERSION, "record": value}
    )


def compute_candidate_provenance_hash(
    provenance: CandidateProvenanceV2 | Mapping[str, Any],
) -> str:
    """计算排除自身字段的 Candidate provenance record hash。."""
    if isinstance(provenance, CandidateProvenanceV2):
        value = provenance.model_dump(mode="python", exclude={"record_hash"})
    else:
        value = dict(provenance)
        value.pop("record_hash", None)
    return canonical_sha256(
        {"hash_version": CANDIDATE_PROVENANCE_HASH_VERSION, "record": value}
    )
