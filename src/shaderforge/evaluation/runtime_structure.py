"""V2.1 runtime Target structure evidence 的确定性验证器。."""

from __future__ import annotations

import math
from collections import deque
from hashlib import sha256
from io import BytesIO
from typing import Literal

import numpy as np
from PIL import Image, UnidentifiedImageError
from pydantic import model_validator

from shaderforge.analysis import (
    BBoxUv,
    TargetMeasurementsV2,
    classify_instance_mask_topology_v2,
    normalize_target_png,
    verify_radial_segment_structure_evidence_v1,
)
from shaderforge.analysis.measurements import MAX_IMAGE_PIXELS, InvalidTargetImageError
from shaderforge.contracts import FrozenModel, NonEmptyString, Sha256Hex
from shaderforge.contracts.canonical import canonical_sha256
from shaderforge.contracts.taxonomy import REQUIRED_LAYER_ORDER, RequiredLayerTaxon
from shaderforge.evaluation.admission import TargetStructureFacts
from shaderforge.intent.constraints_builder import (
    validate_request_constraint_set_policy,
)
from shaderforge.intent.interpretation_artifacts import load_visual_interpretation_call
from shaderforge.intent.ir import IntentBuildContext, IntentIR
from shaderforge.intent.models import (
    RequestConstraintSet,
    RequiredLayerConstraintValue,
)
from shaderforge.intent.validation import validate_intent_ir
from shaderforge.store import ArtifactRefV2, ArtifactResolver

RUNTIME_REQUIRED_LAYER_MASK_SCHEMA_VERSION: Literal[
    "runtime_required_layer_mask_v1"
] = "runtime_required_layer_mask_v1"
RUNTIME_TARGET_STRUCTURE_EVIDENCE_SCHEMA_VERSION: Literal[
    "runtime_target_structure_evidence_v2"
] = "runtime_target_structure_evidence_v2"
RUNTIME_TARGET_STRUCTURE_VERIFICATION_SCHEMA_VERSION: Literal[
    "runtime_target_structure_verification_v2"
] = "runtime_target_structure_verification_v2"
RUNTIME_TARGET_STRUCTURE_VERIFIER_VERSION: Literal[
    "runtime_target_structure_verifier_v2"
] = "runtime_target_structure_verifier_v2"

RuntimeRequiredLayer = RequiredLayerTaxon
VerificationStatus = Literal["structure_verified", "rejected"]


class RuntimeRequiredLayerMask(FrozenModel):
    """一个 required layer 的 runtime 二值 mask 证据。."""

    schema_version: Literal["runtime_required_layer_mask_v1"] = (
        RUNTIME_REQUIRED_LAYER_MASK_SCHEMA_VERSION
    )
    layer: RuntimeRequiredLayer
    mask_ref: ArtifactRefV2


class RuntimeTargetStructureEvidence(FrozenModel):
    """绑定 MeasurementsV2、runtime mask 与 required-layer mask 的输入证据。."""

    schema_version: Literal["runtime_target_structure_evidence_v2"] = (
        RUNTIME_TARGET_STRUCTURE_EVIDENCE_SCHEMA_VERSION
    )
    verifier_version: Literal["runtime_target_structure_verifier_v2"] = (
        RUNTIME_TARGET_STRUCTURE_VERIFIER_VERSION
    )
    target_source_ref: ArtifactRefV2
    target_source_sha256: Sha256Hex
    normalized_reference_ref: ArtifactRefV2
    measurements_ref: ArtifactRefV2
    interpretation_audit_ref: ArtifactRefV2
    constraint_set_ref: ArtifactRefV2
    intent_build_context_ref: ArtifactRefV2
    intent_ref: ArtifactRefV2
    target_hypothesis_id: NonEmptyString
    target_hypothesis_hash: Sha256Hex
    subject_mask_ref: ArtifactRefV2
    instance_mask_refs: tuple[ArtifactRefV2, ...]
    required_layer_masks: tuple[RuntimeRequiredLayerMask, ...]

    @model_validator(mode="after")
    def _validate_evidence_set(self) -> RuntimeTargetStructureEvidence:
        if not self.instance_mask_refs:
            raise ValueError("instance_mask_refs 不能为空。")
        instance_hashes = [item.sha256 for item in self.instance_mask_refs]
        if len(instance_hashes) != len(set(instance_hashes)):
            raise ValueError("instance_mask_refs 不得包含重复内容。")
        layers = [item.layer for item in self.required_layer_masks]
        if not layers or "base_fill" not in layers:
            raise ValueError("runtime required-layer evidence 必须包含 base_fill。")
        if len(layers) != len(set(layers)):
            raise ValueError("runtime required-layer evidence 的 layer 不得重复。")
        layer_hashes = [item.mask_ref.sha256 for item in self.required_layer_masks]
        if len(layer_hashes) != len(set(layer_hashes)):
            raise ValueError("不同 required layer 不得复用同一 mask 内容。")
        return self


class RuntimeTargetStructureVerification(FrozenModel):
    """runtime Target structure verifier 的可持久化结论。."""

    schema_version: Literal["runtime_target_structure_verification_v2"] = (
        RUNTIME_TARGET_STRUCTURE_VERIFICATION_SCHEMA_VERSION
    )
    verifier_version: Literal["runtime_target_structure_verifier_v2"] = (
        RUNTIME_TARGET_STRUCTURE_VERIFIER_VERSION
    )
    status: VerificationStatus
    evidence_sha256: Sha256Hex
    target_source_sha256: Sha256Hex
    target_hypothesis_id: NonEmptyString
    target_hypothesis_hash: Sha256Hex
    target: TargetStructureFacts | None
    computed_component_count: int | None = None
    computed_hole_count: int | None = None
    reason_codes: tuple[NonEmptyString, ...]

    @model_validator(mode="after")
    def _validate_result(self) -> RuntimeTargetStructureVerification:
        if not self.reason_codes:
            raise ValueError("runtime structure verification reason_codes 不能为空。")
        if self.status == "structure_verified":
            if self.target is None:
                raise ValueError("structure_verified 结论必须携带 target facts。")
            if (
                self.computed_component_count is None
                or self.computed_hole_count is None
            ):
                raise ValueError("structure_verified 结论必须包含重算结构计数。")
            if self.reason_codes != (
                "runtime_target_structure_and_required_layers_verified",
            ):
                raise ValueError("structure_verified 结论 reason_codes 不一致。")
        elif self.target is not None:
            raise ValueError("rejected 结论不得携带可用于 admission 的 target facts。")
        return self


class _VerificationFailure(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _read_ref(resolver: ArtifactResolver, expected: ArtifactRefV2) -> bytes:
    try:
        resolved = resolver.resolve(expected.artifact_id)
        if resolved != expected:
            raise _VerificationFailure("artifact_ref_identity_mismatch")
        data = resolver.read_bytes(expected.artifact_id)
    except _VerificationFailure:
        raise
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise _VerificationFailure("artifact_unavailable_or_invalid") from exc
    if len(data) != expected.size_bytes:
        raise _VerificationFailure("artifact_size_mismatch")
    if sha256(data).hexdigest() != expected.sha256:
        raise _VerificationFailure("artifact_sha256_mismatch")
    return data


def _require_ref_contract(
    ref: ArtifactRefV2,
    *,
    kinds: tuple[str, ...],
    schema_version: str,
    content_types: tuple[str, ...],
) -> None:
    if ref.kind not in kinds:
        raise _VerificationFailure("artifact_kind_mismatch")
    if ref.schema_version != schema_version:
        raise _VerificationFailure("artifact_schema_version_mismatch")
    if ref.content_type not in content_types:
        raise _VerificationFailure("artifact_content_type_mismatch")


def _decode_reference_size(data: bytes) -> tuple[int, int]:
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG":
                raise _VerificationFailure("normalized_reference_not_png")
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise _VerificationFailure("image_pixel_limit_exceeded")
            image.load()
            return image.size
    except (UnidentifiedImageError, OSError) as exc:
        raise _VerificationFailure("normalized_reference_invalid") from exc


def _decode_binary_mask(
    data: bytes, expected_size: tuple[int, int]
) -> tuple[bool, ...]:
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG":
                raise _VerificationFailure("mask_not_png")
            image.load()
            if image.size != expected_size:
                raise _VerificationFailure("mask_size_mismatch")
            if image.mode not in {"1", "L"}:
                raise _VerificationFailure("mask_mode_not_binary")
            values = tuple(image.convert("L").tobytes())
    except _VerificationFailure:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise _VerificationFailure("mask_png_invalid") from exc
    if not values or any(value not in {0, 255} for value in values):
        raise _VerificationFailure("mask_pixels_not_binary")
    return tuple(value == 255 for value in values)


def _neighbors(index: int, width: int, height: int) -> tuple[int, ...]:
    x = index % width
    y = index // width
    result: list[int] = []
    if x > 0:
        result.append(index - 1)
    if x + 1 < width:
        result.append(index + 1)
    if y > 0:
        result.append(index - width)
    if y + 1 < height:
        result.append(index + width)
    return tuple(result)


def _component_count(mask: tuple[bool, ...], width: int, height: int) -> int:
    unseen = {index for index, active in enumerate(mask) if active}
    count = 0
    while unseen:
        count += 1
        queue = deque((unseen.pop(),))
        while queue:
            index = queue.popleft()
            for neighbor in _neighbors(index, width, height):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    queue.append(neighbor)
    return count


def _hole_count(mask: tuple[bool, ...], width: int, height: int) -> int:
    background = {index for index, active in enumerate(mask) if not active}
    exterior = {
        index
        for index in background
        if index % width in {0, width - 1} or index // width in {0, height - 1}
    }
    queue = deque(exterior)
    background.difference_update(exterior)
    while queue:
        index = queue.popleft()
        for neighbor in _neighbors(index, width, height):
            if neighbor in background:
                background.remove(neighbor)
                queue.append(neighbor)
    holes = 0
    while background:
        holes += 1
        queue = deque((background.pop(),))
        while queue:
            index = queue.popleft()
            for neighbor in _neighbors(index, width, height):
                if neighbor in background:
                    background.remove(neighbor)
                    queue.append(neighbor)
    return holes


def _remeasure_instance_geometry(
    mask: tuple[bool, ...], width: int, height: int
) -> tuple[BBoxUv, tuple[float, float], tuple[float, float], float, float]:
    """按 Measurements producer 的冻结算法重测逐实例几何。."""
    array = np.asarray(mask, dtype=bool).reshape((height, width))
    points = np.argwhere(array).astype(np.float64)
    if not len(points):
        raise _VerificationFailure("instance_mask_empty")
    min_y, min_x = points.min(axis=0)
    max_y, max_x = points.max(axis=0)
    bbox = BBoxUv(
        min_x=float(min_x / width),
        min_y=float(1.0 - (max_y + 1) / height),
        max_x=float((max_x + 1) / width),
        max_y=float(1.0 - min_y / height),
    )
    center = (
        float(np.mean((points[:, 1] + 0.5) / width)),
        float(np.mean(1.0 - (points[:, 0] + 0.5) / height)),
    )
    uv = np.column_stack(
        (
            (points[:, 1] + 0.5) / width,
            1.0 - (points[:, 0] + 0.5) / height,
        )
    )
    if len(uv) > 1:
        covariance = np.cov(uv, rowvar=False, bias=True)
        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
        order = np.argsort(eigenvalues)[::-1]
        major = eigenvectors[:, order[0]]
        orientation = float(math.atan2(float(major[1]), float(major[0])))
    else:
        orientation = 0.0
    axes = (
        max((bbox.max_x - bbox.min_x) * 0.5, 0.5 / width),
        max((bbox.max_y - bbox.min_y) * 0.5, 0.5 / height),
    )
    return bbox, center, axes, orientation, float(array.mean())


def _rejected(
    evidence: RuntimeTargetStructureEvidence,
    code: str,
    *,
    component_count: int | None = None,
    hole_count: int | None = None,
) -> RuntimeTargetStructureVerification:
    return RuntimeTargetStructureVerification(
        status="rejected",
        evidence_sha256=canonical_sha256(evidence),
        target_source_sha256=evidence.target_source_sha256,
        target_hypothesis_id=evidence.target_hypothesis_id,
        target_hypothesis_hash=evidence.target_hypothesis_hash,
        target=None,
        computed_component_count=component_count,
        computed_hole_count=hole_count,
        reason_codes=(code,),
    )


def _effective_required_layers(constraint_set: RequestConstraintSet) -> set[str]:
    excluded: set[str] = set()
    for conflict in constraint_set.conflicts:
        if conflict.status == "resolved":
            excluded.update(
                constraint_id
                for constraint_id in conflict.constraint_ids
                if constraint_id != conflict.selected_constraint_id
            )
    required = {"base_fill"}
    for constraint in constraint_set.constraints:
        if (
            constraint.constraint_id not in excluded
            and constraint.verification_status != "rejected"
            and constraint.strength == "hard"
            and isinstance(constraint.value, RequiredLayerConstraintValue)
        ):
            if not constraint.evidence_refs:
                raise _VerificationFailure("required_layer_constraint_evidence_missing")
            required.add(constraint.value.layer)
    return required


def _load_required_layer_inputs(
    evidence: RuntimeTargetStructureEvidence,
    *,
    resolver: ArtifactResolver,
    measurements: TargetMeasurementsV2,
) -> tuple[RequiredLayerTaxon, ...]:
    try:
        interpretation_bundle = load_visual_interpretation_call(
            evidence.interpretation_audit_ref,
            resolver=resolver,
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise _VerificationFailure("visual_interpretation_audit_invalid") from exc
    interpretation = interpretation_bundle.interpretation
    if interpretation is None:
        raise _VerificationFailure("visual_interpretation_not_succeeded")
    if any(
        item.status == "unknown" for item in interpretation.required_layer_assessments
    ):
        raise _VerificationFailure("required_layer_assessment_unknown")
    assessed = {
        item.layer
        for item in interpretation.required_layer_assessments
        if item.status == "required"
    }
    assessed_not_required = {
        item.layer
        for item in interpretation.required_layer_assessments
        if item.status == "not_required"
    }
    audit_inputs = set(interpretation_bundle.audit.input_artifact_refs)
    if (
        not {evidence.normalized_reference_ref, evidence.measurements_ref}
        <= audit_inputs
    ):
        raise _VerificationFailure("visual_interpretation_inputs_incomplete")

    _require_ref_contract(
        evidence.constraint_set_ref,
        kinds=("request_constraint_set",),
        schema_version="request_constraint_set_v1",
        content_types=("application/json",),
    )
    try:
        constraint_set = RequestConstraintSet.model_validate_json(
            _read_ref(resolver, evidence.constraint_set_ref),
            strict=True,
        )
        validate_request_constraint_set_policy(constraint_set)
    except _VerificationFailure:
        raise
    except ValueError as exc:
        raise _VerificationFailure("request_constraint_set_invalid") from exc
    if constraint_set.target_sha256 != evidence.target_source_sha256:
        raise _VerificationFailure("constraint_set_target_identity_mismatch")
    for ref in (
        *constraint_set.evidence_refs,
        *(
            ref
            for constraint in constraint_set.constraints
            for ref in constraint.evidence_refs
        ),
    ):
        _read_ref(resolver, ref)
    constrained = _effective_required_layers(constraint_set)
    if constrained & assessed_not_required:
        raise _VerificationFailure("required_layer_constraint_assessment_conflict")
    expected_required = assessed | constrained

    _require_ref_contract(
        evidence.intent_build_context_ref,
        kinds=("intent_build_context",),
        schema_version="intent_build_context_v1",
        content_types=("application/json",),
    )
    _require_ref_contract(
        evidence.intent_ref,
        kinds=("intent_ir",),
        schema_version="intent_v3",
        content_types=("application/json",),
    )
    try:
        context = IntentBuildContext.model_validate_json(
            _read_ref(resolver, evidence.intent_build_context_ref),
            strict=True,
        )
        intent = IntentIR.model_validate_json(
            _read_ref(resolver, evidence.intent_ref),
            strict=True,
        )
        validate_intent_ir(
            intent,
            measurements=measurements,
            interpretation=interpretation,
            constraint_set=constraint_set,
            context=context,
        )
    except _VerificationFailure:
        raise
    except ValueError as exc:
        raise _VerificationFailure("intent_rebuild_validation_failed") from exc
    if (
        intent.target_hypothesis_id != evidence.target_hypothesis_id
        or intent.target_hypothesis_hash != evidence.target_hypothesis_hash
    ):
        raise _VerificationFailure("intent_target_hypothesis_mismatch")

    intent_required = {item.role for item in intent.layers if item.required}
    if expected_required != intent_required:
        raise _VerificationFailure("required_layer_intent_set_mismatch")
    ordered = tuple(
        layer for layer in REQUIRED_LAYER_ORDER if layer in expected_required
    )
    return ordered


def verify_runtime_target_structure(
    evidence: RuntimeTargetStructureEvidence,
    *,
    resolver: ArtifactResolver,
) -> RuntimeTargetStructureVerification:
    """从 runtime Artifact 重算结构事实；任一缺口都不产生 admission facts。."""
    try:
        _require_ref_contract(
            evidence.target_source_ref,
            kinds=("target_source",),
            schema_version="target_source_v1",
            content_types=("image/png", "image/jpeg", "image/webp"),
        )
        source_bytes = _read_ref(resolver, evidence.target_source_ref)
        if sha256(source_bytes).hexdigest() != evidence.target_source_sha256:
            raise _VerificationFailure("target_source_identity_mismatch")
        _require_ref_contract(
            evidence.normalized_reference_ref,
            kinds=("normalized_reference",),
            schema_version="normalized_target_png_v1",
            content_types=("image/png",),
        )
        normalized_bytes = _read_ref(resolver, evidence.normalized_reference_ref)
        try:
            expected_normalized = normalize_target_png(source_bytes)
        except InvalidTargetImageError as exc:
            raise _VerificationFailure("target_source_invalid") from exc
        if normalized_bytes != expected_normalized:
            raise _VerificationFailure("normalized_reference_derivation_mismatch")

        _require_ref_contract(
            evidence.measurements_ref,
            kinds=("target_measurements",),
            schema_version="target_measurements_v2_2",
            content_types=("application/json",),
        )
        measurements_bytes = _read_ref(resolver, evidence.measurements_ref)
        try:
            measurements = TargetMeasurementsV2.model_validate_json(
                measurements_bytes,
                strict=True,
            )
        except ValueError as exc:
            raise _VerificationFailure("measurements_artifact_invalid") from exc
        if measurements.target_sha256 != evidence.target_source_sha256:
            raise _VerificationFailure("target_source_identity_mismatch")

        image_size = _decode_reference_size(normalized_bytes)
        if image_size != measurements.image_size:
            raise _VerificationFailure("normalized_reference_size_mismatch")

        hypothesis = next(
            (
                item
                for item in measurements.target_hypotheses
                if item.hypothesis_id == evidence.target_hypothesis_id
            ),
            None,
        )
        if (
            hypothesis is None
            or hypothesis.hypothesis_hash != evidence.target_hypothesis_hash
        ):
            raise _VerificationFailure("target_hypothesis_identity_mismatch")
        if hypothesis.subject_mask_ref != evidence.subject_mask_ref:
            raise _VerificationFailure("subject_mask_ref_mismatch")
        if hypothesis.instance_mask_refs != evidence.instance_mask_refs:
            raise _VerificationFailure("instance_mask_refs_mismatch")
        if hypothesis.radial_segment_evidence_ref is not None:
            try:
                segment_evidence = verify_radial_segment_structure_evidence_v1(
                    hypothesis.radial_segment_evidence_ref,
                    resolver=resolver,
                )
            except (FileNotFoundError, OSError, TypeError, ValueError) as exc:
                raise _VerificationFailure(
                    "radial_segment_evidence_invalid"
                ) from exc
            if (
                segment_evidence.target_sha256 != evidence.target_source_sha256
                or segment_evidence.target_source_ref != evidence.target_source_ref
                or segment_evidence.semantic_subject_mask_ref
                != evidence.subject_mask_ref
                or tuple(
                    item.ownership_mask_ref for item in segment_evidence.segments
                )
                != evidence.instance_mask_refs
            ):
                raise _VerificationFailure("radial_segment_evidence_binding_mismatch")
        _require_ref_contract(
            evidence.subject_mask_ref,
            kinds=("subject_mask",),
            schema_version="binary_mask_v1",
            content_types=("image/png",),
        )
        subject = _decode_binary_mask(
            _read_ref(resolver, evidence.subject_mask_ref),
            image_size,
        )
        if not any(subject):
            raise _VerificationFailure("subject_mask_empty")
        width, height = image_size
        component_count = _component_count(subject, width, height)
        hole_count = _hole_count(subject, width, height)
        if component_count != hypothesis.component_count:
            return _rejected(
                evidence,
                "component_count_mismatch",
                component_count=component_count,
                hole_count=hole_count,
            )
        if hole_count != hypothesis.hole_count:
            return _rejected(
                evidence,
                "hole_count_mismatch",
                component_count=component_count,
                hole_count=hole_count,
            )
        if hypothesis.fill_topology == "solid" and hole_count != 0:
            return _rejected(evidence, "solid_topology_has_holes")
        if hypothesis.fill_topology in {"ring", "hollow"} and hole_count == 0:
            return _rejected(evidence, "hollow_topology_has_no_holes")

        instance_masks_list: list[tuple[bool, ...]] = []
        for ref in evidence.instance_mask_refs:
            _require_ref_contract(
                ref,
                kinds=("subject_mask",)
                if ref == evidence.subject_mask_ref
                else ("instance_mask",),
                schema_version="binary_mask_v1",
                content_types=("image/png",),
            )
            instance_masks_list.append(
                _decode_binary_mask(_read_ref(resolver, ref), image_size)
            )
        instance_masks = tuple(instance_masks_list)
        if len(instance_masks) != hypothesis.instance_count:
            raise _VerificationFailure("instance_count_mismatch")
        if any(not any(mask) for mask in instance_masks):
            raise _VerificationFailure("instance_mask_empty")
        if any(_component_count(mask, width, height) != 1 for mask in instance_masks):
            raise _VerificationFailure("instance_mask_not_connected")
        if any(
            active and not subject[index]
            for mask in instance_masks
            for index, active in enumerate(mask)
        ):
            raise _VerificationFailure("instance_mask_outside_subject")
        if any(
            sum(mask[index] for mask in instance_masks) > 1
            for index in range(len(subject))
        ):
            raise _VerificationFailure("instance_masks_overlap")
        instance_union = tuple(
            any(mask[index] for mask in instance_masks) for index in range(len(subject))
        )
        if instance_union != subject:
            raise _VerificationFailure("instance_masks_do_not_cover_subject")
        for geometry, mask in zip(
            hypothesis.instance_geometries, instance_masks, strict=True
        ):
            bbox, center, axes, orientation, area_ratio = _remeasure_instance_geometry(
                mask, width, height
            )
            if (
                geometry.mask_ref
                != evidence.instance_mask_refs[geometry.instance_index]
                or geometry.bbox_uv != bbox
                or geometry.center_uv != center
                or geometry.axes_uv != axes
                or geometry.orientation_rad != orientation
                or geometry.area_ratio != area_ratio
                or geometry.component_count != _component_count(mask, width, height)
                or geometry.hole_count != _hole_count(mask, width, height)
                or geometry.fill_topology
                != classify_instance_mask_topology_v2(
                    mask,
                    width=width,
                    height=height,
                )
            ):
                raise _VerificationFailure("instance_geometry_remeasurement_mismatch")

        required_layers = _load_required_layer_inputs(
            evidence,
            resolver=resolver,
            measurements=measurements,
        )
        layer_masks: dict[str, tuple[bool, ...]] = {}
        for item in evidence.required_layer_masks:
            _require_ref_contract(
                item.mask_ref,
                kinds=("subject_mask",)
                if item.layer == "base_fill"
                else ("required_layer_mask",),
                schema_version="binary_mask_v1",
                content_types=("image/png",),
            )
            layer_mask = _decode_binary_mask(
                _read_ref(resolver, item.mask_ref),
                image_size,
            )
            if not any(layer_mask):
                raise _VerificationFailure("required_layer_mask_empty")
            layer_masks[item.layer] = layer_mask
        if layer_masks["base_fill"] != subject:
            raise _VerificationFailure("base_fill_mask_must_match_subject")
        if set(layer_masks) != set(required_layers):
            raise _VerificationFailure("required_layer_mask_set_mismatch")
    except _VerificationFailure as exc:
        return _rejected(evidence, exc.code)

    return RuntimeTargetStructureVerification(
        status="structure_verified",
        evidence_sha256=canonical_sha256(evidence),
        target_source_sha256=evidence.target_source_sha256,
        target_hypothesis_id=evidence.target_hypothesis_id,
        target_hypothesis_hash=evidence.target_hypothesis_hash,
        target=TargetStructureFacts(
            topology=hypothesis.fill_topology,
            instance_count=hypothesis.instance_count,
            hole_count=hole_count,
            required_layers=required_layers,
        ),
        computed_component_count=component_count,
        computed_hole_count=hole_count,
        reason_codes=("runtime_target_structure_and_required_layers_verified",),
    )


__all__ = [
    "RUNTIME_REQUIRED_LAYER_MASK_SCHEMA_VERSION",
    "RUNTIME_TARGET_STRUCTURE_EVIDENCE_SCHEMA_VERSION",
    "RUNTIME_TARGET_STRUCTURE_VERIFICATION_SCHEMA_VERSION",
    "RUNTIME_TARGET_STRUCTURE_VERIFIER_VERSION",
    "RuntimeRequiredLayerMask",
    "RuntimeTargetStructureEvidence",
    "RuntimeTargetStructureVerification",
    "verify_runtime_target_structure",
]
