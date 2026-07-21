"""SeedPlan、模板匹配与三候选多样性冻结契约。."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from shaderforge.contracts import FiniteFloat, FrozenModel, NonEmptyString, Sha256Hex
from shaderforge.contracts.taxonomy import RequiredLayerTaxon
from shaderforge.genome import GenomeHashes, TypedEffectGenome, compute_genome_hashes
from shaderforge.store import ArtifactRefV2

SeedRole = Literal["minimum_complexity", "semantic_enhancement", "alternate_structure"]
SeedSource = Literal["rule", "model", "memory"]
GeometryKind = Literal["circle_sdf", "ellipse_sdf", "rounded_rect_sdf"]
BaseFillKind = Literal["solid_fill", "linear_gradient"]
OverrideParameterName = Literal[
    "center",
    "radius",
    "radii",
    "rotation",
    "half_size",
    "corner_radius",
    "color",
    "start",
    "end",
    "start_color",
    "end_color",
    "offset",
    "blur",
    "spread",
    "sigma",
    "intensity",
    "width",
    "softness",
    "direction",
    "angular_width",
    "thickness",
    "opacity",
]
OverrideValue = bool | int | FiniteFloat | tuple[FiniteFloat, ...]


def _artifact_key(ref: ArtifactRefV2) -> tuple[str, str, str, str, int, str]:
    return (
        ref.sha256,
        ref.kind,
        ref.schema_version,
        ref.content_type,
        ref.size_bytes,
        ref.artifact_id,
    )


class LayerBindingV1(FrozenModel):
    """把一个 Intent layer 绑定到模板内的有限 primitive。."""

    layer_id: NonEmptyString
    layer_order: int = Field(ge=0)
    role: RequiredLayerTaxon
    object_ref: NonEmptyString | None
    primitive_id: NonEmptyString
    enabled: bool


class AllowedOverrideV1(FrozenModel):
    """模板允许的具名参数覆盖；不接受任意参数 path。."""

    layer_id: NonEmptyString
    parameter_name: OverrideParameterName
    value: OverrideValue


class SeedPlanV1(FrozenModel):
    """由有限模板选择组成、可严格重放的 SeedPlan。."""

    schema_version: Literal["seed_plan_v1"] = "seed_plan_v1"
    seed_role: SeedRole
    intent_id: NonEmptyString
    target_hypothesis_id: NonEmptyString
    target_hypothesis_hash: Sha256Hex
    template_id: NonEmptyString
    template_version: Literal["1"] = "1"
    layer_bindings: tuple[LayerBindingV1, ...] = Field(min_length=1)
    parameter_overrides: tuple[AllowedOverrideV1, ...] = ()
    source: SeedSource
    random_seed: int = Field(ge=0, le=9_223_372_036_854_775_807)
    evidence_refs: tuple[ArtifactRefV2, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_closed_plan(self) -> SeedPlanV1:
        layer_ids = [item.layer_id for item in self.layer_bindings]
        if len(layer_ids) != len(set(layer_ids)):
            raise ValueError("SeedPlan layer_id 不得重复。")
        orders = [item.layer_order for item in self.layer_bindings]
        if orders != sorted(orders) or len(orders) != len(set(orders)):
            raise ValueError("SeedPlan layer bindings 必须按唯一 layer_order 排序。")
        base_layers = [item for item in self.layer_bindings if item.role == "base_fill"]
        if len(base_layers) != 1 or not base_layers[0].enabled:
            raise ValueError("SeedPlan 必须恰好启用一个 base_fill layer。")
        known_enabled = {item.layer_id for item in self.layer_bindings if item.enabled}
        override_keys = [
            (item.layer_id, item.parameter_name) for item in self.parameter_overrides
        ]
        if len(override_keys) != len(set(override_keys)):
            raise ValueError("SeedPlan parameter override 不得重复。")
        if any(item.layer_id not in known_enabled for item in self.parameter_overrides):
            raise ValueError("SeedPlan override 只能指向已启用 layer。")
        evidence_keys = [_artifact_key(ref) for ref in self.evidence_refs]
        if evidence_keys != sorted(set(evidence_keys)):
            raise ValueError("SeedPlan evidence refs 必须唯一且规范排序。")
        return self


class TemplateMatchV1(FrozenModel):
    """Template Matcher 的有限、可审计匹配结果。."""

    schema_version: Literal["template_match_v1"] = "template_match_v1"
    seed_role: SeedRole
    template_id: NonEmptyString
    template_version: Literal["1"] = "1"
    geometry_kind: GeometryKind
    base_fill_kind: BaseFillKind
    enabled_layer_ids: tuple[NonEmptyString, ...] = Field(min_length=1)
    reason_codes: tuple[NonEmptyString, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_enabled_layers(self) -> TemplateMatchV1:
        if len(self.enabled_layer_ids) != len(set(self.enabled_layer_ids)):
            raise ValueError("TemplateMatch enabled layer 不得重复。")
        return self


DiversityException = Literal[
    "semantic_genome_hash_not_unique",
    "no_template_topology_or_enabled_layer_difference",
    "semantic_and_structural_diversity_missing",
]


class SeedDiversityAssessmentV1(FrozenModel):
    """三候选 semantic 与结构多样性的发布门禁证据。."""

    schema_version: Literal["seed_diversity_assessment_v1"] = (
        "seed_diversity_assessment_v1"
    )
    gate_passed: bool
    semantic_genome_hashes: tuple[Sha256Hex, Sha256Hex, Sha256Hex]
    distinct_structural_signatures: int = Field(ge=1, le=3)
    diversity_exception: DiversityException | None

    @model_validator(mode="after")
    def _validate_gate_status(self) -> SeedDiversityAssessmentV1:
        semantic_distinct = len(set(self.semantic_genome_hashes)) == 3
        structural_distinct = self.distinct_structural_signatures >= 2
        expected = semantic_distinct and structural_distinct
        if self.gate_passed != expected:
            raise ValueError("Seed diversity gate 状态与证据不一致。")
        if expected:
            expected_exception: DiversityException | None = None
        elif not semantic_distinct and not structural_distinct:
            expected_exception = "semantic_and_structural_diversity_missing"
        elif not semantic_distinct:
            expected_exception = "semantic_genome_hash_not_unique"
        else:
            expected_exception = "no_template_topology_or_enabled_layer_difference"
        if self.diversity_exception != expected_exception:
            raise ValueError("Seed diversity exception 与 gate 状态不一致。")
        return self


class ExpandedSeedV1(FrozenModel):
    """单个 SeedPlan 的确定性展开结果。."""

    plan: SeedPlanV1
    genome: TypedEffectGenome
    genome_hashes: GenomeHashes

    @model_validator(mode="after")
    def _validate_expanded_seed(self) -> ExpandedSeedV1:
        if compute_genome_hashes(self.genome) != self.genome_hashes:
            raise ValueError("ExpandedSeed genome hashes 与 Genome 内容不一致。")
        provenance = self.genome.provenance
        if (
            self.genome.strategy != self.plan.template_id
            or provenance.source != self.plan.source
            or provenance.intent_id != self.plan.intent_id
            or provenance.target_hypothesis_id != self.plan.target_hypothesis_id
            or provenance.target_hypothesis_hash != self.plan.target_hypothesis_hash
            or provenance.template_id != self.plan.template_id
            or provenance.template_version != self.plan.template_version
            or provenance.random_seed != self.plan.random_seed
            or provenance.evidence_refs != self.plan.evidence_refs
        ):
            raise ValueError("ExpandedSeed Genome provenance 未精确闭包 SeedPlan。")
        return self


class SeedExpansionResultV2(FrozenModel):
    """恰好三个 Seed 的展开结果及 fail-closed diversity gate。."""

    schema_version: Literal["seed_expansion_result_v2"] = "seed_expansion_result_v2"
    expanded_seeds: tuple[ExpandedSeedV1, ExpandedSeedV1, ExpandedSeedV1]
    diversity: SeedDiversityAssessmentV1

    @model_validator(mode="after")
    def _validate_result_closure(self) -> SeedExpansionResultV2:
        roles = tuple(item.plan.seed_role for item in self.expanded_seeds)
        if roles != (
            "minimum_complexity",
            "semantic_enhancement",
            "alternate_structure",
        ):
            raise ValueError("Seed expansion 必须按冻结的三个 seed role 排序。")
        hashes = tuple(
            item.genome_hashes.semantic_genome_hash for item in self.expanded_seeds
        )
        if hashes != self.diversity.semantic_genome_hashes:
            raise ValueError("Seed expansion 与 diversity hash 证据不一致。")
        structural = {
            (
                item.plan.template_id,
                item.genome_hashes.topology_hash,
                tuple(
                    binding.layer_id
                    for binding in item.plan.layer_bindings
                    if binding.enabled
                ),
            )
            for item in self.expanded_seeds
        }
        if len(structural) != self.diversity.distinct_structural_signatures:
            raise ValueError("Seed expansion 与 diversity 结构证据不一致。")
        return self


__all__ = [
    "AllowedOverrideV1",
    "BaseFillKind",
    "DiversityException",
    "ExpandedSeedV1",
    "GeometryKind",
    "LayerBindingV1",
    "OverrideParameterName",
    "OverrideValue",
    "SeedDiversityAssessmentV1",
    "SeedExpansionResultV2",
    "SeedPlanV1",
    "SeedRole",
    "SeedSource",
    "TemplateMatchV1",
]
