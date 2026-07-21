"""确定性候选的版本化结构能力与 admission 纯契约."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from shaderforge.contracts.base import FrozenModel, NonEmptyString, Sha256Hex
from shaderforge.contracts.canonical import canonical_sha256
from shaderforge.contracts.taxonomy import REQUIRED_LAYER_ORDER, RequiredLayerTaxon

TARGET_STRUCTURE_FACTS_SCHEMA_VERSION: Literal["target_structure_facts_v1"] = (
    "target_structure_facts_v1"
)
DETERMINISTIC_GENERATOR_CAPABILITY_POLICY_VERSION: Literal[
    "deterministic_generator_capability_v2"
] = "deterministic_generator_capability_v2"
MEASUREMENT_SEED_ADMISSION_EVIDENCE_SCHEMA_VERSION: Literal[
    "measurement_seed_admission_evidence_v1"
] = "measurement_seed_admission_evidence_v1"
MEASUREMENT_SEED_ADMISSION_POLICY_VERSION: Literal[
    "measurement_seed_admission_v1"
] = "measurement_seed_admission_v1"

MEASUREMENT_AFFINE_SEED_V1 = "measurement_affine_seed_v1"
EFFECT_GENOME_EXPANDER_V2 = "effect_genome_expander_v2"
GENERATOR_CAPABILITY_DECLARATION_SCHEMA_VERSION: Literal[
    "deterministic_generator_capability_declaration_v1"
] = "deterministic_generator_capability_declaration_v1"

CapabilityStatus = Literal["supported", "unsupported", "unknown"]
CandidateOrigin = Literal["model", "deterministic"]
EvidenceScope = Literal["offline_replay", "runtime_verified"]
AdmissionStatus = Literal["admitted", "unsupported", "unknown", "not_applicable"]

_MEASUREMENT_AFFINE_SUPPORTED_TOPOLOGIES: tuple[
    Literal["solid", "hollow", "ring", "open"], ...
] = ("solid",)
_MEASUREMENT_AFFINE_SUPPORTED_LAYERS: tuple[RequiredLayerTaxon, ...] = (
    "base_fill",
)
_EFFECT_GENOME_EXPANDER_SUPPORTED_TOPOLOGIES: tuple[
    Literal["solid", "hollow", "ring", "open"], ...
] = ("solid",)
# `background` 故意不在此表：当前 matcher/expander 会为它生成
# gaussian_color_lobe，而 typed evaluator 的 background receipt 只认可
# solid_fill/linear_gradient。其余九类同时具备 expander node 与 typed
# required-layer coverage/constraint receipt。
_EFFECT_GENOME_EXPANDER_SUPPORTED_LAYERS: tuple[RequiredLayerTaxon, ...] = (
    "shadow",
    "base_fill",
    "color_lobe",
    "haze",
    "rim",
    "outline",
    "highlight",
    "detail",
    "glow",
)


class DeterministicGeneratorCapabilityDeclarationV1(FrozenModel):
    """生成器能力的内容寻址冻结声明，不等同于产品开关。."""

    schema_version: Literal[
        "deterministic_generator_capability_declaration_v1"
    ] = GENERATOR_CAPABILITY_DECLARATION_SCHEMA_VERSION
    capability_version: NonEmptyString
    generator_version: NonEmptyString
    supported_topologies: tuple[Literal["solid", "hollow", "ring", "open"], ...]
    max_instance_count: int = Field(ge=1)
    max_hole_count: int = Field(ge=0)
    supported_required_layers: tuple[RequiredLayerTaxon, ...]
    evidence_basis: tuple[NonEmptyString, ...] = Field(min_length=1)
    declaration_sha256: Sha256Hex

    @model_validator(mode="after")
    def _validate_declaration(
        self,
    ) -> DeterministicGeneratorCapabilityDeclarationV1:
        if not self.supported_topologies or len(self.supported_topologies) != len(
            set(self.supported_topologies)
        ):
            raise ValueError("supported_topologies 必须非空且不得重复。")
        expected_layers = tuple(
            layer
            for layer in REQUIRED_LAYER_ORDER
            if layer in self.supported_required_layers
        )
        if self.supported_required_layers != expected_layers:
            raise ValueError("supported_required_layers 必须唯一并按 taxonomy 排序。")
        if "base_fill" not in self.supported_required_layers:
            raise ValueError("生成器 capability 必须至少声明 base_fill。")
        if len(self.evidence_basis) != len(set(self.evidence_basis)):
            raise ValueError("evidence_basis 不得重复。")
        payload = self.model_dump(mode="python", exclude={"declaration_sha256"})
        if self.declaration_sha256 != canonical_sha256(payload):
            raise ValueError("generator capability declaration_sha256 不一致。")
        return self


EFFECT_GENOME_EXPANDER_V2_CAPABILITY_SHA256 = (
    "8177827bbedd0d346634683a9894fd896780444f5544c7d21760500c6f4cc696"
)
EFFECT_GENOME_EXPANDER_V2_CAPABILITY = (
    DeterministicGeneratorCapabilityDeclarationV1(
        capability_version="effect_genome_expander_v2_capability_v1",
        generator_version=EFFECT_GENOME_EXPANDER_V2,
        supported_topologies=_EFFECT_GENOME_EXPANDER_SUPPORTED_TOPOLOGIES,
        max_instance_count=1,
        max_hole_count=0,
        supported_required_layers=_EFFECT_GENOME_EXPANDER_SUPPORTED_LAYERS,
        evidence_basis=(
            "seed_plan_expander_v2_typed_nodes",
            "intent_constraint_evaluation_v2_required_layer_coverage",
            "target_structure_solid_single_instance_verified",
        ),
        declaration_sha256=EFFECT_GENOME_EXPANDER_V2_CAPABILITY_SHA256,
    )
)

_DETERMINISTIC_CAPABILITIES = {
    EFFECT_GENOME_EXPANDER_V2: EFFECT_GENOME_EXPANDER_V2_CAPABILITY,
}


def _status(values: tuple[CapabilityStatus, ...]) -> CapabilityStatus:
    if "unsupported" in values:
        return "unsupported"
    if values and all(value == "supported" for value in values):
        return "supported"
    return "unknown"


def _capability_reason_codes(
    topology_status: CapabilityStatus,
    instance_status: CapabilityStatus,
    hole_status: CapabilityStatus,
    layer_status: CapabilityStatus,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if topology_status == "unsupported":
        reasons.append("unsupported_topology")
    if instance_status == "unsupported":
        reasons.append("instance_count_exceeds_generator_capability")
    if hole_status == "unsupported":
        reasons.append("hole_count_exceeds_generator_capability")
    if layer_status == "unsupported":
        reasons.append("required_layers_exceed_generator_capability")
    return tuple(reasons or ("labels_within_generator_capability",))


class TargetStructureFacts(FrozenModel):
    """与样本名称、门禁或生成实现解耦的目标结构事实."""

    schema_version: Literal["target_structure_facts_v1"] = (
        TARGET_STRUCTURE_FACTS_SCHEMA_VERSION
    )
    topology: Literal["solid", "hollow", "ring", "open"]
    instance_count: int = Field(ge=1)
    hole_count: int = Field(ge=0)
    required_layers: tuple[RequiredLayerTaxon, ...]

    @model_validator(mode="after")
    def _validate_layers(self) -> TargetStructureFacts:
        if len(self.required_layers) != len(set(self.required_layers)):
            raise ValueError("required_layers 不得重复。")
        if "base_fill" not in self.required_layers:
            raise ValueError("required_layers 必须包含 base_fill。")
        expected_order = tuple(
            layer for layer in REQUIRED_LAYER_ORDER if layer in self.required_layers
        )
        if self.required_layers != expected_order:
            raise ValueError("required_layers 必须按 required_layer_taxonomy_v1 排序。")
        if self.topology == "solid" and self.hole_count != 0:
            raise ValueError("solid topology 的 hole_count 必须为 0。")
        if self.topology in {"ring", "hollow"} and self.hole_count < 1:
            raise ValueError("ring/hollow topology 的 hole_count 必须至少为 1。")
        return self


class StructureCapabilityAssessment(FrozenModel):
    """目标结构事实相对一个生成器声明能力的判断."""

    status: CapabilityStatus
    topology_status: CapabilityStatus
    instance_count_status: CapabilityStatus
    hole_count_status: CapabilityStatus
    required_layers_status: CapabilityStatus
    expected_topology: Literal["solid", "hollow", "ring", "open"]
    expected_instance_count: int = Field(ge=1)
    expected_hole_count: int = Field(ge=0)
    expected_required_layers: tuple[RequiredLayerTaxon, ...]
    supported_topologies: tuple[str, ...] | None
    max_instance_count: int | None = Field(default=None, ge=1)
    max_hole_count: int | None = Field(default=None, ge=0)
    supported_required_layers: tuple[RequiredLayerTaxon, ...] | None
    unsupported_required_layers: tuple[RequiredLayerTaxon, ...]
    reason_codes: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_cross_fields(self) -> StructureCapabilityAssessment:
        dimension_statuses = (
            self.topology_status,
            self.instance_count_status,
            self.hole_count_status,
            self.required_layers_status,
        )
        capability_fields = (
            self.supported_topologies,
            self.max_instance_count,
            self.max_hole_count,
            self.supported_required_layers,
        )
        if all(value is None for value in capability_fields):
            if self.status != "unknown" or any(
                value != "unknown" for value in dimension_statuses
            ):
                raise ValueError("未知 capability 必须让全部状态保持 unknown。")
            if self.unsupported_required_layers:
                raise ValueError("未知 capability 不得伪造 unsupported layers。")
            if self.reason_codes not in {
                ("model_capability_not_declared",),
                ("unknown_deterministic_generator",),
            }:
                raise ValueError("未知 capability reason_codes 不一致。")
            return self
        if any(value is None for value in capability_fields):
            raise ValueError("已知 capability 字段必须完整出现。")
        assert self.supported_topologies is not None
        assert self.max_instance_count is not None
        assert self.max_hole_count is not None
        assert self.supported_required_layers is not None
        if not self.supported_topologies:
            raise ValueError("supported_topologies 不能为空。")
        if len(self.expected_required_layers) != len(set(self.expected_required_layers)):
            raise ValueError("expected_required_layers 不得重复。")
        if len(self.supported_required_layers) != len(
            set(self.supported_required_layers)
        ):
            raise ValueError("supported_required_layers 不得重复。")
        topology_status: CapabilityStatus = (
            "supported"
            if self.expected_topology in self.supported_topologies
            else "unsupported"
        )
        instance_status: CapabilityStatus = (
            "supported"
            if self.expected_instance_count <= self.max_instance_count
            else "unsupported"
        )
        hole_status: CapabilityStatus = (
            "supported"
            if self.expected_hole_count <= self.max_hole_count
            else "unsupported"
        )
        unsupported_layers = tuple(
            sorted(
                layer
                for layer in self.expected_required_layers
                if layer not in set(self.supported_required_layers)
            )
        )
        layer_status: CapabilityStatus = (
            "supported" if not unsupported_layers else "unsupported"
        )
        expected_statuses = (
            topology_status,
            instance_status,
            hole_status,
            layer_status,
        )
        if dimension_statuses != expected_statuses:
            raise ValueError("capability dimension status 与能力边界不一致。")
        if self.status != _status(expected_statuses):
            raise ValueError("capability status 与分项状态不一致。")
        if self.unsupported_required_layers != unsupported_layers:
            raise ValueError("unsupported_required_layers 与能力边界不一致。")
        if self.reason_codes != _capability_reason_codes(*expected_statuses):
            raise ValueError("capability reason_codes 与分项状态不一致。")
        return self


def assess_target_structure_capability(
    target: TargetStructureFacts,
    *,
    origin: CandidateOrigin,
    generator_version: str | None,
) -> StructureCapabilityAssessment:
    """按 capability-v2 判断生成器是否覆盖目标结构事实."""
    if origin != "deterministic" or generator_version not in {
        MEASUREMENT_AFFINE_SEED_V1,
        EFFECT_GENOME_EXPANDER_V2,
    }:
        return StructureCapabilityAssessment(
            status="unknown",
            topology_status="unknown",
            instance_count_status="unknown",
            hole_count_status="unknown",
            required_layers_status="unknown",
            expected_topology=target.topology,
            expected_instance_count=target.instance_count,
            expected_hole_count=target.hole_count,
            expected_required_layers=target.required_layers,
            supported_topologies=None,
            max_instance_count=None,
            max_hole_count=None,
            supported_required_layers=None,
            unsupported_required_layers=(),
            reason_codes=(
                "model_capability_not_declared"
                if origin == "model"
                else "unknown_deterministic_generator"
            ,),
        )

    if generator_version == MEASUREMENT_AFFINE_SEED_V1:
        supported_topologies: tuple[
            Literal["solid", "hollow", "ring", "open"], ...
        ] = _MEASUREMENT_AFFINE_SUPPORTED_TOPOLOGIES
        max_instance_count = 1
        max_hole_count = 0
        supported_layers = _MEASUREMENT_AFFINE_SUPPORTED_LAYERS
    else:
        assert generator_version == EFFECT_GENOME_EXPANDER_V2
        declaration = _DETERMINISTIC_CAPABILITIES[generator_version]
        supported_topologies = declaration.supported_topologies
        max_instance_count = declaration.max_instance_count
        max_hole_count = declaration.max_hole_count
        supported_layers = declaration.supported_required_layers
    supported_layer_set = frozenset(supported_layers)

    topology_status: CapabilityStatus = (
        "supported"
        if target.topology in supported_topologies
        else "unsupported"
    )
    instance_status: CapabilityStatus = (
        "supported"
        if target.instance_count <= max_instance_count
        else "unsupported"
    )
    hole_status: CapabilityStatus = (
        "supported" if target.hole_count <= max_hole_count else "unsupported"
    )
    unsupported_layers = tuple(
        sorted(
            layer
            for layer in target.required_layers
            if layer not in supported_layer_set
        )
    )
    layer_status: CapabilityStatus = (
        "supported" if not unsupported_layers else "unsupported"
    )
    statuses = (topology_status, instance_status, hole_status, layer_status)
    return StructureCapabilityAssessment(
        status=_status(statuses),
        topology_status=topology_status,
        instance_count_status=instance_status,
        hole_count_status=hole_status,
        required_layers_status=layer_status,
        expected_topology=target.topology,
        expected_instance_count=target.instance_count,
        expected_hole_count=target.hole_count,
        expected_required_layers=target.required_layers,
        supported_topologies=supported_topologies,
        max_instance_count=max_instance_count,
        max_hole_count=max_hole_count,
        supported_required_layers=supported_layers,
        unsupported_required_layers=unsupported_layers,
        reason_codes=_capability_reason_codes(*statuses),
    )


class GeneratorAdmissionEvidence(FrozenModel):
    """把目标事实、来源和 capability-v2 结果绑定为 Selector 输入."""

    schema_version: Literal["measurement_seed_admission_evidence_v1"] = (
        MEASUREMENT_SEED_ADMISSION_EVIDENCE_SCHEMA_VERSION
    )
    admission_policy_version: Literal["measurement_seed_admission_v1"] = (
        MEASUREMENT_SEED_ADMISSION_POLICY_VERSION
    )
    capability_policy_version: Literal["deterministic_generator_capability_v2"] = (
        DETERMINISTIC_GENERATOR_CAPABILITY_POLICY_VERSION
    )
    evidence_scope: EvidenceScope
    evidence_ref: NonEmptyString
    evidence_sha256: Sha256Hex
    target_source_sha256: Sha256Hex
    normalized_reference_sha256: Sha256Hex
    candidate_id: NonEmptyString
    candidate_glsl_sha256: Sha256Hex
    candidate_render_sha256: Sha256Hex
    origin: CandidateOrigin
    generator_version: NonEmptyString | None
    target: TargetStructureFacts
    assessment: StructureCapabilityAssessment

    @model_validator(mode="after")
    def _validate_assessment(self) -> GeneratorAdmissionEvidence:
        expected = assess_target_structure_capability(
            self.target,
            origin=self.origin,
            generator_version=self.generator_version,
        )
        if self.assessment != expected:
            raise ValueError("admission assessment 与目标事实或 generator 身份不一致。")
        return self


def build_generator_admission_evidence(
    target: TargetStructureFacts,
    *,
    origin: CandidateOrigin,
    generator_version: str | None,
    evidence_scope: EvidenceScope,
    evidence_ref: str,
    evidence_sha256: str,
    target_source_sha256: str,
    normalized_reference_sha256: str,
    candidate_id: str,
    candidate_glsl_sha256: str,
    candidate_render_sha256: str,
) -> GeneratorAdmissionEvidence:
    """构造经交叉校验的版本化 admission evidence."""
    return GeneratorAdmissionEvidence(
        evidence_scope=evidence_scope,
        evidence_ref=evidence_ref,
        evidence_sha256=evidence_sha256,
        target_source_sha256=target_source_sha256,
        normalized_reference_sha256=normalized_reference_sha256,
        candidate_id=candidate_id,
        candidate_glsl_sha256=candidate_glsl_sha256,
        candidate_render_sha256=candidate_render_sha256,
        origin=origin,
        generator_version=generator_version,
        target=target,
        assessment=assess_target_structure_capability(
            target,
            origin=origin,
            generator_version=generator_version,
        ),
    )


@dataclass(frozen=True)
class MeasurementSeedAdmissionPolicy:
    """显式启用的 Selector admission 策略；runtime scope 当前仍 fail closed."""

    policy_version: Literal["measurement_seed_admission_v1"] = (
        MEASUREMENT_SEED_ADMISSION_POLICY_VERSION
    )
    capability_policy_version: Literal["deterministic_generator_capability_v2"] = (
        DETERMINISTIC_GENERATOR_CAPABILITY_POLICY_VERSION
    )
    allowed_evidence_scopes: tuple[EvidenceScope, ...] = ("runtime_verified",)

    def __post_init__(self) -> None:
        """拒绝空或重复的 evidence scope 配置."""
        if not self.allowed_evidence_scopes:
            raise ValueError("allowed_evidence_scopes 不能为空。")
        if len(self.allowed_evidence_scopes) != len(set(self.allowed_evidence_scopes)):
            raise ValueError("allowed_evidence_scopes 不得重复。")


@dataclass(frozen=True)
class GeneratorAdmissionDecision:
    """Selector admission 前置判断及可审计原因."""

    status: AdmissionStatus
    reason_codes: tuple[str, ...]
    policy_version: str

    @property
    def admitted(self) -> bool:
        """返回候选是否可继续进入既有 score/protection 规则."""
        return self.status in {"admitted", "not_applicable"}


def decide_generator_admission(
    *,
    candidate_id: str,
    candidate_glsl_sha256: str,
    candidate_render_sha256: str | None,
    candidate_origin: CandidateOrigin,
    candidate_generator_version: str | None,
    evidence: GeneratorAdmissionEvidence | None,
    policy: MeasurementSeedAdmissionPolicy,
) -> GeneratorAdmissionDecision:
    """对 deterministic 候选 fail closed，model 候选保持不适用."""
    if candidate_origin == "model":
        return GeneratorAdmissionDecision(
            status="not_applicable",
            reason_codes=("model_candidate_not_subject_to_generator_admission",),
            policy_version=policy.policy_version,
        )
    if evidence is None:
        return GeneratorAdmissionDecision(
            status="unknown",
            reason_codes=("generator_admission_evidence_missing",),
            policy_version=policy.policy_version,
        )
    if (
        evidence.candidate_id != candidate_id
        or evidence.candidate_glsl_sha256 != candidate_glsl_sha256
        or evidence.candidate_render_sha256 != candidate_render_sha256
        or evidence.origin != candidate_origin
        or evidence.generator_version != candidate_generator_version
    ):
        return GeneratorAdmissionDecision(
            status="unknown",
            reason_codes=("generator_admission_identity_mismatch",),
            policy_version=policy.policy_version,
        )
    if evidence.evidence_scope == "runtime_verified":
        return GeneratorAdmissionDecision(
            status="unknown",
            reason_codes=("runtime_evidence_verifier_unavailable",),
            policy_version=policy.policy_version,
        )
    if evidence.evidence_scope not in policy.allowed_evidence_scopes:
        return GeneratorAdmissionDecision(
            status="unknown",
            reason_codes=("generator_admission_evidence_scope_not_allowed",),
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
    "DETERMINISTIC_GENERATOR_CAPABILITY_POLICY_VERSION",
    "EFFECT_GENOME_EXPANDER_V2",
    "EFFECT_GENOME_EXPANDER_V2_CAPABILITY",
    "EFFECT_GENOME_EXPANDER_V2_CAPABILITY_SHA256",
    "GENERATOR_CAPABILITY_DECLARATION_SCHEMA_VERSION",
    "MEASUREMENT_AFFINE_SEED_V1",
    "MEASUREMENT_SEED_ADMISSION_EVIDENCE_SCHEMA_VERSION",
    "MEASUREMENT_SEED_ADMISSION_POLICY_VERSION",
    "TARGET_STRUCTURE_FACTS_SCHEMA_VERSION",
    "AdmissionStatus",
    "CapabilityStatus",
    "CandidateOrigin",
    "DeterministicGeneratorCapabilityDeclarationV1",
    "EvidenceScope",
    "GeneratorAdmissionDecision",
    "GeneratorAdmissionEvidence",
    "MeasurementSeedAdmissionPolicy",
    "StructureCapabilityAssessment",
    "TargetStructureFacts",
    "assess_target_structure_capability",
    "build_generator_admission_evidence",
    "decide_generator_admission",
]
