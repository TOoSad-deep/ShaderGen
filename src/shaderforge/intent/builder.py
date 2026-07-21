"""Measurements、Interpretation、ConstraintSet 到 Intent variants 的唯一合并入口。."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import BaseModel

from shaderforge.analysis import TargetHypothesis, TargetMeasurementsV2
from shaderforge.contracts import canonical_sha256
from shaderforge.contracts.png_to_shader_v1 import WEBGL1_STATIC_NO_TEXTURE_V1
from shaderforge.contracts.taxonomy import REQUIRED_LAYER_ORDER, RequiredLayerTaxon
from shaderforge.intent.canonical import assert_intent_compatible_constraints
from shaderforge.intent.constraints_builder import (
    validate_request_constraint_set_policy,
)
from shaderforge.intent.ir import (
    CanvasIntent,
    InstanceIntent,
    IntentBuildContext,
    IntentBuildResult,
    IntentIR,
    IntentVariantRejection,
    ObjectIntent,
    Preference,
    RegionIntent,
    RelationIntent,
    VisualInterpretationV2,
    VisualLayerIntent,
)
from shaderforge.intent.models import (
    Constraint,
    ContractConstraintValue,
    HoleCountConstraintValue,
    InstanceCountConstraintValue,
    RegionLockConstraintValue,
    RequestConstraintSet,
    RequiredLayerConstraintValue,
    TopologyConstraintValue,
)
from shaderforge.store import ArtifactRefV2

INTENT_BUILDER_VERSION: Literal["intent_builder_v3"] = "intent_builder_v3"
_LAYER_ORDER = REQUIRED_LAYER_ORDER


def _artifact_semantic_key(ref: ArtifactRefV2) -> tuple[str, str, str, str, int]:
    return (
        ref.sha256,
        ref.kind,
        ref.schema_version,
        ref.content_type,
        ref.size_bytes,
    )


def _semantic_projection(value: object) -> object:
    """递归排除 Artifact record id，只保留可跨存储复验的内容语义。."""
    if isinstance(value, ArtifactRefV2):
        sha, kind, schema, content_type, size = _artifact_semantic_key(value)
        return {
            "sha256": sha,
            "kind": kind,
            "schema_version": schema,
            "content_type": content_type,
            "size_bytes": size,
        }
    if isinstance(value, BaseModel):
        return {key: _semantic_projection(item) for key, item in value.__iter__()}
    if isinstance(value, Mapping):
        return {str(key): _semantic_projection(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_semantic_projection(item) for item in value]
    return value


def compute_intent_input_hash(value: BaseModel) -> str:
    """计算 Builder 输入的内容语义 hash。."""
    return canonical_sha256(_semantic_projection(value))


def build_intent_build_context(
    *,
    contract_id: str,
    primitive_catalog_sha256: str,
    template_catalog_sha256: str,
    allowed_primitive_ids: Iterable[str],
    allowed_template_ids: Iterable[str],
    allowed_interpretation_evidence_refs: Iterable[ArtifactRefV2],
) -> IntentBuildContext:
    """由已验证 catalog 身份构造唯一规范化 V2.1 Context。."""
    refs_by_semantics: dict[tuple[str, str, str, str, int], ArtifactRefV2] = {}
    for ref in sorted(
        allowed_interpretation_evidence_refs,
        key=lambda item: (*_artifact_semantic_key(item), item.artifact_id),
    ):
        refs_by_semantics.setdefault(_artifact_semantic_key(ref), ref)
    return IntentBuildContext(
        contract_id=contract_id,
        primitive_catalog_version="png_to_shader_expected_primitives_v1",
        primitive_catalog_sha256=primitive_catalog_sha256,
        template_catalog_version="png_to_shader_expected_primitives_v1",
        template_catalog_sha256=template_catalog_sha256,
        allowed_primitive_ids=tuple(sorted(set(allowed_primitive_ids))),
        allowed_template_ids=tuple(sorted(set(allowed_template_ids))),
        allowed_interpretation_evidence_refs=tuple(
            refs_by_semantics[key] for key in sorted(refs_by_semantics)
        ),
    )


def _effective_constraints(
    constraint_set: RequestConstraintSet,
) -> tuple[Constraint, ...]:
    excluded: set[str] = set()
    for conflict in constraint_set.conflicts:
        if conflict.status != "resolved":
            continue
        excluded.update(
            constraint_id
            for constraint_id in conflict.constraint_ids
            if constraint_id != conflict.selected_constraint_id
        )
    return tuple(
        sorted(
            (
                item
                for item in constraint_set.constraints
                if item.constraint_id not in excluded
                and item.verification_status != "rejected"
            ),
            key=lambda item: item.constraint_id,
        )
    )


def _all_interpretation_evidence(
    interpretation: VisualInterpretationV2,
) -> tuple[ArtifactRefV2, ...]:
    return (
        *interpretation.evidence_refs,
        *(
            ref
            for layer in interpretation.layer_hypotheses
            for ref in layer.evidence_refs
        ),
        *(
            ref
            for candidate in interpretation.primitive_candidates
            for ref in candidate.evidence_refs
        ),
        *(
            ref
            for strategy in interpretation.strategy_hypotheses
            for ref in strategy.evidence_refs
        ),
        *(
            ref
            for assessment in interpretation.required_layer_assessments
            for ref in assessment.evidence_refs
        ),
        *(ref for item in interpretation.uncertainties for ref in item.evidence_refs),
    )


def _validate_required_layer_completeness(
    interpretation: VisualInterpretationV2,
    effective_constraints: tuple[Constraint, ...],
) -> None:
    """拒绝 unknown 及 hard required 与模型 not_required 的显式冲突。."""
    if any(
        assessment.status == "unknown"
        for assessment in interpretation.required_layer_assessments
    ):
        raise ValueError("required-layer 闭集包含 unknown，不能生成可准入 Intent。")
    status_by_layer = {
        assessment.layer: assessment.status
        for assessment in interpretation.required_layer_assessments
    }
    constrained = {
        item.value.layer
        for item in effective_constraints
        if item.strength == "hard"
        and isinstance(item.value, RequiredLayerConstraintValue)
    }
    conflicting = sorted(
        layer for layer in constrained if status_by_layer[layer] == "not_required"
    )
    if conflicting:
        raise ValueError(
            "hard required-layer constraints 与模型 not_required 判断冲突："
            + ",".join(conflicting)
        )


def _validate_interpretation_evidence(
    interpretation: VisualInterpretationV2,
    context: IntentBuildContext,
) -> None:
    allowed = {
        _artifact_semantic_key(ref)
        for ref in context.allowed_interpretation_evidence_refs
    }
    if any(
        _artifact_semantic_key(ref) not in allowed
        for ref in _all_interpretation_evidence(interpretation)
    ):
        raise ValueError("VisualInterpretation 引用了合并上下文未授权的 evidence。")


def _validate_interpretation_catalogs(
    interpretation: VisualInterpretationV2,
    context: IntentBuildContext,
) -> None:
    primitive_ids = {item.primitive_id for item in interpretation.primitive_candidates}
    template_ids = {
        template_id
        for item in interpretation.strategy_hypotheses
        for template_id in item.template_ids
    }
    unknown_primitives = primitive_ids.difference(context.allowed_primitive_ids)
    unknown_templates = template_ids.difference(context.allowed_template_ids)
    if unknown_primitives or unknown_templates:
        raise ValueError(
            "VisualInterpretation 引用了 Context 未授权的 primitive/template id。"
        )


def _hypothesis_rejections(
    hypothesis: TargetHypothesis,
    hard_constraints: tuple[Constraint, ...],
    *,
    contract_id: str,
) -> tuple[str, ...]:
    reasons: list[str] = []
    for constraint in hard_constraints:
        value = constraint.value
        if isinstance(value, ContractConstraintValue):
            if value.contract_id != contract_id:
                reasons.append("contract_mismatch")
        elif isinstance(value, TopologyConstraintValue):
            if value.topology != hypothesis.fill_topology:
                reasons.append("topology_mismatch")
        elif isinstance(value, InstanceCountConstraintValue):
            if value.exact_count != hypothesis.instance_count:
                reasons.append("instance_count_mismatch")
        elif isinstance(value, HoleCountConstraintValue):
            if value.exact_count != hypothesis.hole_count:
                reasons.append("hole_count_mismatch")
    return tuple(dict.fromkeys(reasons))


def _validate_contract_constraint(
    constraints: tuple[Constraint, ...],
    *,
    context: IntentBuildContext,
) -> None:
    contracts = [item for item in constraints if item.kind == "contract"]
    if len(contracts) != 1:
        raise ValueError("Intent Builder 要求唯一有效 RenderContract constraint。")
    contract = contracts[0]
    value = contract.value
    if not isinstance(value, ContractConstraintValue):
        raise ValueError("contract constraint payload 不合法。")
    if not (
        contract.source == "render_contract"
        and contract.strength == "hard"
        and contract.verification_status == "verified"
        and contract.scope == "global"
        and value.contract_id == context.contract_id
        and value.contract_id == WEBGL1_STATIC_NO_TEXTURE_V1.contract_id
    ):
        raise ValueError(
            "RenderContract 必须是 verified hard、来自 render_contract，"
            "并绑定当前 webgl1_static_no_texture_v1。"
        )


def _validate_hard_constraint_sources(constraints: tuple[Constraint, ...]) -> None:
    for constraint in constraints:
        if constraint.strength != "hard":
            continue
        if constraint.source == "model":
            raise ValueError("model inference 不得直接进入 Intent hard constraints。")
        if constraint.source == "measurement" and (
            constraint.verification_status != "verified" or not constraint.evidence_refs
        ):
            raise ValueError(
                "measurement hard constraint 必须 verified 且绑定非空 evidence。"
            )


def _validate_constraint_scopes(
    constraints: tuple[Constraint, ...],
    *,
    measurements: TargetMeasurementsV2,
) -> None:
    region_ids = {item.region_id for item in measurements.region_statistics}
    for constraint in constraints:
        if constraint.kind == "contract":
            if constraint.scope != "global":
                raise ValueError("contract constraint 必须使用 global scope。")
            continue
        if constraint.kind in {
            "topology",
            "instance_count",
            "hole_count",
        }:
            if constraint.scope != "object" or constraint.scope_ref != "subject":
                raise ValueError(f"{constraint.kind} 当前只支持 object:subject scope。")
            continue
        if constraint.kind == "required_layer":
            value = constraint.value
            background = (
                isinstance(value, RequiredLayerConstraintValue)
                and value.layer == "background"
            )
            valid = (
                constraint.scope == "global" and constraint.scope_ref is None
                if background
                else constraint.scope == "object" and constraint.scope_ref == "subject"
            )
            if not valid:
                raise ValueError(
                    "required_layer 必须按 background:global 或其他层:object:subject "
                    "绑定。"
                )
            continue
        if constraint.kind in {"region_lock", "color_lock"}:
            value_region_id = getattr(constraint.value, "region_id", None)
            if (
                constraint.scope != "region"
                or constraint.scope_ref not in region_ids
                or value_region_id != constraint.scope_ref
            ):
                raise ValueError(
                    f"{constraint.kind} 必须绑定存在且与 payload 一致的 region。"
                )
            continue
        if constraint.kind in {"complexity", "budget"}:
            if constraint.scope != "global":
                raise ValueError(f"{constraint.kind} 当前只支持 global scope。")


def _unique_refs(refs: Iterable[ArtifactRefV2]) -> tuple[ArtifactRefV2, ...]:
    by_identity: dict[tuple[str, str, str], ArtifactRefV2] = {}
    for ref in sorted(refs, key=lambda item: item.artifact_id):
        by_identity.setdefault((ref.sha256, ref.kind, ref.schema_version), ref)
    return tuple(by_identity[key] for key in sorted(by_identity))


def _constraint_value_refs(
    constraints: tuple[Constraint, ...],
) -> tuple[ArtifactRefV2, ...]:
    return tuple(
        item.value.mask_ref
        for item in constraints
        if isinstance(item.value, RegionLockConstraintValue)
    )


def _build_layers(
    interpretation: VisualInterpretationV2,
    hard_constraints: tuple[Constraint, ...],
    *,
    object_ref: str,
) -> tuple[VisualLayerIntent, ...]:
    required_by_role: dict[RequiredLayerTaxon, tuple[str, ...]] = {}
    for role in _LAYER_ORDER:
        ids = tuple(
            constraint.constraint_id
            for constraint in hard_constraints
            if isinstance(constraint.value, RequiredLayerConstraintValue)
            and constraint.value.layer == role
        )
        if ids:
            required_by_role[role] = ids
    required_roles = set(required_by_role)
    required_roles.add("base_fill")
    assessments = {
        item.layer: item for item in interpretation.required_layer_assessments
    }
    required_roles.update(
        item.layer
        for item in interpretation.required_layer_assessments
        if item.status == "required"
    )
    candidates_by_layer = {
        layer.layer_id: tuple(
            candidate.candidate_id
            for candidate in interpretation.primitive_candidates
            if candidate.layer_id == layer.layer_id
        )
        for layer in interpretation.layer_hypotheses
    }
    layers_by_role: dict[RequiredLayerTaxon, list[VisualLayerIntent]] = {
        role: [] for role in _LAYER_ORDER
    }
    for hypothesis in interpretation.layer_hypotheses:
        required = hypothesis.role in required_roles
        layers_by_role[hypothesis.role].append(
            VisualLayerIntent(
                layer_id=hypothesis.layer_id,
                role=hypothesis.role,
                order=0,
                object_ref=None if hypothesis.role == "background" else object_ref,
                required=required,
                source="model",
                confidence=hypothesis.confidence,
                region_description=hypothesis.region_description,
                primitive_candidate_ids=candidates_by_layer[hypothesis.layer_id],
                required_by_constraint_ids=required_by_role.get(
                    hypothesis.role,
                    (),
                ),
                evidence_refs=hypothesis.evidence_refs,
            )
        )
    for role in sorted(required_roles, key=_LAYER_ORDER.index):
        if layers_by_role[role]:
            continue
        assessment = assessments[role]
        inferred = assessment.status == "required"
        layers_by_role[role].append(
            VisualLayerIntent(
                layer_id=f"required-{role}",
                role=role,
                order=0,
                object_ref=None if role == "background" else object_ref,
                required=True,
                source=(
                    "model"
                    if inferred
                    else "policy"
                    if role == "base_fill"
                    else "constraint"
                ),
                confidence=assessment.confidence if inferred else 0.0,
                region_description=assessment.rationale if inferred else None,
                required_by_constraint_ids=required_by_role.get(role, ()),
                evidence_refs=assessment.evidence_refs if inferred else (),
            )
        )
    ordered: list[VisualLayerIntent] = []
    for role in _LAYER_ORDER:
        ordered.extend(layers_by_role[role])
    return tuple(
        item.model_copy(update={"order": order}) for order, item in enumerate(ordered)
    )


def _preference(constraint: Constraint) -> Preference:
    return Preference(
        preference_id=f"preference_{canonical_sha256({'constraint_id': constraint.constraint_id})}",
        kind=constraint.kind,
        scope=constraint.scope,
        scope_ref=constraint.scope_ref,
        value=constraint.value,
        weight=max(0.000001, constraint.confidence),
        source_constraint_id=constraint.constraint_id,
        source=constraint.source,
        verification_status=constraint.verification_status,
        evidence_refs=constraint.evidence_refs,
    )


def compute_intent_id(intent: IntentIR) -> str:
    """计算排除 intent_id 的完整 Intent 语义身份。."""
    projection = _semantic_projection(intent)
    if not isinstance(projection, dict):
        raise TypeError("Intent 语义投影必须是 object。")
    projection.pop("intent_id", None)
    return f"intent_{canonical_sha256({'builder_version': INTENT_BUILDER_VERSION, 'intent': projection})}"


def _build_variant(
    measurements: TargetMeasurementsV2,
    interpretation: VisualInterpretationV2,
    constraint_set: RequestConstraintSet,
    context: IntentBuildContext,
    hypothesis: TargetHypothesis,
    effective_constraints: tuple[Constraint, ...],
) -> IntentIR:
    hard_constraints = tuple(
        item for item in effective_constraints if item.strength == "hard"
    )
    soft_constraints = tuple(
        item for item in effective_constraints if item.strength == "soft"
    )
    objects = (
        ObjectIntent(
            object_id="subject",
            subject_mask_ref=hypothesis.subject_mask_ref,
            instances=tuple(
                InstanceIntent(
                    instance_id=f"instance_{geometry.instance_index:04d}",
                    instance_index=geometry.instance_index,
                    mask_ref=geometry.mask_ref,
                    bbox_uv=geometry.bbox_uv,
                    center_uv=geometry.center_uv,
                    area_ratio=geometry.area_ratio,
                    axes_uv=geometry.axes_uv,
                    orientation_rad=geometry.orientation_rad,
                    fill_topology=geometry.fill_topology,
                    component_count=geometry.component_count,
                    hole_count=geometry.hole_count,
                )
                for geometry in hypothesis.instance_geometries
            ),
            bbox_uv=hypothesis.bbox_uv,
            center_uv=hypothesis.center_uv,
            area_ratio=hypothesis.area_ratio,
            axes_uv=hypothesis.axes_uv,
            orientation_rad=hypothesis.orientation_rad,
            topology=hypothesis.fill_topology,
            component_count=hypothesis.component_count,
            instance_count=hypothesis.instance_count,
            hole_count=hypothesis.hole_count,
            confidence=hypothesis.confidence,
            radial_segment_evidence_ref=hypothesis.radial_segment_evidence_ref,
            evidence_refs=hypothesis.evidence_refs,
        ),
    )
    primary_object_id = "subject"
    relations = tuple(
        RelationIntent(
            relation_id=item.relation_id,
            kind=item.kind,
            subject_ref=item.subject_ref,
            object_ref=item.object_ref,
            confidence=item.confidence,
            evidence_refs=item.evidence_refs,
        )
        for item in hypothesis.relations
    )
    regions = tuple(
        RegionIntent(
            region_id=item.region_id,
            bbox_uv=item.bbox_uv,
            area_ratio=item.area_ratio,
            mean_lab=item.mean_lab,
        )
        for item in measurements.region_statistics
    )
    evidence_refs = _unique_refs(
        (
            measurements.evidence_index_ref,
            *measurements.edge_refs,
            hypothesis.subject_mask_ref,
            *hypothesis.instance_mask_refs,
            *hypothesis.evidence_refs,
            *(ref for item in hypothesis.relations for ref in item.evidence_refs),
            *constraint_set.evidence_refs,
            *(ref for item in effective_constraints for ref in item.evidence_refs),
            *_constraint_value_refs(effective_constraints),
            *_all_interpretation_evidence(interpretation),
        )
    )
    draft = IntentIR(
        intent_id="pending",
        target_sha256=measurements.target_sha256,
        target_hypothesis_id=hypothesis.hypothesis_id,
        target_hypothesis_hash=hypothesis.hypothesis_hash,
        constraint_set_hash=constraint_set.constraint_set_hash,
        canvas=CanvasIntent(
            contract_id=context.contract_id,
            image_size=measurements.image_size,
        ),
        objects=objects,
        layers=_build_layers(
            interpretation,
            hard_constraints,
            object_ref=primary_object_id,
        ),
        relations=relations,
        regions=regions,
        probes=(),
        hard_constraints=hard_constraints,
        soft_preferences=tuple(_preference(item) for item in soft_constraints),
        primitive_candidates=interpretation.primitive_candidates,
        strategy_hypotheses=interpretation.strategy_hypotheses,
        uncertainties=interpretation.uncertainties,
        evidence_refs=evidence_refs,
    )
    return draft.model_copy(update={"intent_id": compute_intent_id(draft)})


def build_intent_variants(
    measurements: TargetMeasurementsV2,
    interpretation: VisualInterpretationV2,
    constraint_set: RequestConstraintSet,
    context: IntentBuildContext,
) -> IntentBuildResult:
    """唯一合并入口；hard constraint 不可行的 hypothesis 只产生拒绝。."""
    assert_intent_compatible_constraints(constraint_set)
    validate_request_constraint_set_policy(constraint_set)
    if measurements.target_sha256 != constraint_set.target_sha256:
        raise ValueError("Measurements 与 RequestConstraintSet target_sha256 不一致。")
    _validate_interpretation_evidence(interpretation, context)
    _validate_interpretation_catalogs(interpretation, context)
    effective = _effective_constraints(constraint_set)
    _validate_required_layer_completeness(interpretation, effective)
    _validate_contract_constraint(effective, context=context)
    _validate_hard_constraint_sources(effective)
    _validate_constraint_scopes(effective, measurements=measurements)
    hard = tuple(item for item in effective if item.strength == "hard")
    variants: list[IntentIR] = []
    rejections: list[IntentVariantRejection] = []
    for hypothesis in measurements.target_hypotheses:
        reasons = _hypothesis_rejections(
            hypothesis,
            hard,
            contract_id=context.contract_id,
        )
        if reasons:
            rejections.append(
                IntentVariantRejection(
                    target_hypothesis_id=hypothesis.hypothesis_id,
                    target_hypothesis_hash=hypothesis.hypothesis_hash,
                    reason_codes=reasons,
                )
            )
            continue
        variants.append(
            _build_variant(
                measurements,
                interpretation,
                constraint_set,
                context,
                hypothesis,
                effective,
            )
        )
    return IntentBuildResult(
        builder_version=INTENT_BUILDER_VERSION,
        target_sha256=measurements.target_sha256,
        measurements_hash=compute_intent_input_hash(measurements),
        interpretation_hash=compute_intent_input_hash(interpretation),
        build_context_hash=compute_intent_input_hash(context),
        constraint_set_hash=constraint_set.constraint_set_hash,
        source_hypotheses=tuple(
            (item.hypothesis_id, item.hypothesis_hash)
            for item in measurements.target_hypotheses
        ),
        variants=tuple(variants),
        rejections=tuple(rejections),
    )


__all__ = [
    "INTENT_BUILDER_VERSION",
    "build_intent_variants",
    "build_intent_build_context",
    "compute_intent_input_hash",
    "compute_intent_id",
]
