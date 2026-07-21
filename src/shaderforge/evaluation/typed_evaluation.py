"""V2.2 typed Candidate 的约束闭包与基础评估记录。."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field, model_validator

from shaderforge.compiler import CompilationProduct
from shaderforge.contracts import (
    FiniteFloat,
    FrozenModel,
    NonEmptyString,
    Sha256Hex,
    canonical_sha256,
)
from shaderforge.contracts.taxonomy import REQUIRED_LAYER_ORDER, RequiredLayerTaxon
from shaderforge.genome import TypedEffectGenome
from shaderforge.intent.builder import compute_intent_id
from shaderforge.intent.ir import IntentIR, ObjectIntent
from shaderforge.intent.models import (
    ComplexityConstraintValue,
    Constraint,
    ContractConstraintValue,
    HoleCountConstraintValue,
    InstanceCountConstraintValue,
    RequiredLayerConstraintValue,
    TopologyConstraintValue,
)
from shaderforge.store import ArtifactRefV2

from .rendered_structure import (
    RenderedStructureEvidenceV4,
    RenderedStructureVerificationV4,
)

INTENT_CONSTRAINT_EVALUATION_HASH_VERSION = "intent_constraint_evaluation_hash_v2"
INTENT_CONSTRAINT_EVALUATION_HASH_VERSION_V3 = (
    "intent_constraint_evaluation_hash_v3"
)
BASIC_EVALUATION_RECORD_HASH_VERSION = "basic_evaluation_record_hash_v2"


class RequiredLayerCoverageV2(FrozenModel):
    """Intent required layer 到 Genome semantic role 的确定性覆盖结果。."""

    layer: RequiredLayerTaxon
    expected_semantic_role: NonEmptyString
    passed: bool


class HardConstraintResultV2(FrozenModel):
    """单条 hard constraint 的可重算结论。."""

    constraint_id: NonEmptyString
    kind: NonEmptyString
    status: Literal["passed", "failed", "unsupported"]
    reason_code: NonEmptyString


class IntentConstraintEvaluationV2(FrozenModel):
    """Intent/Genome/Compilation hard closure 的完整 typed 记录。."""

    schema_version: Literal["intent_constraint_evaluation_v2"] = (
        "intent_constraint_evaluation_v2"
    )
    hash_version: Literal["intent_constraint_evaluation_hash_v2"] = (
        "intent_constraint_evaluation_hash_v2"
    )
    intent_id: NonEmptyString
    target_sha256: Sha256Hex
    target_hypothesis_id: NonEmptyString
    target_hypothesis_hash: Sha256Hex
    constraint_set_hash: Sha256Hex
    genome_id: NonEmptyString
    semantic_genome_hash: Sha256Hex
    contract_id: NonEmptyString
    required_layers: tuple[RequiredLayerTaxon, ...]
    enabled_semantic_roles: tuple[NonEmptyString, ...]
    required_layer_coverage: tuple[RequiredLayerCoverageV2, ...]
    hard_constraint_results: tuple[HardConstraintResultV2, ...]
    target_structure_status: Literal["solid_single_instance_proven", "unsupported"]
    target_structure_reason_code: NonEmptyString
    hard_constraints_passed: bool
    record_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_closure(self) -> IntentConstraintEvaluationV2:
        expected_layers = tuple(
            layer
            for layer in REQUIRED_LAYER_ORDER
            if layer in set(self.required_layers)
        )
        if self.required_layers != expected_layers:
            raise ValueError("required_layers 必须按 taxonomy 顺序唯一规范化。")
        if self.enabled_semantic_roles != tuple(
            sorted(set(self.enabled_semantic_roles))
        ):
            raise ValueError("enabled_semantic_roles 必须唯一且按字典序规范化。")
        if (
            tuple(item.layer for item in self.required_layer_coverage)
            != self.required_layers
        ):
            raise ValueError("required_layer_coverage 必须精确覆盖 required_layers。")
        ids = [item.constraint_id for item in self.hard_constraint_results]
        if len(ids) != len(set(ids)):
            raise ValueError("hard constraint evaluation id 不得重复。")
        expected_passed = (
            self.target_structure_status == "solid_single_instance_proven"
            and all(item.passed for item in self.required_layer_coverage)
            and all(item.status == "passed" for item in self.hard_constraint_results)
        )
        if self.hard_constraints_passed != expected_passed:
            raise ValueError("hard_constraints_passed 与逐项结论不一致。")
        if self.record_hash != compute_intent_constraint_evaluation_hash(self):
            raise ValueError("Intent constraint evaluation record_hash 不一致。")
        return self


class IntentConstraintEvaluationV3(FrozenModel):
    """显式绑定 RenderedStructure V3 receipt 的 Candidate hard closure。."""

    schema_version: Literal["intent_constraint_evaluation_v3"] = (
        "intent_constraint_evaluation_v3"
    )
    hash_version: Literal["intent_constraint_evaluation_hash_v3"] = (
        "intent_constraint_evaluation_hash_v3"
    )
    candidate_id: NonEmptyString
    intent_id: NonEmptyString
    intent_ref: ArtifactRefV2
    intent_sha256: Sha256Hex
    target_measurements_ref: ArtifactRefV2
    target_measurements_sha256: Sha256Hex
    target_sha256: Sha256Hex
    target_hypothesis_id: NonEmptyString
    target_hypothesis_hash: Sha256Hex
    constraint_set_hash: Sha256Hex
    genome_id: NonEmptyString
    genome_ref: ArtifactRefV2
    genome_sha256: Sha256Hex
    semantic_genome_hash: Sha256Hex
    compilation_ref: ArtifactRefV2
    compilation_sha256: Sha256Hex
    rendered_structure_evidence_ref: ArtifactRefV2
    rendered_structure_evidence_sha256: Sha256Hex
    rendered_structure_verification_ref: ArtifactRefV2
    rendered_structure_verification_sha256: Sha256Hex
    rendered_structure_evidence_record_hash: Sha256Hex
    rendered_structure_verification_record_hash: Sha256Hex
    contract_id: NonEmptyString
    required_layers: tuple[RequiredLayerTaxon, ...]
    enabled_semantic_roles: tuple[NonEmptyString, ...]
    required_layer_coverage: tuple[RequiredLayerCoverageV2, ...]
    hard_constraint_results: tuple[HardConstraintResultV2, ...]
    target_structure_status: Literal["rendered_structure_verified"] = (
        "rendered_structure_verified"
    )
    target_structure_reason_code: Literal[
        "target_structure_rendered_receipt_verified"
    ] = "target_structure_rendered_receipt_verified"
    hard_constraints_passed: Literal[True] = True
    record_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_closure(self) -> IntentConstraintEvaluationV3:
        expected_layers = tuple(
            layer
            for layer in REQUIRED_LAYER_ORDER
            if layer in set(self.required_layers)
        )
        if self.required_layers != expected_layers:
            raise ValueError("V3 required_layers 必须按 taxonomy 顺序唯一规范化。")
        if self.enabled_semantic_roles != tuple(
            sorted(set(self.enabled_semantic_roles))
        ):
            raise ValueError("V3 enabled_semantic_roles 必须唯一且按字典序规范化。")
        if (
            tuple(item.layer for item in self.required_layer_coverage)
            != self.required_layers
            or not all(item.passed for item in self.required_layer_coverage)
        ):
            raise ValueError("V3 required layer receipt 闭包不完整。")
        ids = [item.constraint_id for item in self.hard_constraint_results]
        if len(ids) != len(set(ids)) or not all(
            item.status == "passed" for item in self.hard_constraint_results
        ):
            raise ValueError("V3 hard constraints 必须全部由 receipt 闭合。")
        for ref, expected_hash in (
            (self.intent_ref, self.intent_sha256),
            (self.target_measurements_ref, self.target_measurements_sha256),
            (self.genome_ref, self.genome_sha256),
            (self.compilation_ref, self.compilation_sha256),
            (
                self.rendered_structure_evidence_ref,
                self.rendered_structure_evidence_sha256,
            ),
            (
                self.rendered_structure_verification_ref,
                self.rendered_structure_verification_sha256,
            ),
        ):
            if ref.sha256 != expected_hash:
                raise ValueError("V3 receipt ArtifactRef/hash 绑定不一致。")
        if self.record_hash != compute_intent_constraint_evaluation_hash_v3(self):
            raise ValueError("Intent constraint evaluation V3 record_hash 不一致。")
        return self


class BasicEvaluationRecordV2(FrozenModel):
    """与 Candidate/render 精确绑定的有限数值基础评估。."""

    schema_version: Literal["basic_evaluation_record_v2"] = "basic_evaluation_record_v2"
    hash_version: Literal["basic_evaluation_record_hash_v2"] = (
        "basic_evaluation_record_hash_v2"
    )
    run_id: NonEmptyString
    candidate_id: NonEmptyString
    intent_id: NonEmptyString
    target_hypothesis_hash: Sha256Hex
    genome_id: NonEmptyString
    semantic_genome_hash: Sha256Hex
    compilation_sha256: Sha256Hex
    glsl_sha256: Sha256Hex
    render_ref: ArtifactRefV2
    render_sha256: Sha256Hex
    metric_version: NonEmptyString
    total_loss: FiniteFloat = Field(ge=0.0)
    global_rmse: FiniteFloat = Field(ge=0.0)
    edge_loss: FiniteFloat = Field(ge=0.0)
    geometry_loss: FiniteFloat = Field(ge=0.0)
    alpha_loss: FiniteFloat = Field(ge=0.0)
    diagnostics: tuple[NonEmptyString, ...] = ()
    record_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_record(self) -> BasicEvaluationRecordV2:
        if self.render_ref.sha256 != self.render_sha256:
            raise ValueError("Basic evaluation render ref/hash 不一致。")
        if self.record_hash != compute_basic_evaluation_record_hash(self):
            raise ValueError("Basic evaluation record_hash 不一致。")
        return self


def compute_intent_constraint_evaluation_hash(
    value: IntentConstraintEvaluationV2 | Mapping[str, Any],
) -> str:
    """计算排除自身字段的 typed constraint evaluation hash。."""
    if isinstance(value, IntentConstraintEvaluationV2):
        payload = value.model_dump(mode="python", exclude={"record_hash"})
    else:
        payload = dict(value)
        payload.pop("record_hash", None)
    return canonical_sha256(
        {
            "hash_version": INTENT_CONSTRAINT_EVALUATION_HASH_VERSION,
            "evaluation": payload,
        }
    )


def compute_intent_constraint_evaluation_hash_v3(
    value: IntentConstraintEvaluationV3 | Mapping[str, Any],
) -> str:
    """计算显式 receipt 绑定的 constraint evaluation V3 hash。."""
    if isinstance(value, IntentConstraintEvaluationV3):
        payload = value.model_dump(mode="python", exclude={"record_hash"})
    else:
        payload = dict(value)
        payload.pop("record_hash", None)
    return canonical_sha256(
        {
            "hash_version": INTENT_CONSTRAINT_EVALUATION_HASH_VERSION_V3,
            "evaluation": payload,
        }
    )


def compute_basic_evaluation_record_hash(
    value: BasicEvaluationRecordV2 | Mapping[str, Any],
) -> str:
    """计算排除自身字段的 BasicEvaluation record hash。."""
    if isinstance(value, BasicEvaluationRecordV2):
        payload = value.model_dump(mode="python", exclude={"record_hash"})
    else:
        payload = dict(value)
        payload.pop("record_hash", None)
    return canonical_sha256(
        {
            "hash_version": BASIC_EVALUATION_RECORD_HASH_VERSION,
            "evaluation": payload,
        }
    )


def with_basic_evaluation_record_hash(
    value: BasicEvaluationRecordV2 | Mapping[str, Any],
) -> BasicEvaluationRecordV2:
    """为未物化 hash 的基础评估生成严格记录。."""
    payload = (
        {name: getattr(value, name) for name in BasicEvaluationRecordV2.model_fields}
        if isinstance(value, BasicEvaluationRecordV2)
        else dict(value)
    )
    payload["record_hash"] = compute_basic_evaluation_record_hash(payload)
    return BasicEvaluationRecordV2.model_validate(payload, strict=True)


def _required_layers(intent: IntentIR) -> tuple[RequiredLayerTaxon, ...]:
    required = {layer.role for layer in intent.layers if layer.required}
    return tuple(layer for layer in REQUIRED_LAYER_ORDER if layer in required)


def _object_for_constraint(
    intent: IntentIR,
    constraint: Constraint,
) -> ObjectIntent | None:
    if constraint.scope == "global":
        return intent.objects[0] if len(intent.objects) == 1 else None
    return next(
        (item for item in intent.objects if item.object_id == constraint.scope_ref),
        None,
    )


def _evaluate_hard_constraint(
    intent: IntentIR,
    genome: TypedEffectGenome,
    product: CompilationProduct,
    constraint: Constraint,
    proven_layers: frozenset[RequiredLayerTaxon],
) -> HardConstraintResultV2:
    value = constraint.value
    passed = False
    supported = True
    if isinstance(value, ContractConstraintValue):
        passed = genome.contract_id == value.contract_id == intent.canvas.contract_id
    elif isinstance(value, RequiredLayerConstraintValue):
        passed = value.layer in proven_layers
    elif isinstance(value, TopologyConstraintValue):
        target = _object_for_constraint(intent, constraint)
        if value.topology != "solid" or not _proves_solid_single_instance(genome):
            supported = False
        else:
            passed = target is not None and target.topology == "solid"
    elif isinstance(value, InstanceCountConstraintValue):
        target = _object_for_constraint(intent, constraint)
        if value.exact_count != 1 or not _proves_solid_single_instance(genome):
            supported = False
        else:
            passed = target is not None and target.instance_count == 1
    elif isinstance(value, HoleCountConstraintValue):
        target = _object_for_constraint(intent, constraint)
        if value.exact_count != 0 or not _proves_solid_single_instance(genome):
            supported = False
        else:
            passed = target is not None and target.hole_count == 0
    elif isinstance(value, ComplexityConstraintValue):
        passed = (
            len(genome.nodes) <= value.max_nodes
            and product.estimated_ops <= value.max_estimated_ops
        )
    else:
        supported = False
    if not supported:
        status: Literal["passed", "failed", "unsupported"] = "unsupported"
        reason = f"hard_constraint_{constraint.kind}_requires_external_verifier"
    elif passed:
        status = "passed"
        reason = f"hard_constraint_{constraint.kind}_verified"
    else:
        status = "failed"
        reason = f"hard_constraint_{constraint.kind}_failed"
    return HardConstraintResultV2(
        constraint_id=constraint.constraint_id,
        kind=constraint.kind,
        status=status,
        reason_code=reason,
    )


_LAYER_NODE_KINDS: dict[RequiredLayerTaxon, frozenset[str]] = {
    "background": frozenset({"solid_fill", "linear_gradient"}),
    "shadow": frozenset({"shadow"}),
    "base_fill": frozenset({"solid_fill", "linear_gradient"}),
    "color_lobe": frozenset({"gaussian_color_lobe"}),
    "haze": frozenset({"glow", "gaussian_color_lobe"}),
    "rim": frozenset({"rim_band"}),
    "outline": frozenset({"outline_band"}),
    "highlight": frozenset({"arc_highlight"}),
    "detail": frozenset({"gaussian_color_lobe", "arc_highlight"}),
    "glow": frozenset({"glow"}),
}


def _proven_layer_roles(genome: TypedEffectGenome) -> frozenset[RequiredLayerTaxon]:
    proven: set[RequiredLayerTaxon] = set()
    for layer, allowed_kinds in _LAYER_NODE_KINDS.items():
        role = f"layer_{layer}"
        if any(
            node.semantic_role == role and node.kind in allowed_kinds
            for node in genome.nodes
        ):
            proven.add(layer)
    return frozenset(proven)


def _proves_solid_single_instance(genome: TypedEffectGenome) -> bool:
    """保守证明当前 registry 能表达的单一无孔凸主体。."""
    geometry_kinds = {"circle_sdf", "ellipse_sdf", "rounded_rect_sdf"}
    geometry_nodes = [node for node in genome.nodes if node.kind in geometry_kinds]
    mask_algebra_kinds = {
        "union_mask",
        "intersection_mask",
        "difference_mask",
    }
    return len(geometry_nodes) == 1 and not any(
        node.kind in mask_algebra_kinds for node in genome.nodes
    )


def _target_structure_status(
    intent: IntentIR,
    genome: TypedEffectGenome,
) -> tuple[Literal["solid_single_instance_proven", "unsupported"], str]:
    if (
        len(intent.objects) == 1
        and intent.objects[0].topology == "solid"
        and intent.objects[0].component_count == 1
        and intent.objects[0].instance_count == 1
        and intent.objects[0].hole_count == 0
        and _proves_solid_single_instance(genome)
    ):
        return (
            "solid_single_instance_proven",
            "target_structure_solid_single_instance_verified",
        )
    return "unsupported", "target_structure_requires_typed_topology_receipt"


def evaluate_intent_genome_constraints(
    intent: IntentIR,
    genome: TypedEffectGenome,
    product: CompilationProduct,
) -> IntentConstraintEvaluationV2:
    """不读取持久化结论，独立重算 Intent/Genome hard closure。."""
    if intent.intent_id != compute_intent_id(intent):
        raise ValueError("Intent intent_id 不可重算。")
    if genome.provenance.intent_id != intent.intent_id:
        raise ValueError("Genome provenance intent_id 不一致。")
    if (
        genome.provenance.target_hypothesis_id != intent.target_hypothesis_id
        or genome.provenance.target_hypothesis_hash != intent.target_hypothesis_hash
    ):
        raise ValueError("Genome provenance target identity 不一致。")
    if genome.contract_id != intent.canvas.contract_id:
        raise ValueError("Genome 与 Intent RenderContract 不一致。")
    enabled_roles = tuple(sorted({node.semantic_role for node in genome.nodes}))
    proven_layers = _proven_layer_roles(genome)
    required_layers = _required_layers(intent)
    coverage = tuple(
        RequiredLayerCoverageV2(
            layer=layer,
            expected_semantic_role=f"layer_{layer}",
            passed=layer in proven_layers,
        )
        for layer in required_layers
    )
    hard_results = tuple(
        _evaluate_hard_constraint(
            intent,
            genome,
            product,
            item,
            proven_layers,
        )
        for item in intent.hard_constraints
    )
    structure_status, structure_reason = _target_structure_status(intent, genome)
    payload: dict[str, Any] = {
        "schema_version": "intent_constraint_evaluation_v2",
        "hash_version": INTENT_CONSTRAINT_EVALUATION_HASH_VERSION,
        "intent_id": intent.intent_id,
        "target_sha256": intent.target_sha256,
        "target_hypothesis_id": intent.target_hypothesis_id,
        "target_hypothesis_hash": intent.target_hypothesis_hash,
        "constraint_set_hash": intent.constraint_set_hash,
        "genome_id": genome.genome_id,
        "semantic_genome_hash": product.semantic_genome_hash,
        "contract_id": genome.contract_id,
        "required_layers": required_layers,
        "enabled_semantic_roles": enabled_roles,
        "required_layer_coverage": coverage,
        "hard_constraint_results": hard_results,
        "target_structure_status": structure_status,
        "target_structure_reason_code": structure_reason,
        "hard_constraints_passed": structure_status == "solid_single_instance_proven"
        and all(item.passed for item in coverage)
        and all(item.status == "passed" for item in hard_results),
    }
    payload["record_hash"] = compute_intent_constraint_evaluation_hash(payload)
    return IntentConstraintEvaluationV2.model_validate(payload, strict=True)


def _receipt_hard_constraint_result(
    intent: IntentIR,
    genome: TypedEffectGenome,
    product: CompilationProduct,
    constraint: Constraint,
    proven_layers: frozenset[RequiredLayerTaxon],
    verification: RenderedStructureVerificationV4,
) -> HardConstraintResultV2:
    """只用 exact rendered receipt 闭合 topology/count/hole constraints。."""
    value = constraint.value
    target = _object_for_constraint(intent, constraint)
    if isinstance(value, TopologyConstraintValue):
        passed = (
            target is intent.objects[0]
            and value.topology == target.topology == verification.measured_topology
        )
    elif isinstance(value, InstanceCountConstraintValue):
        passed = (
            target is intent.objects[0]
            and value.exact_count
            == target.instance_count
            == verification.measured_instance_count
        )
    elif isinstance(value, HoleCountConstraintValue):
        passed = (
            target is intent.objects[0]
            and value.exact_count
            == target.hole_count
            == verification.measured_hole_count
        )
    else:
        static = _evaluate_hard_constraint(
            intent,
            genome,
            product,
            constraint,
            proven_layers,
        )
        if isinstance(value, RequiredLayerConstraintValue):
            receipt_by_layer = {
                item.layer: item for item in verification.layer_contribution_results
            }
            row = receipt_by_layer.get(value.layer)
            passed = bool(
                static.status == "passed"
                and row is not None
                and row.required_by_intent
                and row.predicted_visible
            )
        else:
            return static
    return HardConstraintResultV2(
        constraint_id=constraint.constraint_id,
        kind=constraint.kind,
        status="passed" if passed else "failed",
        reason_code=(
            f"hard_constraint_{constraint.kind}_rendered_receipt_verified"
            if passed
            else f"hard_constraint_{constraint.kind}_rendered_receipt_failed"
        ),
    )


def _require_exact_rendered_structure_receipt(
    *,
    candidate_id: str,
    target_measurements_ref: ArtifactRefV2,
    intent: IntentIR,
    intent_ref: ArtifactRefV2,
    genome: TypedEffectGenome,
    genome_ref: ArtifactRefV2,
    product: CompilationProduct,
    compilation_ref: ArtifactRefV2,
    evidence: RenderedStructureEvidenceV4,
    verification: RenderedStructureVerificationV4,
) -> ArtifactRefV2:
    if (
        target_measurements_ref.kind != "target_measurements"
        or target_measurements_ref.schema_version != "target_measurements_v2_2"
        or target_measurements_ref.content_type != "application/json"
    ):
        raise ValueError("Evaluation V3 target measurements ref schema 不正确。")
    subject = intent.objects[0] if len(intent.objects) == 1 else None
    expected_instances = () if subject is None else subject.instances
    instance_results = verification.instance_structure_results
    layer_results = {
        item.layer: item for item in verification.layer_contribution_results
    }
    required_layers = set(_required_layers(intent))
    if (
        subject is None
        or evidence.candidate_id != candidate_id
        or verification.candidate_id != candidate_id
        or evidence.intent_id != intent.intent_id
        or evidence.intent_ref != intent_ref
        or evidence.intent_sha256 != intent_ref.sha256
        or evidence.target_hypothesis_id != intent.target_hypothesis_id
        or evidence.target_hypothesis_hash != intent.target_hypothesis_hash
        or evidence.genome_id != genome.genome_id
        or evidence.genome_ref != genome_ref
        or evidence.genome_sha256 != genome_ref.sha256
        or evidence.semantic_genome_hash != product.semantic_genome_hash
        or evidence.compilation_ref != compilation_ref
        or evidence.compilation_sha256 != compilation_ref.sha256
        or verification.evidence_record_hash != evidence.record_hash
        or verification.status != "structure_verified"
        or verification.reason_codes
        or verification.measured_topology != subject.topology
        or verification.measured_instance_count != subject.instance_count
        or verification.measured_component_count != subject.component_count
        or verification.measured_hole_count != subject.hole_count
        or len(instance_results) != len(expected_instances)
        or any(
            not result.passed
            or result.instance_index != index
            or result.instance_id != expected.instance_id
            or result.expected_topology != expected.fill_topology
            or result.measured_topology != expected.fill_topology
            or result.expected_component_count != expected.component_count
            or result.measured_component_count != expected.component_count
            or result.expected_hole_count != expected.hole_count
            or result.measured_hole_count != expected.hole_count
            for index, (expected, result) in enumerate(
                zip(expected_instances, instance_results, strict=True)
            )
        )
        or tuple(layer_results) != REQUIRED_LAYER_ORDER
        or any(
            layer not in layer_results
            or not layer_results[layer].required_by_intent
            or not layer_results[layer].predicted_visible
            for layer in required_layers
        )
        or any(not item.passed for item in verification.instance_relation_results)
    ):
        raise ValueError("Evaluation V3 rendered structure receipt 未 exact 闭合。")
    return target_measurements_ref


def evaluate_intent_genome_constraints_v3(
    intent: IntentIR,
    genome: TypedEffectGenome,
    product: CompilationProduct,
    *,
    candidate_id: str,
    target_measurements_ref: ArtifactRefV2,
    intent_ref: ArtifactRefV2,
    genome_ref: ArtifactRefV2,
    compilation_ref: ArtifactRefV2,
    rendered_structure_evidence_ref: ArtifactRefV2,
    rendered_structure_evidence: RenderedStructureEvidenceV4,
    rendered_structure_verification_ref: ArtifactRefV2,
    rendered_structure_verification: RenderedStructureVerificationV4,
) -> IntentConstraintEvaluationV3:
    """由 EvidenceV4/VerificationV4 生成 breaking Evaluation V3。."""
    if intent.intent_id != compute_intent_id(intent):
        raise ValueError("Intent intent_id 不可重算。")
    if genome.provenance.intent_id != intent.intent_id:
        raise ValueError("Genome provenance intent_id 不一致。")
    if (
        genome.provenance.target_hypothesis_id != intent.target_hypothesis_id
        or genome.provenance.target_hypothesis_hash != intent.target_hypothesis_hash
    ):
        raise ValueError("Genome provenance target identity 不一致。")
    if genome.contract_id != intent.canvas.contract_id:
        raise ValueError("Genome 与 Intent RenderContract 不一致。")
    if (
        rendered_structure_evidence_ref.kind != "rendered_structure_evidence"
        or rendered_structure_evidence_ref.schema_version
        != "rendered_structure_evidence_v4"
        or rendered_structure_verification_ref.kind
        != "rendered_structure_verification"
        or rendered_structure_verification_ref.schema_version
        != "rendered_structure_verification_v4"
    ):
        raise ValueError("Evaluation V3 receipt ArtifactRef schema 不正确。")
    target_measurements_ref = _require_exact_rendered_structure_receipt(
        candidate_id=candidate_id,
        target_measurements_ref=target_measurements_ref,
        intent=intent,
        intent_ref=intent_ref,
        genome=genome,
        genome_ref=genome_ref,
        product=product,
        compilation_ref=compilation_ref,
        evidence=rendered_structure_evidence,
        verification=rendered_structure_verification,
    )
    enabled_roles = tuple(sorted({node.semantic_role for node in genome.nodes}))
    proven_layers = _proven_layer_roles(genome)
    required_layers = _required_layers(intent)
    coverage = tuple(
        RequiredLayerCoverageV2(
            layer=layer,
            expected_semantic_role=f"layer_{layer}",
            passed=True,
        )
        for layer in required_layers
    )
    hard_results = tuple(
        _receipt_hard_constraint_result(
            intent,
            genome,
            product,
            item,
            proven_layers,
            rendered_structure_verification,
        )
        for item in intent.hard_constraints
    )
    if not all(item.status == "passed" for item in hard_results):
        raise ValueError("Evaluation V3 hard constraints 未被 receipt 全部闭合。")
    payload: dict[str, Any] = {
        "schema_version": "intent_constraint_evaluation_v3",
        "hash_version": INTENT_CONSTRAINT_EVALUATION_HASH_VERSION_V3,
        "candidate_id": candidate_id,
        "intent_id": intent.intent_id,
        "intent_ref": intent_ref,
        "intent_sha256": intent_ref.sha256,
        "target_measurements_ref": target_measurements_ref,
        "target_measurements_sha256": target_measurements_ref.sha256,
        "target_sha256": intent.target_sha256,
        "target_hypothesis_id": intent.target_hypothesis_id,
        "target_hypothesis_hash": intent.target_hypothesis_hash,
        "constraint_set_hash": intent.constraint_set_hash,
        "genome_id": genome.genome_id,
        "genome_ref": genome_ref,
        "genome_sha256": genome_ref.sha256,
        "semantic_genome_hash": product.semantic_genome_hash,
        "compilation_ref": compilation_ref,
        "compilation_sha256": compilation_ref.sha256,
        "rendered_structure_evidence_ref": rendered_structure_evidence_ref,
        "rendered_structure_evidence_sha256": rendered_structure_evidence_ref.sha256,
        "rendered_structure_verification_ref": rendered_structure_verification_ref,
        "rendered_structure_verification_sha256": (
            rendered_structure_verification_ref.sha256
        ),
        "rendered_structure_evidence_record_hash": (
            rendered_structure_evidence.record_hash
        ),
        "rendered_structure_verification_record_hash": (
            rendered_structure_verification.record_hash
        ),
        "contract_id": genome.contract_id,
        "required_layers": required_layers,
        "enabled_semantic_roles": enabled_roles,
        "required_layer_coverage": coverage,
        "hard_constraint_results": hard_results,
        "target_structure_status": "rendered_structure_verified",
        "target_structure_reason_code": (
            "target_structure_rendered_receipt_verified"
        ),
        "hard_constraints_passed": True,
    }
    payload["record_hash"] = compute_intent_constraint_evaluation_hash_v3(payload)
    return IntentConstraintEvaluationV3.model_validate(payload, strict=True)


__all__ = [
    "BASIC_EVALUATION_RECORD_HASH_VERSION",
    "INTENT_CONSTRAINT_EVALUATION_HASH_VERSION",
    "INTENT_CONSTRAINT_EVALUATION_HASH_VERSION_V3",
    "BasicEvaluationRecordV2",
    "HardConstraintResultV2",
    "IntentConstraintEvaluationV2",
    "IntentConstraintEvaluationV3",
    "RequiredLayerCoverageV2",
    "compute_basic_evaluation_record_hash",
    "compute_intent_constraint_evaluation_hash",
    "compute_intent_constraint_evaluation_hash_v3",
    "evaluate_intent_genome_constraints",
    "evaluate_intent_genome_constraints_v3",
    "with_basic_evaluation_record_hash",
]
