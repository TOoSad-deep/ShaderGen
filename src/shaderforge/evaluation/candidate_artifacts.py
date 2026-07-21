"""V2 Candidate 与 provenance 的内容寻址恢复闭包。."""

from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256
from io import BytesIO
from typing import Any, Literal, TypeVar

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, model_validator

from shaderforge.analysis import (
    TargetMeasurementsV2,
    verify_radial_segment_structure_evidence_v1,
)
from shaderforge.compiler import (
    CompilationBundle,
    CompilerAst,
    CompilerParameterTable,
    DiagnosticCompilationBundleV3,
    NodeLineSourceMap,
    compile_diagnostic_passes,
    compile_effect_genome,
)
from shaderforge.contracts import FrozenModel, NonEmptyString, Sha256Hex
from shaderforge.contracts.canonical import canonical_sha256
from shaderforge.evaluation.attempt_artifacts import (
    CandidateAttemptEvidenceV1,
    RendererRequestReceiptV2,
    load_attempt_evidence,
    load_renderer_request,
)
from shaderforge.evaluation.models_v2 import (
    CandidateProvenanceV2,
    CandidateRecordV2,
    compute_candidate_provenance_hash,
    compute_candidate_record_hash,
)
from shaderforge.evaluation.render_runtime_artifacts import (
    RenderPlanV2,
    RenderProgressV2,
    RenderRepeatabilityEvidenceV2,
    build_repeatability_evidence,
    load_render_model,
)
from shaderforge.evaluation.rendered_structure import (
    RenderedStructureEvidenceV4,
    RenderedStructureVerificationV4,
    verify_rendered_structure_evidence,
)
from shaderforge.evaluation.typed_evaluation import (
    BasicEvaluationRecordV2,
    IntentConstraintEvaluationV3,
    evaluate_intent_genome_constraints_v3,
)
from shaderforge.genome import EffectGenome, TypedEffectGenome, compute_genome_hashes
from shaderforge.intent.builder import compute_intent_id
from shaderforge.intent.ir import IntentIR
from shaderforge.store import ArtifactCatalog, ArtifactRefV2, ArtifactResolver

CANDIDATE_RECORD_ARTIFACT_KIND: Literal["candidate_record"] = "candidate_record"
CANDIDATE_PROVENANCE_ARTIFACT_KIND: Literal["candidate_provenance"] = (
    "candidate_provenance"
)
CANDIDATE_PROVENANCE_SCHEMA_VERSION: Literal["candidate_provenance_v3"] = (
    "candidate_provenance_v3"
)

INTENT_ARTIFACT_KIND: Literal["intent"] = "intent"
INTENT_ARTIFACT_SCHEMA_VERSION: Literal["intent_v3"] = "intent_v3"
GENOME_ARTIFACT_KIND: Literal["genome"] = "genome"
GENOME_ARTIFACT_SCHEMA_VERSION: Literal["genome_v0"] = "genome_v0"

# V2.2 尚未提供这些 payload 的 typed Schema。版本名明确声明 opaque，
# loader 只证明元数据、JSON/PNG/UTF-8 形态和内容身份，不授予语义 admission。
COMPILATION_ARTIFACT_KIND: Literal["compilation_bundle"] = "compilation_bundle"
COMPILATION_ARTIFACT_SCHEMA_VERSION: Literal["compilation_bundle_v2_opaque"] = (
    "compilation_bundle_v2_opaque"
)
GLSL_ARTIFACT_KIND: Literal["glsl"] = "glsl"
GLSL_ARTIFACT_SCHEMA_VERSION: Literal["glsl_es_100_v1"] = "glsl_es_100_v1"
RENDER_ARTIFACT_KIND: Literal["render_png"] = "render_png"
RENDER_ARTIFACT_SCHEMA_VERSION: Literal["render_png_v2"] = "render_png_v2"
CONSTRAINT_EVALUATION_ARTIFACT_KIND: Literal["intent_constraint_evaluation"] = (
    "intent_constraint_evaluation"
)
CONSTRAINT_EVALUATION_ARTIFACT_SCHEMA_VERSION: Literal[
    "intent_constraint_evaluation_v2_opaque"
] = "intent_constraint_evaluation_v2_opaque"
EVALUATION_ARTIFACT_KIND: Literal["basic_evaluation_record"] = "basic_evaluation_record"
EVALUATION_ARTIFACT_SCHEMA_VERSION: Literal["basic_evaluation_record_v2_opaque"] = (
    "basic_evaluation_record_v2_opaque"
)

# V2.2 typed 路径与 opaque V2.1 路径使用不同 schema；不能通过改状态字符串升级。
TYPED_COMPILATION_ARTIFACT_SCHEMA_VERSION: Literal["compilation_bundle_v1"] = (
    "compilation_bundle_v1"
)
TYPED_GLSL_ARTIFACT_KIND: Literal["compiled_glsl"] = "compiled_glsl"
TYPED_GLSL_ARTIFACT_SCHEMA_VERSION: Literal["compiled_glsl_es_100_v1"] = (
    "compiled_glsl_es_100_v1"
)
TYPED_CONSTRAINT_EVALUATION_ARTIFACT_SCHEMA_VERSION: Literal[
    "intent_constraint_evaluation_v3"
] = "intent_constraint_evaluation_v3"
TYPED_EVALUATION_ARTIFACT_SCHEMA_VERSION: Literal["basic_evaluation_record_v2"] = (
    "basic_evaluation_record_v2"
)
DIAGNOSTIC_COMPILATION_ARTIFACT_KIND = "diagnostic_compilation_bundle"
DIAGNOSTIC_COMPILATION_ARTIFACT_SCHEMA_VERSION = "diagnostic_compilation_bundle_v3"
RENDERED_STRUCTURE_EVIDENCE_ARTIFACT_KIND = "rendered_structure_evidence"
RENDERED_STRUCTURE_EVIDENCE_ARTIFACT_SCHEMA_VERSION = "rendered_structure_evidence_v4"
RENDERED_STRUCTURE_VERIFICATION_ARTIFACT_KIND = "rendered_structure_verification"
RENDERED_STRUCTURE_VERIFICATION_ARTIFACT_SCHEMA_VERSION = (
    "rendered_structure_verification_v4"
)
COMPILER_AST_ARTIFACT_KIND: Literal["compiler_ast"] = "compiler_ast"
COMPILER_AST_ARTIFACT_SCHEMA_VERSION: Literal["compiler_ast_v1"] = "compiler_ast_v1"
COMPILER_NODE_LINE_MAP_ARTIFACT_KIND: Literal["compiler_node_line_map"] = (
    "compiler_node_line_map"
)
COMPILER_NODE_LINE_MAP_ARTIFACT_SCHEMA_VERSION: Literal["compiler_node_line_map_v1"] = (
    "compiler_node_line_map_v1"
)
COMPILER_PARAMETER_TABLE_ARTIFACT_KIND: Literal["compiler_parameter_table"] = (
    "compiler_parameter_table"
)
COMPILER_PARAMETER_TABLE_ARTIFACT_SCHEMA_VERSION: Literal[
    "compiler_parameter_table_v1"
] = "compiler_parameter_table_v1"

_JSON_CONTENT_TYPE = "application/json"
_TEXT_CONTENT_TYPE = "text/plain; charset=utf-8"
_PNG_CONTENT_TYPE = "image/png"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

CandidateSemanticValidationStatus = Literal[
    "not_admissible_v2_2_typed_schemas_unavailable",
    "admissible_v2_4_rendered_structure_verified",
]
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class CandidateMaterializationInputV2(FrozenModel):
    """创建完成态 Candidate 所需的、尚未含 provenance/ref/hash 的输入。."""

    run_id: NonEmptyString
    candidate_id: NonEmptyString
    parent_candidate_id: NonEmptyString | None
    origin: Literal["model", "deterministic"]
    generator_id: NonEmptyString
    generator_version: NonEmptyString
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
    attempt_id: NonEmptyString | None = None
    renderer_request_refs: tuple[ArtifactRefV2, ...] = ()
    attempt_evidence_refs: tuple[ArtifactRefV2, ...] = ()

    @model_validator(mode="after")
    def _validate_input(self) -> CandidateMaterializationInputV2:
        if self.parent_candidate_id == self.candidate_id:
            raise ValueError("Candidate 不得把自身声明为 parent。")
        for name, refs in (
            ("renderer_request_refs", self.renderer_request_refs),
            ("attempt_evidence_refs", self.attempt_evidence_refs),
        ):
            ids = [item.artifact_id for item in refs]
            if len(ids) != len(set(ids)):
                raise ValueError(f"Candidate input {name} 不得重复。")
        if (self.attempt_id is None) != (not self.renderer_request_refs):
            raise ValueError("Candidate input attempt/request identities 必须同时出现。")
        if self.attempt_id is not None and not self.attempt_evidence_refs:
            raise ValueError("Candidate input attempt 必须包含 Renderer evidence。")
        if self.attempt_id is None and (
            self.renderer_request_refs or self.attempt_evidence_refs
        ):
            raise ValueError("Candidate input 无 attempt 时不得包含 Renderer evidence。")
        return self


class CandidateArtifactBundleV2(FrozenModel):
    """已恢复、内容完整但尚不能冒充 V2.2 语义验证的 Candidate。."""

    candidate_ref: ArtifactRefV2
    candidate: CandidateRecordV2
    provenance: CandidateProvenanceV2
    intent: IntentIR
    genome: EffectGenome
    content_verified_refs: tuple[ArtifactRefV2, ...] = Field(min_length=1)
    semantic_validation_status: Literal[
        "not_admissible_v2_2_typed_schemas_unavailable"
    ] = "not_admissible_v2_2_typed_schemas_unavailable"

    @model_validator(mode="after")
    def _validate_bundle(self) -> CandidateArtifactBundleV2:
        _require_ref_metadata(
            self.candidate_ref,
            kind=CANDIDATE_RECORD_ARTIFACT_KIND,
            schema_version="candidate_record_v3",
            content_type=_JSON_CONTENT_TYPE,
        )
        _validate_cross_identity(
            self.candidate, self.provenance, self.intent, self.genome
        )
        expected = _ordered_closure_refs(
            self.candidate_ref, self.candidate, self.provenance
        )
        if self.content_verified_refs != expected:
            raise ValueError(
                "Candidate bundle content_verified_refs 未精确覆盖证据闭包。"
            )
        ids = [item.artifact_id for item in self.content_verified_refs]
        if len(ids) != len(set(ids)):
            raise ValueError("Candidate 全证据闭包不得复用 artifact_id。")
        return self


class CandidateRenderedClosureProjectionV2(FrozenModel):
    """只由 strict loader 已验证正文重算的 rendered Candidate 投影."""

    schema_version: Literal["candidate_rendered_closure_projection_v1"] = (
        "candidate_rendered_closure_projection_v1"
    )
    candidate_id: NonEmptyString
    candidate_record_hash: Sha256Hex
    target_hypothesis_hash: Sha256Hex
    semantic_genome_hash: Sha256Hex
    render_plan_hash: Sha256Hex
    budget_policy_hash: Sha256Hex
    beauty_request_hashes: tuple[Sha256Hex, ...] = Field(min_length=5, max_length=5)
    beauty_render_sha256s: tuple[Sha256Hex, ...] = Field(min_length=5, max_length=5)
    diagnostic_pass_ids: tuple[NonEmptyString, ...] = Field(min_length=1)
    diagnostic_request_hashes: tuple[Sha256Hex, ...] = Field(min_length=1)
    diagnostic_render_sha256s: tuple[Sha256Hex, ...] = Field(min_length=1)
    renderer_environment_hash: Sha256Hex
    logical_request_count: int = Field(ge=6)
    physical_call_count: int = Field(ge=6)
    replay_count: int = Field(ge=0)
    structure_verification_status: Literal["structure_verified"] = (
        "structure_verified"
    )
    content_closure_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_projection(self) -> CandidateRenderedClosureProjectionV2:
        if len(set(self.beauty_request_hashes)) != 5:
            raise ValueError("Rendered projection 必须保留五个唯一 beauty request。")
        if self.diagnostic_pass_ids != tuple(sorted(set(self.diagnostic_pass_ids))):
            raise ValueError("Rendered projection diagnostic pass 必须按 id 唯一排序。")
        if not (
            len(self.diagnostic_pass_ids)
            == len(self.diagnostic_request_hashes)
            == len(self.diagnostic_render_sha256s)
        ):
            raise ValueError("Rendered projection diagnostic 向量长度不一致。")
        if self.physical_call_count != self.logical_request_count + self.replay_count:
            raise ValueError("Rendered projection physical/replay 计数不闭合。")
        return self


class TypedCandidateArtifactBundleV2(FrozenModel):
    """已重放 Compiler 与 hard closure 的唯一可准入 Candidate bundle。."""

    candidate_ref: ArtifactRefV2
    candidate: CandidateRecordV2
    provenance: CandidateProvenanceV2
    intent: IntentIR
    genome: TypedEffectGenome
    compilation_bundle: CompilationBundle
    diagnostic_compilation_bundle: DiagnosticCompilationBundleV3
    render_plan: RenderPlanV2
    render_progress: RenderProgressV2
    repeatability: RenderRepeatabilityEvidenceV2
    rendered_structure_evidence: RenderedStructureEvidenceV4
    rendered_structure_verification: RenderedStructureVerificationV4
    constraint_evaluation: IntentConstraintEvaluationV3
    basic_evaluations: tuple[BasicEvaluationRecordV2, ...] = Field(
        min_length=5, max_length=5
    )
    content_verified_refs: tuple[ArtifactRefV2, ...] = Field(min_length=1)
    rendered_closure_projection: CandidateRenderedClosureProjectionV2
    semantic_validation_status: Literal[
        "admissible_v2_4_rendered_structure_verified"
    ] = (
        "admissible_v2_4_rendered_structure_verified"
    )

    @model_validator(mode="after")
    def _validate_bundle(self) -> TypedCandidateArtifactBundleV2:
        _require_ref_metadata(
            self.candidate_ref,
            kind=CANDIDATE_RECORD_ARTIFACT_KIND,
            schema_version="candidate_record_v3",
            content_type=_JSON_CONTENT_TYPE,
        )
        _validate_cross_identity(
            self.candidate,
            self.provenance,
            self.intent,
            self.genome,
        )
        expected = _ordered_typed_closure_refs(
            self.candidate_ref,
            self.candidate,
            self.provenance,
            self.compilation_bundle,
            self.diagnostic_compilation_bundle,
            self.render_progress,
            self.repeatability,
            self.rendered_structure_evidence,
            self.constraint_evaluation,
        )
        if self.content_verified_refs != expected:
            raise ValueError(
                "Typed Candidate content_verified_refs 未精确覆盖证据闭包。"
            )
        ids = [item.artifact_id for item in self.content_verified_refs]
        if len(ids) != len(set(ids)):
            raise ValueError("Typed Candidate 全证据闭包不得复用 artifact_id。")
        if self.provenance.downstream_semantic_validation != (
            "typed_candidate_semantics_v2_4_rendered_structure"
        ):
            raise ValueError("Typed Candidate provenance 未声明 V2.4 rendered 路径。")
        if not self.constraint_evaluation.hard_constraints_passed:
            raise ValueError("Typed Candidate hard constraint closure 未通过。")
        derived = _derive_typed_candidate_projection(
            candidate=self.candidate,
            plan=self.render_plan,
            progress=self.render_progress,
            evidence=self.rendered_structure_evidence,
            verification=self.rendered_structure_verification,
            content_verified_refs=self.content_verified_refs,
        )
        if self.rendered_closure_projection != derived:
            raise ValueError("Typed Candidate rendered projection 不是正文重算结果。")
        return self


def _require_ref_metadata(
    ref: ArtifactRefV2,
    *,
    kind: str,
    schema_version: str,
    content_type: str,
) -> None:
    if (
        ref.kind != kind
        or ref.schema_version != schema_version
        or ref.content_type != content_type
    ):
        raise ValueError("Candidate ArtifactRef kind/schema/content-type 不符合契约。")


def _require_bound_run(resolver: ArtifactResolver, run_id: str) -> None:
    resolver_run_id = getattr(resolver, "run_id", None)
    if resolver_run_id is not None and resolver_run_id != run_id:
        raise ValueError("Artifact resolver 与请求恢复的 run_id 不一致。")


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
            raise ValueError(f"Candidate Artifact JSON 包含重复 key：{key}。")
        value[key] = item
    return value


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"Candidate Artifact JSON 拒绝非有限数值：{value}。")


def _parse_json_object(data: bytes) -> Mapping[str, Any]:
    try:
        parsed = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Candidate Artifact 不是合法 UTF-8 JSON。") from exc
    if not isinstance(parsed, Mapping):
        raise ValueError("Candidate Artifact 必须是 JSON object。")
    return parsed


def _parse_typed_json(data: bytes, model_type: type[_ModelT]) -> _ModelT:
    _parse_json_object(data)
    return model_type.model_validate_json(data, strict=True)


def _validate_png(data: bytes) -> None:
    if not data.startswith(_PNG_SIGNATURE):
        raise ValueError("render Artifact 不是 PNG。")
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG":
                raise ValueError("render Artifact 不是 PNG。")
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("render Artifact PNG 无效。") from exc


def _validate_ref_content(
    resolver: ArtifactResolver,
    ref: ArtifactRefV2,
    *,
    kind: str,
    schema_version: str,
    content_type: str,
) -> bytes:
    _require_ref_metadata(
        ref,
        kind=kind,
        schema_version=schema_version,
        content_type=content_type,
    )
    data = _read_exact(resolver, ref)
    if content_type == _JSON_CONTENT_TYPE:
        _parse_json_object(data)
    elif content_type == _TEXT_CONTENT_TYPE:
        try:
            if not data.decode("utf-8").strip():
                raise ValueError("GLSL Artifact 不能为空。")
        except UnicodeDecodeError as exc:
            raise ValueError("GLSL Artifact 必须是 UTF-8。") from exc
    elif content_type == _PNG_CONTENT_TYPE:
        _validate_png(data)
    return data


def _load_inputs(
    resolver: ArtifactResolver,
    candidate: CandidateRecordV2,
) -> tuple[IntentIR, EffectGenome]:
    intent_bytes = _validate_ref_content(
        resolver,
        candidate.intent_ref,
        kind=INTENT_ARTIFACT_KIND,
        schema_version=INTENT_ARTIFACT_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
    )
    genome_bytes = _validate_ref_content(
        resolver,
        candidate.genome_ref,
        kind=GENOME_ARTIFACT_KIND,
        schema_version=GENOME_ARTIFACT_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
    )
    intent = _parse_typed_json(intent_bytes, IntentIR)
    genome = _parse_typed_json(genome_bytes, EffectGenome)
    _validate_ref_content(
        resolver,
        candidate.compilation_ref,
        kind=COMPILATION_ARTIFACT_KIND,
        schema_version=COMPILATION_ARTIFACT_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
    )
    for ref, kind, schema_version in (
        (
            candidate.diagnostic_compilation_ref,
            DIAGNOSTIC_COMPILATION_ARTIFACT_KIND,
            DIAGNOSTIC_COMPILATION_ARTIFACT_SCHEMA_VERSION,
        ),
        (candidate.render_plan_ref, "renderer_plan", "renderer_plan_v3"),
        (candidate.render_progress_ref, "renderer_progress", "renderer_progress_v2"),
        (
            candidate.render_repeatability_ref,
            "render_repeatability_evidence",
            "render_repeatability_evidence_v2",
        ),
        (
            candidate.rendered_structure_evidence_ref,
            RENDERED_STRUCTURE_EVIDENCE_ARTIFACT_KIND,
            RENDERED_STRUCTURE_EVIDENCE_ARTIFACT_SCHEMA_VERSION,
        ),
        (
            candidate.rendered_structure_verification_ref,
            RENDERED_STRUCTURE_VERIFICATION_ARTIFACT_KIND,
            RENDERED_STRUCTURE_VERIFICATION_ARTIFACT_SCHEMA_VERSION,
        ),
    ):
        _validate_ref_content(
            resolver,
            ref,
            kind=kind,
            schema_version=schema_version,
            content_type=_JSON_CONTENT_TYPE,
        )
    _validate_ref_content(
        resolver,
        candidate.glsl_ref,
        kind=GLSL_ARTIFACT_KIND,
        schema_version=GLSL_ARTIFACT_SCHEMA_VERSION,
        content_type=_TEXT_CONTENT_TYPE,
    )
    for ref in candidate.render_refs:
        _validate_ref_content(
            resolver,
            ref,
            kind=RENDER_ARTIFACT_KIND,
            schema_version=RENDER_ARTIFACT_SCHEMA_VERSION,
            content_type=_PNG_CONTENT_TYPE,
        )
    _validate_ref_content(
        resolver,
        candidate.constraint_evaluation_ref,
        kind=CONSTRAINT_EVALUATION_ARTIFACT_KIND,
        schema_version=CONSTRAINT_EVALUATION_ARTIFACT_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
    )
    for ref in candidate.evaluation_refs:
        _validate_ref_content(
            resolver,
            ref,
            kind=EVALUATION_ARTIFACT_KIND,
            schema_version=EVALUATION_ARTIFACT_SCHEMA_VERSION,
            content_type=_JSON_CONTENT_TYPE,
        )
    return intent, genome


def _validate_cross_identity(
    candidate: CandidateRecordV2,
    provenance: CandidateProvenanceV2,
    intent: IntentIR,
    genome: EffectGenome,
) -> None:
    candidate_identity = (
        candidate.run_id,
        candidate.candidate_id,
        candidate.parent_candidate_id,
        candidate.target_hypothesis_id,
        candidate.target_hypothesis_hash,
        candidate.constraint_set_hash,
    )
    provenance_identity = (
        provenance.run_id,
        provenance.candidate_id,
        provenance.parent_candidate_id,
        provenance.target_hypothesis_id,
        provenance.target_hypothesis_hash,
        provenance.constraint_set_hash,
    )
    if candidate_identity != provenance_identity:
        raise ValueError("Candidate 与 provenance 的 run/candidate/target 身份不一致。")
    candidate_refs = (
        candidate.intent_ref,
        candidate.genome_ref,
        candidate.compilation_ref,
        candidate.diagnostic_compilation_ref,
        candidate.glsl_ref,
        candidate.render_refs,
        candidate.render_plan_ref,
        candidate.render_progress_ref,
        candidate.render_repeatability_ref,
        candidate.rendered_structure_evidence_ref,
        candidate.rendered_structure_verification_ref,
        candidate.constraint_evaluation_ref,
        candidate.evaluation_refs,
    )
    provenance_refs = (
        provenance.intent_ref,
        provenance.genome_ref,
        provenance.compilation_ref,
        provenance.diagnostic_compilation_ref,
        provenance.glsl_ref,
        provenance.render_refs,
        provenance.render_plan_ref,
        provenance.render_progress_ref,
        provenance.render_repeatability_ref,
        provenance.rendered_structure_evidence_ref,
        provenance.rendered_structure_verification_ref,
        provenance.constraint_evaluation_ref,
        provenance.evaluation_refs,
    )
    if candidate_refs != provenance_refs:
        raise ValueError("Candidate 与 provenance 的证据 refs 不一致。")
    if (
        intent.intent_id != provenance.intent_id
        or intent.intent_id != compute_intent_id(intent)
        or intent.target_hypothesis_id != candidate.target_hypothesis_id
        or intent.target_hypothesis_hash != candidate.target_hypothesis_hash
        or intent.constraint_set_hash != candidate.constraint_set_hash
    ):
        raise ValueError("Candidate/provenance 与 Intent 身份不一致。")
    genome_hashes = compute_genome_hashes(genome)
    if (
        genome.genome_id != provenance.genome_id
        or genome.provenance.intent_id != intent.intent_id
        or genome.provenance.target_hypothesis_id != candidate.target_hypothesis_id
        or genome.provenance.target_hypothesis_hash != candidate.target_hypothesis_hash
        or genome_hashes.topology_hash != candidate.topology_hash
        or genome_hashes.parameter_layout_hash != candidate.parameter_layout_hash
        or genome_hashes.semantic_genome_hash != candidate.semantic_genome_hash
        or provenance.topology_hash != candidate.topology_hash
        or provenance.parameter_layout_hash != candidate.parameter_layout_hash
        or provenance.semantic_genome_hash != candidate.semantic_genome_hash
    ):
        raise ValueError("Candidate/provenance 与 Genome 身份或 hashes 不一致。")


def _ordered_closure_refs(
    candidate_ref: ArtifactRefV2,
    candidate: CandidateRecordV2,
    provenance: CandidateProvenanceV2,
) -> tuple[ArtifactRefV2, ...]:
    return _unique_refs((
        candidate_ref,
        candidate.provenance_ref,
        candidate.intent_ref,
        candidate.genome_ref,
        candidate.compilation_ref,
        candidate.diagnostic_compilation_ref,
        candidate.glsl_ref,
        *candidate.render_refs,
        candidate.render_plan_ref,
        candidate.render_progress_ref,
        candidate.render_repeatability_ref,
        candidate.rendered_structure_evidence_ref,
        candidate.rendered_structure_verification_ref,
        candidate.constraint_evaluation_ref,
        *candidate.evaluation_refs,
        *provenance.renderer_request_refs,
        *provenance.attempt_evidence_refs,
    ))


def _ordered_typed_closure_refs(
    candidate_ref: ArtifactRefV2,
    candidate: CandidateRecordV2,
    provenance: CandidateProvenanceV2,
    compilation: CompilationBundle,
    diagnostic: DiagnosticCompilationBundleV3,
    progress: RenderProgressV2,
    repeatability: RenderRepeatabilityEvidenceV2,
    structure_evidence: RenderedStructureEvidenceV4,
    constraint_evaluation: IntentConstraintEvaluationV3,
) -> tuple[ArtifactRefV2, ...]:
    """返回 typed Candidate 的完整、无重复内容闭包。."""
    outcome_refs = tuple(
        ref
        for outcome in progress.outcomes
        for ref in (
            outcome.renderer_request_ref,
            outcome.attempt_evidence_ref,
            *(
                (outcome.renderer_environment_ref,)
                if outcome.renderer_environment_ref is not None
                else ()
            ),
            *((outcome.render_ref,) if outcome.render_ref is not None else ()),
        )
    )
    diagnostic_receipt_refs = tuple(
        ref
        for receipt in structure_evidence.diagnostic_receipts
        for ref in (
            receipt.source_ref,
            receipt.renderer_request_ref,
            receipt.renderer_environment_ref,
            receipt.render_ref,
        )
    )
    return _unique_refs((
        candidate_ref,
        candidate.provenance_ref,
        candidate.intent_ref,
        candidate.genome_ref,
        candidate.compilation_ref,
        candidate.diagnostic_compilation_ref,
        candidate.glsl_ref,
        compilation.node_line_map_ref,
        compilation.compiler_parameter_table_ref,
        compilation.ast_ref,
        *(item.source_ref for item in diagnostic.passes),
        *candidate.render_refs,
        candidate.render_plan_ref,
        candidate.render_progress_ref,
        candidate.render_repeatability_ref,
        candidate.rendered_structure_evidence_ref,
        candidate.rendered_structure_verification_ref,
        candidate.constraint_evaluation_ref,
        constraint_evaluation.target_measurements_ref,
        *candidate.evaluation_refs,
        *provenance.renderer_request_refs,
        *provenance.attempt_evidence_refs,
        *outcome_refs,
        *repeatability.capture_request_refs,
        *repeatability.capture_render_refs,
        repeatability.renderer_environment_ref,
        structure_evidence.intent_ref,
        structure_evidence.genome_ref,
        structure_evidence.compilation_ref,
        structure_evidence.diagnostic_compilation_ref,
        structure_evidence.beauty_renderer_request_ref,
        structure_evidence.renderer_environment_ref,
        structure_evidence.beauty_render_ref,
        *diagnostic_receipt_refs,
    ))


def _derive_typed_candidate_projection(
    *,
    candidate: CandidateRecordV2,
    plan: RenderPlanV2,
    progress: RenderProgressV2,
    evidence: RenderedStructureEvidenceV4,
    verification: RenderedStructureVerificationV4,
    content_verified_refs: tuple[ArtifactRefV2, ...],
) -> CandidateRenderedClosureProjectionV2:
    if verification.status != "structure_verified":
        raise ValueError("Rendered projection 只接受 structure_verified 正文。")
    successful = tuple(item for item in progress.outcomes if item.outcome == "success")
    beauty = successful[:5]
    diagnostic = successful[5:]
    return CandidateRenderedClosureProjectionV2(
        candidate_id=candidate.candidate_id,
        candidate_record_hash=candidate.record_hash,
        target_hypothesis_hash=candidate.target_hypothesis_hash,
        semantic_genome_hash=candidate.semantic_genome_hash,
        render_plan_hash=plan.plan_hash,
        budget_policy_hash=plan.budget_policy_hash,
        beauty_request_hashes=tuple(item.renderer_request_hash for item in beauty),
        beauty_render_sha256s=tuple(
            item.render_sha256 for item in beauty if item.render_sha256 is not None
        ),
        diagnostic_pass_ids=tuple(
            item.diagnostic_pass_id
            for item in plan.items[5:]
            if item.diagnostic_pass_id is not None
        ),
        diagnostic_request_hashes=tuple(
            item.renderer_request_hash for item in diagnostic
        ),
        diagnostic_render_sha256s=tuple(
            item.render_sha256 for item in diagnostic if item.render_sha256 is not None
        ),
        renderer_environment_hash=evidence.renderer_environment_hash,
        logical_request_count=len(plan.items),
        physical_call_count=len(progress.outcomes),
        replay_count=len(progress.outcomes) - len(plan.items),
        structure_verification_status="structure_verified",
        content_closure_hash=canonical_sha256(content_verified_refs),
    )


def _unique_refs(refs: tuple[ArtifactRefV2, ...]) -> tuple[ArtifactRefV2, ...]:
    """按首次逻辑出现顺序返回内容寻址 Artifact 的物理闭包."""
    result: list[ArtifactRefV2] = []
    seen: set[str] = set()
    for ref in refs:
        if ref.artifact_id not in seen:
            seen.add(ref.artifact_id)
            result.append(ref)
    return tuple(result)


def _validate_attempt_provenance(
    resolver: ArtifactResolver,
    provenance: CandidateProvenanceV2,
) -> None:
    if provenance.attempt_id is None:
        return
    requests = tuple(
        load_renderer_request(ref, resolver=resolver, run_id=provenance.run_id)
        for ref in provenance.renderer_request_refs
    )
    request_hashes = {request.request_hash for request in requests}
    if len(request_hashes) != len(requests) or any(
        request.attempt_id != provenance.attempt_id
        or request.target_hypothesis_hash != provenance.target_hypothesis_hash
        or request.semantic_genome_hash != provenance.semantic_genome_hash
        for request in requests
    ):
        raise ValueError("Candidate provenance 与 Renderer requests identity 不一致。")
    evidence = tuple(
        load_attempt_evidence(ref, resolver=resolver, run_id=provenance.run_id)
        for ref in provenance.attempt_evidence_refs
    )
    if any(
        item.attempt_id != provenance.attempt_id
        or item.target_hypothesis_hash != provenance.target_hypothesis_hash
        or item.semantic_genome_hash != provenance.semantic_genome_hash
        or (
            item.renderer_request_hash is not None
            and item.renderer_request_hash not in request_hashes
        )
        for item in evidence
    ):
        raise ValueError("Candidate provenance 与 attempt evidence identity 不一致。")
    renderer_calls = tuple(
        item for item in evidence if item.renderer_request_hash is not None
    )
    by_request: dict[str, list[CandidateAttemptEvidenceV1]] = {}
    for item in renderer_calls:
        assert item.renderer_request_hash is not None
        by_request.setdefault(item.renderer_request_hash, []).append(item)
    if set(by_request) != request_hashes:
        raise ValueError("完成态 Candidate 未完整覆盖全部 Renderer requests。")
    for calls in by_request.values():
        ordinals = tuple(item.call_ordinal for item in calls)
        if ordinals not in {(1,), (1, 2)} or calls[-1].outcome != "success":
            raise ValueError("每个完成态 Renderer request 必须有界并以成功闭合。")


def _candidate_from_input(
    value: CandidateMaterializationInputV2,
    provenance_ref: ArtifactRefV2,
) -> CandidateRecordV2:
    raw: dict[str, Any] = {
        "schema_version": "candidate_record_v3",
        "run_id": value.run_id,
        "candidate_id": value.candidate_id,
        "parent_candidate_id": value.parent_candidate_id,
        "target_hypothesis_id": value.target_hypothesis_id,
        "target_hypothesis_hash": value.target_hypothesis_hash,
        "constraint_set_hash": value.constraint_set_hash,
        "intent_ref": value.intent_ref,
        "genome_ref": value.genome_ref,
        "topology_hash": value.topology_hash,
        "parameter_layout_hash": value.parameter_layout_hash,
        "semantic_genome_hash": value.semantic_genome_hash,
        "compilation_ref": value.compilation_ref,
        "diagnostic_compilation_ref": value.diagnostic_compilation_ref,
        "glsl_ref": value.glsl_ref,
        "render_refs": value.render_refs,
        "render_plan_ref": value.render_plan_ref,
        "render_progress_ref": value.render_progress_ref,
        "render_repeatability_ref": value.render_repeatability_ref,
        "rendered_structure_evidence_ref": value.rendered_structure_evidence_ref,
        "rendered_structure_verification_ref": value.rendered_structure_verification_ref,
        "constraint_evaluation_ref": value.constraint_evaluation_ref,
        "evaluation_refs": value.evaluation_refs,
        "provenance_ref": provenance_ref,
    }
    raw["record_hash"] = compute_candidate_record_hash(raw)
    return CandidateRecordV2.model_validate(raw, strict=True)


def _provenance_from_input(
    value: CandidateMaterializationInputV2,
    *,
    intent: IntentIR,
    genome: EffectGenome,
    typed_semantics: bool = False,
) -> CandidateProvenanceV2:
    raw: dict[str, Any] = {
        "schema_version": CANDIDATE_PROVENANCE_SCHEMA_VERSION,
        "hash_version": "candidate_provenance_hash_v3",
        "run_id": value.run_id,
        "candidate_id": value.candidate_id,
        "parent_candidate_id": value.parent_candidate_id,
        "origin": value.origin,
        "generator_id": value.generator_id,
        "generator_version": value.generator_version,
        "target_hypothesis_id": value.target_hypothesis_id,
        "target_hypothesis_hash": value.target_hypothesis_hash,
        "constraint_set_hash": value.constraint_set_hash,
        "intent_id": intent.intent_id,
        "intent_ref": value.intent_ref,
        "intent_sha256": value.intent_ref.sha256,
        "genome_id": genome.genome_id,
        "genome_ref": value.genome_ref,
        "genome_sha256": value.genome_ref.sha256,
        "topology_hash": value.topology_hash,
        "parameter_layout_hash": value.parameter_layout_hash,
        "semantic_genome_hash": value.semantic_genome_hash,
        "compilation_ref": value.compilation_ref,
        "compilation_sha256": value.compilation_ref.sha256,
        "diagnostic_compilation_ref": value.diagnostic_compilation_ref,
        "diagnostic_compilation_sha256": value.diagnostic_compilation_ref.sha256,
        "glsl_ref": value.glsl_ref,
        "glsl_sha256": value.glsl_ref.sha256,
        "render_refs": value.render_refs,
        "render_sha256s": tuple(item.sha256 for item in value.render_refs),
        "constraint_evaluation_ref": value.constraint_evaluation_ref,
        "constraint_evaluation_sha256": value.constraint_evaluation_ref.sha256,
        "evaluation_refs": value.evaluation_refs,
        "evaluation_sha256s": tuple(item.sha256 for item in value.evaluation_refs),
        "render_plan_ref": value.render_plan_ref,
        "render_plan_sha256": value.render_plan_ref.sha256,
        "render_progress_ref": value.render_progress_ref,
        "render_progress_sha256": value.render_progress_ref.sha256,
        "render_repeatability_ref": value.render_repeatability_ref,
        "render_repeatability_sha256": value.render_repeatability_ref.sha256,
        "rendered_structure_evidence_ref": value.rendered_structure_evidence_ref,
        "rendered_structure_evidence_sha256": value.rendered_structure_evidence_ref.sha256,
        "rendered_structure_verification_ref": value.rendered_structure_verification_ref,
        "rendered_structure_verification_sha256": value.rendered_structure_verification_ref.sha256,
        "attempt_id": value.attempt_id,
        "renderer_request_refs": value.renderer_request_refs,
        "renderer_request_sha256s": tuple(
            item.sha256 for item in value.renderer_request_refs
        ),
        "attempt_evidence_refs": value.attempt_evidence_refs,
        "attempt_evidence_sha256s": tuple(
            item.sha256 for item in value.attempt_evidence_refs
        ),
        "downstream_semantic_validation": (
            "typed_candidate_semantics_v2_4_rendered_structure"
            if typed_semantics
            else "opaque_content_verified_not_admissible_until_v2_2"
        ),
    }
    raw["record_hash"] = compute_candidate_provenance_hash(raw)
    return CandidateProvenanceV2.model_validate(raw, strict=True)


def materialize_candidate_artifacts(
    *,
    catalog: ArtifactCatalog,
    run_id: str,
    candidate_input: CandidateMaterializationInputV2,
) -> CandidateArtifactBundleV2:
    """校验全部上游 bytes 后，一次物化 provenance 和不可变 Candidate。."""
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id 不能为空。")
    _require_bound_run(catalog, run_id)
    if candidate_input.run_id != run_id:
        raise ValueError("Candidate input 不属于请求物化的 run_id。")

    # 先用一个尚无 provenance 的临时 Candidate 形状完成全部输入验证。
    placeholder_ref = ArtifactRefV2(
        artifact_id="candidate_provenance_placeholder",
        sha256="0" * 64,
        kind=CANDIDATE_PROVENANCE_ARTIFACT_KIND,
        schema_version=CANDIDATE_PROVENANCE_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
        size_bytes=0,
    )
    placeholder = _candidate_from_input(candidate_input, placeholder_ref)
    intent, genome = _load_inputs(catalog, placeholder)
    provenance = _provenance_from_input(candidate_input, intent=intent, genome=genome)
    _validate_cross_identity(placeholder, provenance, intent, genome)

    provenance_ref = catalog.put(
        run_id=run_id,
        kind=CANDIDATE_PROVENANCE_ARTIFACT_KIND,
        schema_version=CANDIDATE_PROVENANCE_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
        data=provenance.model_dump_json().encode("utf-8"),
    )
    candidate = _candidate_from_input(candidate_input, provenance_ref)
    _validate_cross_identity(candidate, provenance, intent, genome)
    candidate_ref = catalog.put(
        run_id=run_id,
        kind=CANDIDATE_RECORD_ARTIFACT_KIND,
        schema_version="candidate_record_v3",
        content_type=_JSON_CONTENT_TYPE,
        data=candidate.model_dump_json().encode("utf-8"),
    )
    return load_candidate_artifacts(candidate_ref, resolver=catalog, run_id=run_id)


def load_candidate_artifacts(
    candidate_ref: ArtifactRefV2,
    *,
    resolver: ArtifactResolver,
    run_id: str,
) -> CandidateArtifactBundleV2:
    """从 Candidate root ref 恢复全闭包；缺失、篡改或跨 run 一律失败。."""
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id 不能为空。")
    _require_bound_run(resolver, run_id)
    _require_ref_metadata(
        candidate_ref,
        kind=CANDIDATE_RECORD_ARTIFACT_KIND,
        schema_version="candidate_record_v3",
        content_type=_JSON_CONTENT_TYPE,
    )
    candidate = _parse_typed_json(
        _read_exact(resolver, candidate_ref), CandidateRecordV2
    )
    if candidate.run_id != run_id:
        raise ValueError("Candidate 不属于请求恢复的 run_id。")
    _require_ref_metadata(
        candidate.provenance_ref,
        kind=CANDIDATE_PROVENANCE_ARTIFACT_KIND,
        schema_version=CANDIDATE_PROVENANCE_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
    )
    provenance = _parse_typed_json(
        _read_exact(resolver, candidate.provenance_ref), CandidateProvenanceV2
    )
    if provenance.run_id != run_id:
        raise ValueError("Candidate provenance 不属于请求恢复的 run_id。")
    intent, genome = _load_inputs(resolver, candidate)
    _validate_cross_identity(candidate, provenance, intent, genome)
    _validate_attempt_provenance(resolver, provenance)
    return CandidateArtifactBundleV2(
        candidate_ref=candidate_ref,
        candidate=candidate,
        provenance=provenance,
        intent=intent,
        genome=genome,
        content_verified_refs=_ordered_closure_refs(
            candidate_ref, candidate, provenance
        ),
    )


def _load_typed_candidate_payloads(
    resolver: ArtifactResolver,
    candidate: CandidateRecordV2,
    *,
    structure_evidence: RenderedStructureEvidenceV4,
    structure_verification: RenderedStructureVerificationV4,
) -> tuple[
    IntentIR,
    TypedEffectGenome,
    CompilationBundle,
    IntentConstraintEvaluationV3,
    tuple[BasicEvaluationRecordV2, ...],
]:
    """读取 typed 下游 payload，并重放 Compiler 与约束闭包。."""
    intent_bytes = _validate_ref_content(
        resolver,
        candidate.intent_ref,
        kind=INTENT_ARTIFACT_KIND,
        schema_version=INTENT_ARTIFACT_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
    )
    genome_bytes = _validate_ref_content(
        resolver,
        candidate.genome_ref,
        kind=GENOME_ARTIFACT_KIND,
        schema_version=GENOME_ARTIFACT_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
    )
    intent = _parse_typed_json(intent_bytes, IntentIR)
    genome = _parse_typed_json(genome_bytes, TypedEffectGenome)

    compilation_bytes = _validate_ref_content(
        resolver,
        candidate.compilation_ref,
        kind=COMPILATION_ARTIFACT_KIND,
        schema_version=TYPED_COMPILATION_ARTIFACT_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
    )
    compilation = _parse_typed_json(compilation_bytes, CompilationBundle)
    if candidate.glsl_ref != compilation.glsl_ref:
        raise ValueError("Candidate GLSL ref 与 CompilationBundle 不一致。")
    glsl_bytes = _validate_ref_content(
        resolver,
        candidate.glsl_ref,
        kind=TYPED_GLSL_ARTIFACT_KIND,
        schema_version=TYPED_GLSL_ARTIFACT_SCHEMA_VERSION,
        content_type="text/x-glsl; charset=utf-8",
    )
    line_map = _parse_typed_json(
        _validate_ref_content(
            resolver,
            compilation.node_line_map_ref,
            kind=COMPILER_NODE_LINE_MAP_ARTIFACT_KIND,
            schema_version=COMPILER_NODE_LINE_MAP_ARTIFACT_SCHEMA_VERSION,
            content_type=_JSON_CONTENT_TYPE,
        ),
        NodeLineSourceMap,
    )
    parameter_table = _parse_typed_json(
        _validate_ref_content(
            resolver,
            compilation.compiler_parameter_table_ref,
            kind=COMPILER_PARAMETER_TABLE_ARTIFACT_KIND,
            schema_version=COMPILER_PARAMETER_TABLE_ARTIFACT_SCHEMA_VERSION,
            content_type=_JSON_CONTENT_TYPE,
        ),
        CompilerParameterTable,
    )
    ast = _parse_typed_json(
        _validate_ref_content(
            resolver,
            compilation.ast_ref,
            kind=COMPILER_AST_ARTIFACT_KIND,
            schema_version=COMPILER_AST_ARTIFACT_SCHEMA_VERSION,
            content_type=_JSON_CONTENT_TYPE,
        ),
        CompilerAst,
    )
    try:
        replay = compile_effect_genome(genome)
    except RuntimeError as exc:
        raise ValueError(
            "Typed Candidate deterministic compiler replay 失败。"
        ) from exc
    if (
        glsl_bytes != replay.glsl_source.encode("utf-8")
        or compilation.semantic_genome_hash != replay.semantic_genome_hash
        or compilation.glsl_sha256 != replay.glsl_sha256
        or compilation.estimated_ops != replay.estimated_ops
        or compilation.numerical_risks != replay.numerical_risks
        or compilation.diagnostics != replay.diagnostics
        or ast != replay.ast
        or line_map != replay.node_line_map
        or parameter_table != replay.compiler_parameter_table
    ):
        raise ValueError(
            "CompilationBundle/AST/source-map/parameter-table/GLSL 重放不一致。"
        )
    if candidate.semantic_genome_hash != replay.semantic_genome_hash:
        raise ValueError("Candidate semantic_genome_hash 与 Compiler replay 不一致。")

    for ref in candidate.render_refs:
        _validate_ref_content(
            resolver,
            ref,
            kind=RENDER_ARTIFACT_KIND,
            schema_version=RENDER_ARTIFACT_SCHEMA_VERSION,
            content_type=_PNG_CONTENT_TYPE,
        )
    constraint_evaluation = _parse_typed_json(
        _validate_ref_content(
            resolver,
            candidate.constraint_evaluation_ref,
            kind=CONSTRAINT_EVALUATION_ARTIFACT_KIND,
            schema_version=TYPED_CONSTRAINT_EVALUATION_ARTIFACT_SCHEMA_VERSION,
            content_type=_JSON_CONTENT_TYPE,
        ),
        IntentConstraintEvaluationV3,
    )
    target_measurements = _parse_typed_json(
        _validate_ref_content(
            resolver,
            constraint_evaluation.target_measurements_ref,
            kind="target_measurements",
            schema_version="target_measurements_v2_2",
            content_type=_JSON_CONTENT_TYPE,
        ),
        TargetMeasurementsV2,
    )
    matching_hypothesis = next(
        (
            item
            for item in target_measurements.target_hypotheses
            if item.hypothesis_id == intent.target_hypothesis_id
        ),
        None,
    )
    if (
        target_measurements.target_sha256 != intent.target_sha256
        or matching_hypothesis is None
        or matching_hypothesis.hypothesis_hash != intent.target_hypothesis_hash
    ):
        raise ValueError("Evaluation V3 target measurements/Intent identity 不一致。")
    subject = next(
        (item for item in intent.objects if item.object_id == "subject"), None
    )
    if subject is None or (
        subject.radial_segment_evidence_ref
        != matching_hypothesis.radial_segment_evidence_ref
    ):
        raise ValueError("Intent 与 Measurements radial segment binding 不一致。")
    if matching_hypothesis.radial_segment_evidence_ref is not None:
        segment_evidence = verify_radial_segment_structure_evidence_v1(
            matching_hypothesis.radial_segment_evidence_ref,
            resolver=resolver,
        )
        if (
            segment_evidence.target_sha256 != target_measurements.target_sha256
            or segment_evidence.semantic_subject_mask_ref
            != matching_hypothesis.subject_mask_ref
            or tuple(item.ownership_mask_ref for item in segment_evidence.segments)
            != matching_hypothesis.instance_mask_refs
        ):
            raise ValueError("Candidate radial segment evidence binding 不一致。")
    expected_constraint_evaluation = evaluate_intent_genome_constraints_v3(
        intent,
        genome,
        replay,
        candidate_id=candidate.candidate_id,
        target_measurements_ref=constraint_evaluation.target_measurements_ref,
        intent_ref=candidate.intent_ref,
        genome_ref=candidate.genome_ref,
        compilation_ref=candidate.compilation_ref,
        rendered_structure_evidence_ref=(
            candidate.rendered_structure_evidence_ref
        ),
        rendered_structure_evidence=structure_evidence,
        rendered_structure_verification_ref=(
            candidate.rendered_structure_verification_ref
        ),
        rendered_structure_verification=structure_verification,
    )
    if constraint_evaluation != expected_constraint_evaluation:
        raise ValueError("IntentConstraintEvaluation 与独立重算结果不一致。")
    if not constraint_evaluation.hard_constraints_passed:
        raise ValueError("Typed Candidate hard constraint closure 未通过。")

    evaluations = tuple(
        _parse_typed_json(
            _validate_ref_content(
                resolver,
                ref,
                kind=EVALUATION_ARTIFACT_KIND,
                schema_version=TYPED_EVALUATION_ARTIFACT_SCHEMA_VERSION,
                content_type=_JSON_CONTENT_TYPE,
            ),
            BasicEvaluationRecordV2,
        )
        for ref in candidate.evaluation_refs
    )
    if len(evaluations) != len(candidate.render_refs):
        raise ValueError("BasicEvaluation 必须与 render refs 一一对应。")
    for evaluation, render_ref in zip(evaluations, candidate.render_refs, strict=True):
        if (
            evaluation.run_id != candidate.run_id
            or evaluation.candidate_id != candidate.candidate_id
            or evaluation.intent_id != intent.intent_id
            or evaluation.target_hypothesis_hash != intent.target_hypothesis_hash
            or evaluation.genome_id != genome.genome_id
            or evaluation.semantic_genome_hash != replay.semantic_genome_hash
            or evaluation.compilation_sha256 != candidate.compilation_ref.sha256
            or evaluation.glsl_sha256 != candidate.glsl_ref.sha256
            or evaluation.render_ref != render_ref
            or evaluation.render_sha256 != render_ref.sha256
        ):
            raise ValueError("BasicEvaluation 与 Candidate/render 身份不一致。")
    return intent, genome, compilation, constraint_evaluation, evaluations


def _load_structure_receipt_models(
    resolver: ArtifactResolver,
    candidate: CandidateRecordV2,
) -> tuple[RenderedStructureEvidenceV4, RenderedStructureVerificationV4]:
    """先恢复 Evaluation V3 显式绑定的两个 structure receipt 正文。."""
    evidence = _parse_typed_json(
        _validate_ref_content(
            resolver,
            candidate.rendered_structure_evidence_ref,
            kind=RENDERED_STRUCTURE_EVIDENCE_ARTIFACT_KIND,
            schema_version=RENDERED_STRUCTURE_EVIDENCE_ARTIFACT_SCHEMA_VERSION,
            content_type=_JSON_CONTENT_TYPE,
        ),
        RenderedStructureEvidenceV4,
    )
    verification = _parse_typed_json(
        _validate_ref_content(
            resolver,
            candidate.rendered_structure_verification_ref,
            kind=RENDERED_STRUCTURE_VERIFICATION_ARTIFACT_KIND,
            schema_version=RENDERED_STRUCTURE_VERIFICATION_ARTIFACT_SCHEMA_VERSION,
            content_type=_JSON_CONTENT_TYPE,
        ),
        RenderedStructureVerificationV4,
    )
    return evidence, verification


def _load_rendered_candidate_payloads(
    resolver: ArtifactResolver,
    candidate: CandidateRecordV2,
    provenance: CandidateProvenanceV2,
    *,
    intent: IntentIR,
    genome: TypedEffectGenome,
    compilation: CompilationBundle,
) -> tuple[
    DiagnosticCompilationBundleV3,
    RenderPlanV2,
    RenderProgressV2,
    RenderRepeatabilityEvidenceV2,
    RenderedStructureEvidenceV4,
    RenderedStructureVerificationV4,
]:
    diagnostic = _parse_typed_json(
        _validate_ref_content(
            resolver,
            candidate.diagnostic_compilation_ref,
            kind=DIAGNOSTIC_COMPILATION_ARTIFACT_KIND,
            schema_version=DIAGNOSTIC_COMPILATION_ARTIFACT_SCHEMA_VERSION,
            content_type=_JSON_CONTENT_TYPE,
        ),
        DiagnosticCompilationBundleV3,
    )
    replay = compile_diagnostic_passes(genome)
    if (
        diagnostic.semantic_genome_hash != replay.semantic_genome_hash
        or len(diagnostic.passes) != len(replay.passes)
    ):
        raise ValueError("Diagnostic compilation identity 与 deterministic replay 不一致。")
    for artifact, source in zip(diagnostic.passes, replay.passes, strict=True):
        source_bytes = _validate_ref_content(
            resolver,
            artifact.source_ref,
            kind="diagnostic_glsl",
            schema_version="diagnostic_glsl_es_100_v3",
            content_type="text/x-glsl; charset=utf-8",
        )
        if (
            source_bytes != source.glsl_source.encode("utf-8")
            or artifact.pass_id != source.pass_id
            or artifact.pass_kind != source.pass_kind
            or artifact.canonical_node_id != source.canonical_node_id
            or artifact.instance_index != source.instance_index
            or artifact.layer != source.layer
            or artifact.source_sha256 != source.glsl_sha256
        ):
            raise ValueError("Diagnostic source/bundle 与 deterministic replay 不一致。")

    plan = load_render_model(candidate.render_plan_ref, resolver=resolver, run_id=candidate.run_id)
    progress = load_render_model(
        candidate.render_progress_ref, resolver=resolver, run_id=candidate.run_id
    )
    repeatability = load_render_model(
        candidate.render_repeatability_ref,
        resolver=resolver,
        run_id=candidate.run_id,
    )
    if not isinstance(plan, RenderPlanV2) or not isinstance(progress, RenderProgressV2):
        raise ValueError("Candidate render plan/progress Artifact 类型错误。")
    if not isinstance(repeatability, RenderRepeatabilityEvidenceV2):
        raise ValueError("Candidate repeatability Artifact 类型错误。")
    if (
        progress.plan_ref != candidate.render_plan_ref
        or progress.plan_hash != plan.plan_hash
        or progress.attempt_id != plan.attempt_id
        or progress.budget_policy_hash != plan.budget_policy_hash
        or progress.has_uncommitted_outcome
        or progress.completed_logical_requests != len(plan.items)
    ):
        raise ValueError("Candidate render progress 未完整、已结算地覆盖 plan。")
    successful = tuple(item for item in progress.outcomes if item.outcome == "success")
    if len(successful) != len(plan.items):
        raise ValueError("Candidate render progress success 分母不完整。")
    beauty = successful[:5]
    if tuple(item.render_ref for item in beauty) != candidate.render_refs:
        raise ValueError("Candidate 五次 beauty refs 与 render progress 不一致。")
    request_refs = tuple(item.renderer_request_ref for item in successful)
    if request_refs != provenance.renderer_request_refs:
        raise ValueError("Candidate provenance 未按 plan 覆盖全部 Renderer requests。")
    for plan_item, outcome in zip(plan.items, successful, strict=True):
        request = load_renderer_request(
            outcome.renderer_request_ref,
            resolver=resolver,
            run_id=candidate.run_id,
        )
        if not isinstance(request, RendererRequestReceiptV2):
            raise ValueError("V2.4 Candidate 禁止旧 Renderer request schema。")
        if (
            request.request_hash != outcome.renderer_request_hash
            or request.logical_request_ordinal
            != plan_item.logical_request_ordinal
            or request.render_profile != plan_item.profile
            or request.beauty_capture_index != plan_item.beauty_capture_index
            or request.diagnostic_pass_id != plan_item.diagnostic_pass_id
            or request.compilation_ref != plan_item.compilation_ref
            or request.glsl_ref != plan_item.source_ref
            or (request.width, request.height)
            != (plan_item.width, plan_item.height)
        ):
            raise ValueError("Renderer request/progress 未严格绑定 RenderPlan item。")

    persisted_repeatability = build_repeatability_evidence(
        run_id=candidate.run_id,
        attempt_id=plan.attempt_id,
        capture_request_refs=tuple(item.renderer_request_ref for item in beauty),
        capture_render_refs=candidate.render_refs,
        renderer_environment_ref=repeatability.renderer_environment_ref,
        resolver=resolver,
    )
    if persisted_repeatability != repeatability or not repeatability.passed:
        raise ValueError("Candidate 五次 beauty repeatability 未通过实际 PNG 重算。")

    structure_evidence = _parse_typed_json(
        _validate_ref_content(
            resolver,
            candidate.rendered_structure_evidence_ref,
            kind=RENDERED_STRUCTURE_EVIDENCE_ARTIFACT_KIND,
            schema_version=RENDERED_STRUCTURE_EVIDENCE_ARTIFACT_SCHEMA_VERSION,
            content_type=_JSON_CONTENT_TYPE,
        ),
        RenderedStructureEvidenceV4,
    )
    persisted_verification = _parse_typed_json(
        _validate_ref_content(
            resolver,
            candidate.rendered_structure_verification_ref,
            kind=RENDERED_STRUCTURE_VERIFICATION_ARTIFACT_KIND,
            schema_version=RENDERED_STRUCTURE_VERIFICATION_ARTIFACT_SCHEMA_VERSION,
            content_type=_JSON_CONTENT_TYPE,
        ),
        RenderedStructureVerificationV4,
    )
    if (
        structure_evidence.beauty_render_ref != candidate.render_refs[0]
        or structure_evidence.beauty_renderer_request_ref
        != beauty[0].renderer_request_ref
        or structure_evidence.diagnostic_compilation_ref
        != candidate.diagnostic_compilation_ref
    ):
        raise ValueError("Rendered structure evidence 未绑定主 beauty/Candidate。")
    diagnostic_success = successful[5:]
    evidence_receipts = structure_evidence.diagnostic_receipts
    if tuple(item.renderer_request_ref for item in evidence_receipts) != tuple(
        item.renderer_request_ref for item in diagnostic_success
    ) or tuple(item.render_ref for item in evidence_receipts) != tuple(
        item.render_ref for item in diagnostic_success
    ):
        raise ValueError("Rendered structure diagnostics 与 progress 不一致。")
    replayed_verification = verify_rendered_structure_evidence(
        structure_evidence,
        resolver=resolver,
        intent=intent,
        genome=genome,
        compilation_bundle=compilation,
        diagnostic_bundle=diagnostic,
    )
    if (
        replayed_verification != persisted_verification
        or persisted_verification.status != "structure_verified"
    ):
        raise ValueError("Rendered structure verification 未通过实际 PNG 重放。")
    return (
        diagnostic,
        plan,
        progress,
        repeatability,
        structure_evidence,
        persisted_verification,
    )


def materialize_typed_candidate_artifacts(
    *,
    catalog: ArtifactCatalog,
    run_id: str,
    candidate_input: CandidateMaterializationInputV2,
) -> TypedCandidateArtifactBundleV2:
    """只为完整 typed V2.2 闭包物化可准入 Candidate。."""
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id 不能为空。")
    _require_bound_run(catalog, run_id)
    if candidate_input.run_id != run_id:
        raise ValueError("Candidate input 不属于请求物化的 run_id。")
    placeholder_ref = ArtifactRefV2(
        artifact_id="candidate_provenance_placeholder",
        sha256="0" * 64,
        kind=CANDIDATE_PROVENANCE_ARTIFACT_KIND,
        schema_version=CANDIDATE_PROVENANCE_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
        size_bytes=0,
    )
    placeholder = _candidate_from_input(candidate_input, placeholder_ref)
    structure_evidence, structure_verification = _load_structure_receipt_models(
        catalog, placeholder
    )
    intent, genome, _, _, _ = _load_typed_candidate_payloads(
        catalog,
        placeholder,
        structure_evidence=structure_evidence,
        structure_verification=structure_verification,
    )
    provenance = _provenance_from_input(
        candidate_input,
        intent=intent,
        genome=genome,
        typed_semantics=True,
    )
    _validate_cross_identity(placeholder, provenance, intent, genome)
    provenance_ref = catalog.put(
        run_id=run_id,
        kind=CANDIDATE_PROVENANCE_ARTIFACT_KIND,
        schema_version=CANDIDATE_PROVENANCE_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
        data=provenance.model_dump_json().encode("utf-8"),
    )
    candidate = _candidate_from_input(candidate_input, provenance_ref)
    candidate_ref = catalog.put(
        run_id=run_id,
        kind=CANDIDATE_RECORD_ARTIFACT_KIND,
        schema_version="candidate_record_v3",
        content_type=_JSON_CONTENT_TYPE,
        data=candidate.model_dump_json().encode("utf-8"),
    )
    return load_typed_candidate_artifacts(
        candidate_ref,
        resolver=catalog,
        run_id=run_id,
    )


def load_typed_candidate_artifacts(
    candidate_ref: ArtifactRefV2,
    *,
    resolver: ArtifactResolver,
    run_id: str,
) -> TypedCandidateArtifactBundleV2:
    """恢复 typed Candidate 并重放全部语义；任何不闭合均拒绝。."""
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id 不能为空。")
    _require_bound_run(resolver, run_id)
    _require_ref_metadata(
        candidate_ref,
        kind=CANDIDATE_RECORD_ARTIFACT_KIND,
        schema_version="candidate_record_v3",
        content_type=_JSON_CONTENT_TYPE,
    )
    candidate = _parse_typed_json(
        _read_exact(resolver, candidate_ref),
        CandidateRecordV2,
    )
    if candidate.run_id != run_id:
        raise ValueError("Candidate 不属于请求恢复的 run_id。")
    _require_ref_metadata(
        candidate.provenance_ref,
        kind=CANDIDATE_PROVENANCE_ARTIFACT_KIND,
        schema_version=CANDIDATE_PROVENANCE_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
    )
    provenance = _parse_typed_json(
        _read_exact(resolver, candidate.provenance_ref),
        CandidateProvenanceV2,
    )
    if provenance.run_id != run_id:
        raise ValueError("Candidate provenance 不属于请求恢复的 run_id。")
    structure_evidence, structure_verification = _load_structure_receipt_models(
        resolver, candidate
    )
    intent, genome, compilation, constraint_evaluation, evaluations = (
        _load_typed_candidate_payloads(
            resolver,
            candidate,
            structure_evidence=structure_evidence,
            structure_verification=structure_verification,
        )
    )
    _validate_cross_identity(candidate, provenance, intent, genome)
    _validate_attempt_provenance(resolver, provenance)
    (
        diagnostic,
        render_plan,
        render_progress,
        repeatability,
        replayed_structure_evidence,
        replayed_structure_verification,
    ) = _load_rendered_candidate_payloads(
        resolver,
        candidate,
        provenance,
        intent=intent,
        genome=genome,
        compilation=compilation,
    )
    if (
        replayed_structure_evidence != structure_evidence
        or replayed_structure_verification != structure_verification
    ):
        raise ValueError("Candidate structure receipt 前置恢复与 verifier 重放不一致。")
    content_verified_refs = _ordered_typed_closure_refs(
        candidate_ref,
        candidate,
        provenance,
        compilation,
        diagnostic,
        render_progress,
        repeatability,
        structure_evidence,
        constraint_evaluation,
    )
    projection = _derive_typed_candidate_projection(
        candidate=candidate,
        plan=render_plan,
        progress=render_progress,
        evidence=structure_evidence,
        verification=structure_verification,
        content_verified_refs=content_verified_refs,
    )
    return TypedCandidateArtifactBundleV2(
        candidate_ref=candidate_ref,
        candidate=candidate,
        provenance=provenance,
        intent=intent,
        genome=genome,
        compilation_bundle=compilation,
        diagnostic_compilation_bundle=diagnostic,
        render_plan=render_plan,
        render_progress=render_progress,
        repeatability=repeatability,
        rendered_structure_evidence=structure_evidence,
        rendered_structure_verification=structure_verification,
        constraint_evaluation=constraint_evaluation,
        basic_evaluations=evaluations,
        content_verified_refs=content_verified_refs,
        rendered_closure_projection=projection,
    )


def load_candidate_artifact_bundle(
    candidate_ref: ArtifactRefV2,
    *,
    resolver: ArtifactResolver,
    run_id: str,
) -> CandidateArtifactBundleV2 | TypedCandidateArtifactBundleV2:
    """按冻结 compilation schema 分派 opaque 或 typed loader。."""
    candidate = _parse_typed_json(
        _read_exact(resolver, candidate_ref), CandidateRecordV2
    )
    if candidate.compilation_ref.schema_version == (
        TYPED_COMPILATION_ARTIFACT_SCHEMA_VERSION
    ):
        return load_typed_candidate_artifacts(
            candidate_ref,
            resolver=resolver,
            run_id=run_id,
        )
    return load_candidate_artifacts(
        candidate_ref,
        resolver=resolver,
        run_id=run_id,
    )


__all__ = [
    "CANDIDATE_PROVENANCE_ARTIFACT_KIND",
    "CANDIDATE_PROVENANCE_SCHEMA_VERSION",
    "CANDIDATE_RECORD_ARTIFACT_KIND",
    "COMPILATION_ARTIFACT_KIND",
    "COMPILATION_ARTIFACT_SCHEMA_VERSION",
    "CONSTRAINT_EVALUATION_ARTIFACT_KIND",
    "CONSTRAINT_EVALUATION_ARTIFACT_SCHEMA_VERSION",
    "CandidateArtifactBundleV2",
    "CandidateMaterializationInputV2",
    "CandidateRenderedClosureProjectionV2",
    "CandidateSemanticValidationStatus",
    "TypedCandidateArtifactBundleV2",
    "EVALUATION_ARTIFACT_KIND",
    "EVALUATION_ARTIFACT_SCHEMA_VERSION",
    "GENOME_ARTIFACT_KIND",
    "GENOME_ARTIFACT_SCHEMA_VERSION",
    "GLSL_ARTIFACT_KIND",
    "GLSL_ARTIFACT_SCHEMA_VERSION",
    "INTENT_ARTIFACT_KIND",
    "INTENT_ARTIFACT_SCHEMA_VERSION",
    "RENDER_ARTIFACT_KIND",
    "RENDER_ARTIFACT_SCHEMA_VERSION",
    "load_candidate_artifacts",
    "load_candidate_artifact_bundle",
    "load_typed_candidate_artifacts",
    "materialize_candidate_artifacts",
    "materialize_typed_candidate_artifacts",
    "TYPED_COMPILATION_ARTIFACT_SCHEMA_VERSION",
    "TYPED_CONSTRAINT_EVALUATION_ARTIFACT_SCHEMA_VERSION",
    "TYPED_EVALUATION_ARTIFACT_SCHEMA_VERSION",
    "TYPED_GLSL_ARTIFACT_KIND",
    "TYPED_GLSL_ARTIFACT_SCHEMA_VERSION",
]
