"""V2.1 VisualInterpretation 与 Intent IR 的严格领域模型。."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from shaderforge.analysis import BBoxUv
from shaderforge.contracts import FiniteFloat, FrozenModel, NonEmptyString, Sha256Hex
from shaderforge.contracts.taxonomy import REQUIRED_LAYER_ORDER, RequiredLayerTaxon
from shaderforge.intent.models import (
    Constraint,
    ConstraintKind,
    ConstraintSource,
    ConstraintValue,
    RequiredLayerConstraintValue,
)
from shaderforge.store import ArtifactRefV2

VisualLayerRole = Literal[
    "background",
    "shadow",
    "base_fill",
    "color_lobe",
    "haze",
    "rim",
    "outline",
    "highlight",
    "detail",
]
IntentLayerRole = RequiredLayerTaxon


class LayerHypothesis(FrozenModel):
    """模型对单个视觉层的推断，不携带确定性目标身份。."""

    layer_id: NonEmptyString
    role: VisualLayerRole
    order: int = Field(ge=0)
    confidence: FiniteFloat = Field(ge=0.0, le=1.0)
    region_description: NonEmptyString
    primitive_candidates: tuple[NonEmptyString, ...] = Field(min_length=1)
    evidence_refs: tuple[ArtifactRefV2, ...] = ()


class PrimitiveCandidate(FrozenModel):
    """模型提出的 primitive 候选。."""

    candidate_id: NonEmptyString
    primitive_id: NonEmptyString
    layer_id: NonEmptyString
    confidence: FiniteFloat = Field(ge=0.0, le=1.0)
    evidence_refs: tuple[ArtifactRefV2, ...] = ()


class StrategyHypothesis(FrozenModel):
    """模型或规则提出的模板策略假设。."""

    strategy_id: NonEmptyString
    template_ids: tuple[NonEmptyString, ...] = Field(min_length=1)
    required_layer_ids: tuple[NonEmptyString, ...] = Field(min_length=1)
    complexity: Literal["low", "medium", "high"]
    confidence: FiniteFloat = Field(ge=0.0, le=1.0)
    evidence_refs: tuple[ArtifactRefV2, ...] = ()


class Uncertainty(FrozenModel):
    """不能被模型推断冒充为确定性事实的不确定项。."""

    uncertainty_id: NonEmptyString
    subject: NonEmptyString
    description: NonEmptyString
    severity: Literal["low", "medium", "high"]
    evidence_refs: tuple[ArtifactRefV2, ...] = ()


class RequiredLayerAssessment(FrozenModel):
    """对共享 required-layer taxonomy 的逐项闭集判断。."""

    layer: RequiredLayerTaxon
    status: Literal["required", "not_required", "unknown"]
    confidence: FiniteFloat = Field(gt=0.0, le=1.0)
    provenance: Literal["model_inference"] = "model_inference"
    rationale: NonEmptyString
    evidence_refs: tuple[ArtifactRefV2, ...] = Field(min_length=1)


class VisualInterpretationV2(FrozenModel):
    """只保存推断；Schema 刻意不提供 target hash、尺寸或 bbox 字段。."""

    schema_version: Literal["visual_interpretation_v2_1"] = "visual_interpretation_v2_1"
    summary: NonEmptyString
    layer_hypotheses: tuple[LayerHypothesis, ...] = Field(min_length=1)
    required_layer_assessments: tuple[RequiredLayerAssessment, ...] = Field(
        min_length=len(REQUIRED_LAYER_ORDER),
        max_length=len(REQUIRED_LAYER_ORDER),
    )
    primitive_candidates: tuple[PrimitiveCandidate, ...]
    strategy_hypotheses: tuple[StrategyHypothesis, ...] = Field(min_length=1)
    uncertainties: tuple[Uncertainty, ...] = ()
    evidence_refs: tuple[ArtifactRefV2, ...] = ()

    @model_validator(mode="after")
    def _validate_references(self) -> VisualInterpretationV2:
        assessed_layers = tuple(item.layer for item in self.required_layer_assessments)
        if assessed_layers != REQUIRED_LAYER_ORDER:
            raise ValueError(
                "required_layer_assessments 必须按 taxonomy 顺序覆盖完整闭集。"
            )
        assessment_by_layer = {
            item.layer: item for item in self.required_layer_assessments
        }
        if assessment_by_layer["base_fill"].status != "required":
            raise ValueError("required-layer 闭集必须把 base_fill 标记为 required。")
        layer_ids = [item.layer_id for item in self.layer_hypotheses]
        if len(layer_ids) != len(set(layer_ids)):
            raise ValueError("VisualInterpretation layer_id 不得重复。")
        orders = [item.order for item in self.layer_hypotheses]
        if orders != sorted(orders) or len(orders) != len(set(orders)):
            raise ValueError("VisualInterpretation layer order 必须严格递增。")
        known_layers = set(layer_ids)
        if any(
            assessment_by_layer[item.role].status != "required"
            for item in self.layer_hypotheses
        ):
            raise ValueError(
                "已声明的 layer_hypothesis 必须在 required-layer 闭集中标记 required。"
            )
        candidate_ids = [item.candidate_id for item in self.primitive_candidates]
        strategy_ids = [item.strategy_id for item in self.strategy_hypotheses]
        uncertainty_ids = [item.uncertainty_id for item in self.uncertainties]
        for field_name, values in (
            ("primitive candidate_id", candidate_ids),
            ("strategy_id", strategy_ids),
            ("uncertainty_id", uncertainty_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"VisualInterpretation {field_name} 不得重复。")
        if any(item.layer_id not in known_layers for item in self.primitive_candidates):
            raise ValueError("primitive candidate 引用了不存在的 layer_id。")
        declared = {
            (layer.layer_id, primitive_id)
            for layer in self.layer_hypotheses
            for primitive_id in layer.primitive_candidates
        }
        actual = {
            (candidate.layer_id, candidate.primitive_id)
            for candidate in self.primitive_candidates
        }
        if declared != actual:
            raise ValueError(
                "layer primitive_candidates 必须与 PrimitiveCandidate 集精确闭包。"
            )
        if any(
            layer_id not in known_layers
            for item in self.strategy_hypotheses
            for layer_id in item.required_layer_ids
        ):
            raise ValueError("strategy hypothesis 引用了不存在的 layer_id。")
        return self


class CanvasIntent(FrozenModel):
    """来自确定性上下文的画布契约。."""

    contract_id: NonEmptyString
    coordinate_system: Literal["shader_uv_bottom_left"] = "shader_uv_bottom_left"
    image_size: tuple[int, int]

    @model_validator(mode="after")
    def _validate_size(self) -> CanvasIntent:
        if any(value <= 0 for value in self.image_size):
            raise ValueError("CanvasIntent image_size 必须为正整数。")
        return self


class IntentBuildContext(FrozenModel):
    """唯一合并入口显式接收的冻结上下文。."""

    schema_version: Literal["intent_build_context_v1"] = "intent_build_context_v1"
    contract_id: NonEmptyString
    primitive_catalog_version: Literal["png_to_shader_expected_primitives_v1"]
    primitive_catalog_sha256: Sha256Hex
    template_catalog_version: Literal["png_to_shader_expected_primitives_v1"]
    template_catalog_sha256: Sha256Hex
    allowed_primitive_ids: tuple[NonEmptyString, ...] = Field(min_length=1)
    allowed_template_ids: tuple[NonEmptyString, ...] = Field(min_length=1)
    allowed_interpretation_evidence_refs: tuple[ArtifactRefV2, ...] = Field(
        min_length=1
    )

    @model_validator(mode="after")
    def _validate_catalogs(self) -> IntentBuildContext:
        for name, values in (
            ("allowed_primitive_ids", self.allowed_primitive_ids),
            ("allowed_template_ids", self.allowed_template_ids),
        ):
            if tuple(values) != tuple(sorted(set(values))):
                raise ValueError(
                    f"IntentBuildContext {name} 必须唯一且按字典序规范化。"
                )
        evidence_keys = [
            (
                ref.sha256,
                ref.kind,
                ref.schema_version,
                ref.content_type,
                ref.size_bytes,
            )
            for ref in self.allowed_interpretation_evidence_refs
        ]
        if evidence_keys != sorted(set(evidence_keys)):
            raise ValueError(
                "IntentBuildContext evidence refs 必须按内容语义唯一且规范排序。"
            )
        return self


class InstanceIntent(FrozenModel):
    """绑定 mask 像素重测得到的逐实例几何，不从 aggregate 推断。."""

    schema_version: Literal["instance_intent_v2"] = "instance_intent_v2"
    instance_id: NonEmptyString
    instance_index: int = Field(ge=0)
    mask_ref: ArtifactRefV2
    bbox_uv: BBoxUv
    center_uv: tuple[FiniteFloat, FiniteFloat]
    area_ratio: FiniteFloat = Field(gt=0.0, le=1.0)
    axes_uv: tuple[FiniteFloat, FiniteFloat]
    orientation_rad: FiniteFloat
    fill_topology: Literal["solid", "hollow", "ring", "open"]
    component_count: int = Field(ge=1)
    hole_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_geometry(self) -> InstanceIntent:
        if any(not 0.0 <= value <= 1.0 for value in self.center_uv):
            raise ValueError("InstanceIntent center_uv 必须位于 0 到 1。")
        if any(value <= 0.0 for value in self.axes_uv):
            raise ValueError("InstanceIntent axes_uv 必须大于 0。")
        if self.fill_topology in {"ring", "hollow"} and self.hole_count < 1:
            raise ValueError("InstanceIntent ring/hollow 必须至少有一个 hole。")
        if self.fill_topology in {"solid", "open"} and self.hole_count != 0:
            raise ValueError("InstanceIntent solid/open 的 hole_count 必须为 0。")
        return self


class ObjectIntent(FrozenModel):
    """TargetHypothesis 主体级确定性结构投影。."""

    object_id: NonEmptyString
    subject_mask_ref: ArtifactRefV2
    instances: tuple[InstanceIntent, ...] = Field(min_length=1)
    bbox_uv: BBoxUv
    center_uv: tuple[FiniteFloat, FiniteFloat]
    area_ratio: FiniteFloat = Field(ge=0.0, le=1.0)
    axes_uv: tuple[FiniteFloat, FiniteFloat]
    orientation_rad: FiniteFloat
    topology: Literal["solid", "hollow", "ring", "open"]
    component_count: int = Field(ge=1)
    instance_count: int = Field(ge=1)
    hole_count: int = Field(ge=0)
    confidence: FiniteFloat = Field(ge=0.0, le=1.0)
    radial_segment_evidence_ref: ArtifactRefV2 | None = None
    evidence_refs: tuple[ArtifactRefV2, ...] = ()

    @model_validator(mode="after")
    def _validate_instances(self) -> ObjectIntent:
        if self.instance_count != len(self.instances):
            raise ValueError("ObjectIntent instance_count 必须等于 instances 数量。")
        ids = [item.instance_id for item in self.instances]
        indexes = [item.instance_index for item in self.instances]
        if len(ids) != len(set(ids)) or indexes != list(range(len(indexes))):
            raise ValueError("ObjectIntent instances 必须具有连续索引和唯一 id。")
        if self.radial_segment_evidence_ref is not None:
            if self.topology != "ring" or self.instance_count < 3:
                raise ValueError("radial segment evidence 只适用于三段以上 ring object。")
            if self.radial_segment_evidence_ref not in self.evidence_refs:
                raise ValueError("radial segment evidence 必须进入 object evidence 闭包。")
        return self


class VisualLayerIntent(FrozenModel):
    """Intent 中按合成顺序排列的视觉层。."""

    layer_id: NonEmptyString
    role: IntentLayerRole
    order: int = Field(ge=0)
    object_ref: NonEmptyString | None
    required: bool
    source: Literal["constraint", "model", "policy"]
    confidence: FiniteFloat = Field(ge=0.0, le=1.0)
    region_description: NonEmptyString | None = None
    primitive_candidate_ids: tuple[NonEmptyString, ...] = ()
    required_by_constraint_ids: tuple[NonEmptyString, ...] = ()
    evidence_refs: tuple[ArtifactRefV2, ...] = ()


class RelationIntent(FrozenModel):
    """由确定性测量继承的对象关系。."""

    relation_id: NonEmptyString
    kind: Literal["overlap", "contains", "subtracts", "touches", "disjoint"]
    subject_ref: NonEmptyString
    object_ref: NonEmptyString
    confidence: FiniteFloat = Field(ge=0.0, le=1.0)
    evidence_refs: tuple[ArtifactRefV2, ...] = ()


class RegionIntent(FrozenModel):
    """确定性区域统计在 Intent 中的投影。."""

    region_id: NonEmptyString
    bbox_uv: BBoxUv
    area_ratio: FiniteFloat = Field(ge=0.0, le=1.0)
    mean_lab: tuple[FiniteFloat, FiniteFloat, FiniteFloat]


class PixelProbeIntent(FrozenModel):
    """可被 Compiler/Oracle 使用的像素探针。."""

    probe_id: NonEmptyString
    uv: tuple[FiniteFloat, FiniteFloat]
    purpose: NonEmptyString
    evidence_refs: tuple[ArtifactRefV2, ...] = ()

    @model_validator(mode="after")
    def _validate_uv(self) -> PixelProbeIntent:
        if any(value < 0.0 or value > 1.0 for value in self.uv):
            raise ValueError("PixelProbeIntent uv 必须位于 0 到 1。")
        return self


class Preference(FrozenModel):
    """保留完整 typed payload 与 provenance 的 soft constraint 投影。."""

    preference_id: NonEmptyString
    kind: ConstraintKind
    scope: Literal["global", "object", "region", "parameter"]
    scope_ref: NonEmptyString | None = None
    value: ConstraintValue
    weight: FiniteFloat = Field(gt=0.0, le=1.0)
    source_constraint_id: NonEmptyString
    source: ConstraintSource
    verification_status: Literal["verified", "inferred", "unverified", "rejected"]
    evidence_refs: tuple[ArtifactRefV2, ...] = ()

    @model_validator(mode="after")
    def _validate_payload_and_scope(self) -> Preference:
        if self.kind != self.value.kind:
            raise ValueError("Preference.kind 必须与 value.kind 一致。")
        if self.scope == "global" and self.scope_ref is not None:
            raise ValueError("global Preference 不得设置 scope_ref。")
        if self.scope != "global" and self.scope_ref is None:
            raise ValueError("非 global Preference 必须设置 scope_ref。")
        return self


class IntentIR(FrozenModel):
    """单个 TargetHypothesis 对应的完整、不可变 Intent variant。."""

    schema_version: Literal["intent_v3"] = "intent_v3"
    intent_id: NonEmptyString
    target_sha256: Sha256Hex
    target_hypothesis_id: NonEmptyString
    target_hypothesis_hash: Sha256Hex
    constraint_set_hash: Sha256Hex
    canvas: CanvasIntent
    objects: tuple[ObjectIntent, ...] = Field(min_length=1)
    layers: tuple[VisualLayerIntent, ...] = Field(min_length=1)
    relations: tuple[RelationIntent, ...]
    regions: tuple[RegionIntent, ...]
    probes: tuple[PixelProbeIntent, ...]
    hard_constraints: tuple[Constraint, ...]
    soft_preferences: tuple[Preference, ...]
    primitive_candidates: tuple[PrimitiveCandidate, ...]
    strategy_hypotheses: tuple[StrategyHypothesis, ...] = Field(min_length=1)
    uncertainties: tuple[Uncertainty, ...]
    evidence_refs: tuple[ArtifactRefV2, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_collections(self) -> IntentIR:
        for field_name, values in (
            ("object_id", [item.object_id for item in self.objects]),
            ("layer_id", [item.layer_id for item in self.layers]),
            ("relation_id", [item.relation_id for item in self.relations]),
            ("region_id", [item.region_id for item in self.regions]),
            ("probe_id", [item.probe_id for item in self.probes]),
            ("preference_id", [item.preference_id for item in self.soft_preferences]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"IntentIR {field_name} 不得重复。")
        orders = [item.order for item in self.layers]
        if orders != sorted(orders) or len(orders) != len(set(orders)):
            raise ValueError("IntentIR layer order 必须严格递增。")
        object_ids = {item.object_id for item in self.objects}
        all_instance_ids = [
            instance.instance_id for item in self.objects for instance in item.instances
        ]
        if len(all_instance_ids) != len(set(all_instance_ids)):
            raise ValueError("IntentIR instance_id 在全部 objects 中必须唯一。")
        instance_ids = set(all_instance_ids)
        addressable_ids = object_ids | instance_ids
        if any(
            item.object_ref is not None and item.object_ref not in addressable_ids
            for item in self.layers
        ):
            raise ValueError("IntentIR layer 引用了不存在的 object。")
        if any(
            item.subject_ref not in addressable_ids
            or item.object_ref not in addressable_ids
            for item in self.relations
        ):
            raise ValueError("IntentIR relation 引用了不存在的 object/instance。")
        candidate_id_list = [item.candidate_id for item in self.primitive_candidates]
        if len(candidate_id_list) != len(set(candidate_id_list)):
            raise ValueError("IntentIR primitive candidate_id 不得重复。")
        candidate_ids = set(candidate_id_list)
        if any(
            candidate_id not in candidate_ids
            for layer in self.layers
            for candidate_id in layer.primitive_candidate_ids
        ):
            raise ValueError("IntentIR layer 引用了不存在的 primitive candidate。")
        layer_by_id = {item.layer_id: item for item in self.layers}
        if any(
            candidate.layer_id not in layer_by_id
            or candidate.candidate_id
            not in layer_by_id[candidate.layer_id].primitive_candidate_ids
            for candidate in self.primitive_candidates
        ):
            raise ValueError("IntentIR primitive candidate 与 layer 引用未形成闭包。")
        if any(
            layer_id not in layer_by_id
            for strategy in self.strategy_hypotheses
            for layer_id in strategy.required_layer_ids
        ):
            raise ValueError("IntentIR strategy 引用了不存在的 layer。")
        hard_ids = [item.constraint_id for item in self.hard_constraints]
        if len(hard_ids) != len(set(hard_ids)):
            raise ValueError("IntentIR hard constraint id 不得重复。")
        required_constraint_by_id = {
            item.constraint_id: (item, item.value)
            for item in self.hard_constraints
            if isinstance(item.value, RequiredLayerConstraintValue)
        }
        for layer in self.layers:
            if not layer.required and layer.required_by_constraint_ids:
                raise ValueError("非 required layer 不得绑定 required constraint。")
            if layer.role != "base_fill" and layer.required:
                if not layer.required_by_constraint_ids and not (
                    layer.source == "model" and layer.evidence_refs
                ):
                    raise ValueError(
                        "required layer 必须绑定 constraint 或模型闭集 evidence。"
                    )
            for constraint_id in layer.required_by_constraint_ids:
                constraint_entry = required_constraint_by_id.get(constraint_id)
                if constraint_entry is None:
                    raise ValueError("required layer 与 constraint scope/role 不一致。")
                constraint, value = constraint_entry
                if (
                    value.layer != layer.role
                    or constraint.scope_ref != layer.object_ref
                ):
                    raise ValueError("required layer 与 constraint scope/role 不一致。")
        bound_required_ids = {
            constraint_id
            for layer in self.layers
            for constraint_id in layer.required_by_constraint_ids
        }
        if bound_required_ids != set(required_constraint_by_id):
            raise ValueError("IntentIR 必须精确绑定全部 required-layer constraints。")
        top_level_evidence = set(self.evidence_refs)
        nested_evidence = {
            ref
            for ref in (
                *(item.subject_mask_ref for item in self.objects),
                *(
                    instance.mask_ref
                    for item in self.objects
                    for instance in item.instances
                ),
                *(ref for item in self.objects for ref in item.evidence_refs),
                *(ref for item in self.layers for ref in item.evidence_refs),
                *(ref for item in self.relations for ref in item.evidence_refs),
                *(
                    ref
                    for item in self.primitive_candidates
                    for ref in item.evidence_refs
                ),
                *(
                    ref
                    for item in self.strategy_hypotheses
                    for ref in item.evidence_refs
                ),
                *(ref for item in self.uncertainties for ref in item.evidence_refs),
                *(ref for item in self.hard_constraints for ref in item.evidence_refs),
                *(ref for item in self.soft_preferences for ref in item.evidence_refs),
            )
        }
        if not nested_evidence <= top_level_evidence:
            raise ValueError("IntentIR evidence_refs 未覆盖全部嵌套证据与 mask。")
        return self


class IntentVariantRejection(FrozenModel):
    """单个 hypothesis 因 hard constraint 不可行而产生的结构化拒绝。."""

    target_hypothesis_id: NonEmptyString
    target_hypothesis_hash: Sha256Hex
    reason_codes: tuple[NonEmptyString, ...] = Field(min_length=1)


class IntentBuildResult(FrozenModel):
    """一次唯一合并入口产生的 Intent variants 与拒绝集合。."""

    schema_version: Literal["intent_build_result_v3"] = "intent_build_result_v3"
    builder_version: Literal["intent_builder_v3"]
    target_sha256: Sha256Hex
    measurements_hash: Sha256Hex
    interpretation_hash: Sha256Hex
    build_context_hash: Sha256Hex
    constraint_set_hash: Sha256Hex
    source_hypotheses: tuple[tuple[NonEmptyString, Sha256Hex], ...] = Field(
        min_length=1
    )
    variants: tuple[IntentIR, ...]
    rejections: tuple[IntentVariantRejection, ...]

    @model_validator(mode="after")
    def _validate_partition(self) -> IntentBuildResult:
        variant_identities = [
            (item.target_hypothesis_id, item.target_hypothesis_hash)
            for item in self.variants
        ]
        rejection_identities = [
            (item.target_hypothesis_id, item.target_hypothesis_hash)
            for item in self.rejections
        ]
        identities = [*variant_identities, *rejection_identities]
        if len(identities) != len(set(identities)):
            raise ValueError("一个 hypothesis 只能产生 variant 或 rejection。")
        if len(self.source_hypotheses) != len(set(self.source_hypotheses)):
            raise ValueError("IntentBuildResult source hypotheses 不得重复。")
        if set(identities) != set(self.source_hypotheses):
            raise ValueError("IntentBuildResult 必须完整覆盖输入 hypotheses。")
        if any(item.target_sha256 != self.target_sha256 for item in self.variants):
            raise ValueError("IntentBuildResult target_sha256 不一致。")
        if any(
            item.constraint_set_hash != self.constraint_set_hash
            for item in self.variants
        ):
            raise ValueError("IntentBuildResult constraint_set_hash 不一致。")
        return self


__all__ = [
    "CanvasIntent",
    "IntentBuildContext",
    "IntentBuildResult",
    "IntentIR",
    "IntentVariantRejection",
    "InstanceIntent",
    "IntentLayerRole",
    "LayerHypothesis",
    "ObjectIntent",
    "PixelProbeIntent",
    "Preference",
    "PrimitiveCandidate",
    "RegionIntent",
    "RelationIntent",
    "StrategyHypothesis",
    "Uncertainty",
    "VisualInterpretationV2",
    "VisualLayerIntent",
    "VisualLayerRole",
]
