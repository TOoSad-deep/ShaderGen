from __future__ import annotations

import json
import math
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from shaderforge.analysis import (
    BBoxUv,
    InstanceGeometryV2,
    LabSample,
    RegionStatistics,
    SymmetryEvidence,
    TargetHypothesis,
    TargetMeasurementsV2,
    compute_target_hypothesis_hash,
    normalize_target_png,
)
from shaderforge.contracts.png_to_shader_v1 import WEBGL1_STATIC_NO_TEXTURE_V1
from shaderforge.contracts.taxonomy import REQUIRED_LAYER_ORDER
from shaderforge.evaluation import (
    RuntimeRequiredLayerMask,
    RuntimeTargetStructureEvidence,
    verify_runtime_target_structure,
)
from shaderforge.intent import (
    Constraint,
    ContractConstraintValue,
    IntentBuildContext,
    LayerHypothesis,
    PrimitiveCandidate,
    RequestConstraintSet,
    RequiredLayerAssessment,
    RequiredLayerConstraintValue,
    StrategyHypothesis,
    VisualInterpretationV2,
    build_intent_build_context,
    build_intent_variants,
    build_request_constraint_set,
    load_visual_interpretation_call,
    materialize_visual_interpretation_call,
)
from shaderforge.store import LocalArtifactCatalog, LocalArtifactStore


def _png_bytes(
    mode: str, size: tuple[int, int], values: list[int] | None = None
) -> bytes:
    if mode == "RGB":
        image = Image.new(mode, size, (255, 255, 255))
    else:
        assert values is not None
        image = Image.new(mode, size)
        image.putdata(values)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _mask(size: tuple[int, int], active: set[tuple[int, int]]) -> bytes:
    width, height = size
    values = [
        255 if (index % width, index // width) in active else 0
        for index in range(width * height)
    ]
    return _png_bytes("L", size, values)


def _build_evidence(
    tmp_path: Path,
    *,
    topology: str,
    hole_count: int,
    mask_has_hole: bool,
    include_highlight: bool = False,
) -> tuple[LocalArtifactCatalog, RuntimeTargetStructureEvidence]:
    run = LocalArtifactStore(tmp_path).start_run("project-v2", "run-v2-structure")
    catalog = LocalArtifactCatalog(run, run_id="run-v2-structure")
    size = (7, 7)
    source_bytes = _png_bytes("RGB", size)
    source_ref = catalog.put(
        run_id=catalog.run_id,
        kind="target_source",
        schema_version="target_source_v1",
        content_type="image/png",
        data=source_bytes,
    )
    normalized_ref = catalog.put(
        run_id=catalog.run_id,
        kind="normalized_reference",
        schema_version="normalized_target_png_v1",
        content_type="image/png",
        data=normalize_target_png(source_bytes),
    )
    subject_pixels = {
        (x, y)
        for x in range(1, 6)
        for y in range(1, 6)
        if not mask_has_hole or x in {1, 5} or y in {1, 5}
    }
    min_x = min(x for x, _ in subject_pixels)
    max_x = max(x for x, _ in subject_pixels)
    min_y = min(y for _, y in subject_pixels)
    max_y = max(y for _, y in subject_pixels)
    measured_bbox = BBoxUv(
        min_x=float(min_x / size[0]),
        min_y=float(1.0 - (max_y + 1) / size[1]),
        max_x=float((max_x + 1) / size[0]),
        max_y=float(1.0 - min_y / size[1]),
    )
    measured_center = (
        (0.5, 0.5) if mask_has_hole else (0.5000000000000001, 0.49999999999999994)
    )
    measured_orientation = math.pi / 2 if mask_has_hole else 0.0
    measured_axes = (
        (measured_bbox.max_x - measured_bbox.min_x) * 0.5,
        (measured_bbox.max_y - measured_bbox.min_y) * 0.5,
    )
    subject_ref = catalog.put(
        run_id=catalog.run_id,
        kind="subject_mask",
        schema_version="binary_mask_v1",
        content_type="image/png",
        data=_mask(size, subject_pixels),
    )
    index_ref = catalog.put(
        run_id=catalog.run_id,
        kind="target_evidence_index",
        schema_version="target_evidence_index_v1",
        content_type="application/json",
        data=b'{"version":1}',
    )
    draft = TargetHypothesis(
        hypothesis_id="hypothesis-runtime",
        hypothesis_hash="0" * 64,
        subject_mask_ref=subject_ref,
        instance_mask_refs=(subject_ref,),
        instance_geometries=(
            InstanceGeometryV2(
                instance_index=0,
                mask_ref=subject_ref,
                bbox_uv=measured_bbox,
                center_uv=measured_center,
                area_ratio=len(subject_pixels) / 49,
                axes_uv=measured_axes,
                orientation_rad=measured_orientation,
                fill_topology=topology,
                component_count=1,
                hole_count=hole_count,
            ),
        ),
        confidence=1.0,
        bbox_uv=measured_bbox,
        center_uv=measured_center,
        area_ratio=len(subject_pixels) / 49,
        axes_uv=measured_axes,
        orientation_rad=measured_orientation,
        fill_topology=topology,  # type: ignore[arg-type]
        component_count=1,
        instance_count=1,
        hole_count=hole_count,
        evidence_refs=(index_ref,),
    )
    target_sha256 = sha256(source_bytes).hexdigest()
    hypothesis = draft.model_copy(
        update={
            "hypothesis_hash": compute_target_hypothesis_hash(
                target_sha256,
                draft,
            )
        }
    )
    measurements = TargetMeasurementsV2(
        target_sha256=target_sha256,
        image_size=size,
        target_hypotheses=(hypothesis,),
        palette_lab=(LabSample(lab=(50.0, 0.0, 0.0), weight=1.0),),
        region_statistics=(
            RegionStatistics(
                region_id="subject",
                bbox_uv=hypothesis.bbox_uv,
                area_ratio=hypothesis.area_ratio,
                mean_lab=(50.0, 0.0, 0.0),
            ),
        ),
        symmetry=SymmetryEvidence(horizontal=1.0, vertical=1.0, radial=1.0),
        radiality=1.0,
        gradient_evidence=(),
        edge_refs=(index_ref,),
        evidence_index_ref=index_ref,
    )
    measurements_ref = catalog.put(
        run_id=catalog.run_id,
        kind="target_measurements",
        schema_version="target_measurements_v2_2",
        content_type="application/json",
        data=measurements.model_dump_json().encode("utf-8"),
    )
    required_layers = {"base_fill"}
    if include_highlight:
        required_layers.add("highlight")
    interpretation = VisualInterpretationV2(
        summary="runtime verifier 测试解释。",
        layer_hypotheses=(
            LayerHypothesis(
                layer_id="base",
                role="base_fill",
                order=0,
                confidence=0.9,
                region_description="主体内部",
                primitive_candidates=("solid_fill",),
                evidence_refs=(normalized_ref,),
            ),
        ),
        required_layer_assessments=tuple(
            RequiredLayerAssessment(
                layer=layer,
                status="required" if layer in required_layers else "not_required",
                confidence=0.9,
                rationale="测试闭集判断。",
                evidence_refs=(normalized_ref,),
            )
            for layer in REQUIRED_LAYER_ORDER
        ),
        primitive_candidates=(
            PrimitiveCandidate(
                candidate_id="base-solid",
                primitive_id="solid_fill",
                layer_id="base",
                confidence=0.9,
                evidence_refs=(normalized_ref,),
            ),
        ),
        strategy_hypotheses=(
            StrategyHypothesis(
                strategy_id="base-strategy",
                template_ids=("solid-template",),
                required_layer_ids=("base",),
                complexity="low",
                confidence=0.9,
                evidence_refs=(normalized_ref,),
            ),
        ),
        evidence_refs=(normalized_ref,),
    )
    audit_bundle = materialize_visual_interpretation_call(
        catalog=catalog,
        run_id=catalog.run_id,
        prompt_name="analyze_visual_layers_v2",
        prompt_version="analyze_visual_layers_v2_2",
        prompt_text="输出完整 required-layer taxonomy 闭集。",
        model_id="fixture/model",
        input_artifact_refs=(normalized_ref, measurements_ref),
        raw_response=interpretation.model_dump_json(),
        attempt_count=1,
        repair_count=0,
        parser_status="succeeded",
        interpretation=interpretation,
    )
    constraints: list[Constraint] = [
        Constraint(
            constraint_id="normalized-by-builder",
            kind="contract",
            strength="hard",
            scope="global",
            value=ContractConstraintValue(
                contract_id=WEBGL1_STATIC_NO_TEXTURE_V1.contract_id
            ),
            source="render_contract",
            source_revision=0,
            confidence=1.0,
            verification_status="verified",
        )
    ]
    if include_highlight:
        constraints.append(
            Constraint(
                constraint_id="normalized-by-builder",
                kind="required_layer",
                strength="hard",
                scope="object",
                scope_ref="subject",
                value=RequiredLayerConstraintValue(layer="highlight"),
                source="user",
                source_revision=0,
                confidence=1.0,
                verification_status="verified",
                evidence_refs=(normalized_ref,),
            )
        )
    constraint_set = build_request_constraint_set(
        constraint_set_id="runtime-constraints",
        target_sha256=target_sha256,
        request_revision=0,
        constraints=constraints,
        evidence_refs=(normalized_ref,),
    )
    constraint_set_ref = catalog.put(
        run_id=catalog.run_id,
        kind="request_constraint_set",
        schema_version="request_constraint_set_v1",
        content_type="application/json",
        data=constraint_set.model_dump_json().encode("utf-8"),
    )
    context = build_intent_build_context(
        contract_id=WEBGL1_STATIC_NO_TEXTURE_V1.contract_id,
        primitive_catalog_sha256="a" * 64,
        template_catalog_sha256="b" * 64,
        allowed_primitive_ids=("solid_fill",),
        allowed_template_ids=("solid-template",),
        allowed_interpretation_evidence_refs=(normalized_ref,),
    )
    context_ref = catalog.put(
        run_id=catalog.run_id,
        kind="intent_build_context",
        schema_version="intent_build_context_v1",
        content_type="application/json",
        data=context.model_dump_json().encode("utf-8"),
    )
    intent_result = build_intent_variants(
        measurements,
        interpretation,
        constraint_set,
        context,
    )
    intent = intent_result.variants[0]
    intent_ref = catalog.put(
        run_id=catalog.run_id,
        kind="intent_ir",
        schema_version="intent_v3",
        content_type="application/json",
        data=intent.model_dump_json().encode("utf-8"),
    )
    layer_masks = [RuntimeRequiredLayerMask(layer="base_fill", mask_ref=subject_ref)]
    if include_highlight:
        highlight_ref = catalog.put(
            run_id=catalog.run_id,
            kind="required_layer_mask",
            schema_version="binary_mask_v1",
            content_type="image/png",
            data=_mask(size, {(3, 5)}),
        )
        layer_masks.append(
            RuntimeRequiredLayerMask(layer="highlight", mask_ref=highlight_ref)
        )
    evidence = RuntimeTargetStructureEvidence(
        target_source_ref=source_ref,
        target_source_sha256=target_sha256,
        normalized_reference_ref=normalized_ref,
        measurements_ref=measurements_ref,
        interpretation_audit_ref=audit_bundle.audit_ref,
        constraint_set_ref=constraint_set_ref,
        intent_build_context_ref=context_ref,
        intent_ref=intent_ref,
        target_hypothesis_id=hypothesis.hypothesis_id,
        target_hypothesis_hash=hypothesis.hypothesis_hash,
        subject_mask_ref=subject_ref,
        instance_mask_refs=(subject_ref,),
        required_layer_masks=tuple(layer_masks),
    )
    return catalog, evidence


def test_runtime_structure_verifier_recomputes_hollow_hole_and_required_layers(
    tmp_path: Path,
) -> None:
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="hollow",
        hole_count=1,
        mask_has_hole=True,
        include_highlight=True,
    )

    result = verify_runtime_target_structure(evidence, resolver=catalog)

    assert result.status == "structure_verified"
    assert result.computed_component_count == 1
    assert result.computed_hole_count == 1
    assert result.target is not None
    assert result.target.required_layers == ("base_fill", "highlight")
    assert result.reason_codes == (
        "runtime_target_structure_and_required_layers_verified",
    )


def test_runtime_structure_verifier_rejects_claim_not_supported_by_mask(
    tmp_path: Path,
) -> None:
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="ring",
        hole_count=1,
        mask_has_hole=False,
    )

    result = verify_runtime_target_structure(evidence, resolver=catalog)

    assert result.status == "rejected"
    assert result.target is None
    assert result.computed_hole_count == 0
    assert result.reason_codes == ("hole_count_mismatch",)


def test_runtime_structure_verifier_rejects_omitted_required_layer_mask(
    tmp_path: Path,
) -> None:
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
        include_highlight=True,
    )
    base_only = evidence.model_copy(
        update={"required_layer_masks": (evidence.required_layer_masks[0],)}
    )

    result = verify_runtime_target_structure(base_only, resolver=catalog)

    assert result.status == "rejected"
    assert result.target is None
    assert result.reason_codes == ("required_layer_mask_set_mismatch",)


@pytest.mark.parametrize(
    ("layer", "status", "reason_code"),
    (
        ("shadow", "unknown", "required_layer_assessment_unknown"),
        (
            "highlight",
            "not_required",
            "required_layer_constraint_assessment_conflict",
        ),
    ),
)
def test_runtime_structure_verifier_replays_closed_assessment_failures(
    tmp_path: Path,
    layer: str,
    status: str,
    reason_code: str,
) -> None:
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
        include_highlight=True,
    )
    bundle = load_visual_interpretation_call(
        evidence.interpretation_audit_ref,
        resolver=catalog,
    )
    assert bundle.interpretation is not None
    raw = json.loads(bundle.interpretation.model_dump_json())
    for assessment in raw["required_layer_assessments"]:
        if assessment["layer"] == layer:
            assessment["status"] = status
    changed = VisualInterpretationV2.model_validate_json(
        json.dumps(raw),
        strict=True,
    )
    changed_bundle = materialize_visual_interpretation_call(
        catalog=catalog,
        run_id=catalog.run_id,
        prompt_name="analyze_visual_layers_v2",
        prompt_version="analyze_visual_layers_v2_2",
        prompt_text="输出完整 required-layer taxonomy 闭集。",
        model_id="fixture/model",
        input_artifact_refs=bundle.audit.input_artifact_refs,
        raw_response=changed.model_dump_json(),
        attempt_count=1,
        repair_count=0,
        parser_status="succeeded",
        interpretation=changed,
    )

    result = verify_runtime_target_structure(
        evidence.model_copy(
            update={"interpretation_audit_ref": changed_bundle.audit_ref}
        ),
        resolver=catalog,
    )

    assert result.status == "rejected"
    assert result.target is None
    assert result.reason_codes == (reason_code,)


def test_runtime_structure_evidence_requires_explicit_base_fill_mask(
    tmp_path: Path,
) -> None:
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
    )

    with pytest.raises(ValueError, match="必须包含 base_fill"):
        RuntimeTargetStructureEvidence(
            target_source_sha256=evidence.target_source_sha256,
            target_source_ref=evidence.target_source_ref,
            normalized_reference_ref=evidence.normalized_reference_ref,
            measurements_ref=evidence.measurements_ref,
            interpretation_audit_ref=evidence.interpretation_audit_ref,
            constraint_set_ref=evidence.constraint_set_ref,
            intent_build_context_ref=evidence.intent_build_context_ref,
            intent_ref=evidence.intent_ref,
            target_hypothesis_id=evidence.target_hypothesis_id,
            target_hypothesis_hash=evidence.target_hypothesis_hash,
            subject_mask_ref=evidence.subject_mask_ref,
            instance_mask_refs=evidence.instance_mask_refs,
            required_layer_masks=(),
        )

    result = verify_runtime_target_structure(evidence, resolver=catalog)
    assert result.status == "structure_verified"
    assert result.target is not None


def test_runtime_structure_verifier_rejects_measurements_identity_mismatch(
    tmp_path: Path,
) -> None:
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
    )
    mismatched = evidence.model_copy(
        update={"target_source_sha256": sha256(b"another-source").hexdigest()}
    )

    result = verify_runtime_target_structure(mismatched, resolver=catalog)

    assert result.status == "rejected"
    assert result.target is None
    assert result.reason_codes == ("target_source_identity_mismatch",)


def test_runtime_structure_verifier_rejects_normalized_reference_not_derived_from_source(
    tmp_path: Path,
) -> None:
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
    )
    tampered_normalized = catalog.put(
        run_id=catalog.run_id,
        kind="normalized_reference",
        schema_version="normalized_target_png_v1",
        content_type="image/png",
        data=_mask((7, 7), {(3, 3)}),
    )

    result = verify_runtime_target_structure(
        evidence.model_copy(update={"normalized_reference_ref": tampered_normalized}),
        resolver=catalog,
    )

    assert result.status == "rejected"
    assert result.reason_codes == ("normalized_reference_derivation_mismatch",)


def test_runtime_structure_verifier_rejects_overlapping_instance_partition(
    tmp_path: Path,
) -> None:
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
    )
    nested_instance_ref = catalog.put(
        run_id=catalog.run_id,
        kind="instance_mask",
        schema_version="binary_mask_v1",
        content_type="image/png",
        data=_mask((7, 7), {(3, 3)}),
    )
    measurements = TargetMeasurementsV2.model_validate_json(
        catalog.read_bytes(evidence.measurements_ref.artifact_id),
        strict=True,
    )
    original = measurements.target_hypotheses[0]
    draft = original.model_copy(
        update={
            "hypothesis_hash": "0" * 64,
            "instance_mask_refs": (evidence.subject_mask_ref, nested_instance_ref),
            "instance_geometries": (
                original.instance_geometries[0],
                original.instance_geometries[0].model_copy(
                    update={
                        "instance_index": 1,
                        "mask_ref": nested_instance_ref,
                        "bbox_uv": BBoxUv(
                            min_x=3 / 7,
                            min_y=1.0 - 4 / 7,
                            max_x=4 / 7,
                            max_y=1.0 - 3 / 7,
                        ),
                        "center_uv": (3.5 / 7, 1.0 - 3.5 / 7),
                        "area_ratio": 1 / 49,
                        "axes_uv": (0.5 / 7, 0.5 / 7),
                        "hole_count": 0,
                    }
                ),
            ),
            "instance_count": 2,
        }
    )
    hypothesis = draft.model_copy(
        update={
            "hypothesis_hash": compute_target_hypothesis_hash(
                measurements.target_sha256,
                draft,
            )
        }
    )
    changed_measurements = measurements.model_copy(
        update={"target_hypotheses": (hypothesis,)}
    )
    measurements_ref = catalog.put(
        run_id=catalog.run_id,
        kind="target_measurements",
        schema_version="target_measurements_v2_2",
        content_type="application/json",
        data=changed_measurements.model_dump_json().encode("utf-8"),
    )
    changed_evidence = evidence.model_copy(
        update={
            "measurements_ref": measurements_ref,
            "target_hypothesis_hash": hypothesis.hypothesis_hash,
            "instance_mask_refs": hypothesis.instance_mask_refs,
        }
    )

    result = verify_runtime_target_structure(changed_evidence, resolver=catalog)

    assert result.status == "rejected"
    assert result.reason_codes == ("instance_masks_overlap",)


def test_runtime_structure_verifier_rejects_tampered_instance_geometry(
    tmp_path: Path,
) -> None:
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
    )
    measurements = TargetMeasurementsV2.model_validate_json(
        catalog.read_bytes(evidence.measurements_ref.artifact_id), strict=True
    )
    original = measurements.target_hypotheses[0]
    geometry = original.instance_geometries[0].model_copy(
        update={"center_uv": (0.49, 0.5)}
    )
    draft = original.model_copy(
        update={"hypothesis_hash": "0" * 64, "instance_geometries": (geometry,)}
    )
    changed = draft.model_copy(
        update={
            "hypothesis_hash": compute_target_hypothesis_hash(
                measurements.target_sha256, draft
            )
        }
    )
    changed_measurements = measurements.model_copy(
        update={"target_hypotheses": (changed,)}
    )
    measurements_ref = catalog.put(
        run_id=catalog.run_id,
        kind="target_measurements",
        schema_version="target_measurements_v2_2",
        content_type="application/json",
        data=changed_measurements.model_dump_json().encode(),
    )

    result = verify_runtime_target_structure(
        evidence.model_copy(
            update={
                "measurements_ref": measurements_ref,
                "target_hypothesis_hash": changed.hypothesis_hash,
            }
        ),
        resolver=catalog,
    )

    assert result.status == "rejected"
    assert result.reason_codes == ("instance_geometry_remeasurement_mismatch",)


def test_runtime_structure_verifier_rejects_rehashed_hollow_to_ring_instance_flip(
    tmp_path: Path,
) -> None:
    """hole count 相同也必须从 mask 独立重测 per-instance topology。"""
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="hollow",
        hole_count=1,
        mask_has_hole=True,
    )
    measurements = TargetMeasurementsV2.model_validate_json(
        catalog.read_bytes(evidence.measurements_ref.artifact_id), strict=True
    )
    original = measurements.target_hypotheses[0]
    geometry = original.instance_geometries[0].model_copy(
        update={"fill_topology": "ring"}
    )
    draft = original.model_copy(
        update={"hypothesis_hash": "0" * 64, "instance_geometries": (geometry,)}
    )
    changed_hypothesis = draft.model_copy(
        update={
            "hypothesis_hash": compute_target_hypothesis_hash(
                measurements.target_sha256,
                draft,
            )
        }
    )
    changed_measurements = measurements.model_copy(
        update={"target_hypotheses": (changed_hypothesis,)}
    )
    measurements_ref = catalog.put(
        run_id=catalog.run_id,
        kind="target_measurements",
        schema_version="target_measurements_v2_2",
        content_type="application/json",
        data=changed_measurements.model_dump_json().encode(),
    )

    original_audit = load_visual_interpretation_call(
        evidence.interpretation_audit_ref,
        resolver=catalog,
    )
    assert original_audit.interpretation is not None
    changed_audit = materialize_visual_interpretation_call(
        catalog=catalog,
        run_id=catalog.run_id,
        prompt_name="analyze_visual_layers_v2",
        prompt_version="analyze_visual_layers_v2_2",
        prompt_text="输出完整 required-layer taxonomy 闭集。",
        model_id="fixture/model",
        input_artifact_refs=(evidence.normalized_reference_ref, measurements_ref),
        raw_response=original_audit.interpretation.model_dump_json(),
        attempt_count=1,
        repair_count=0,
        parser_status="succeeded",
        interpretation=original_audit.interpretation,
    )
    constraint_set = RequestConstraintSet.model_validate_json(
        catalog.read_bytes(evidence.constraint_set_ref.artifact_id),
        strict=True,
    )
    context = IntentBuildContext.model_validate_json(
        catalog.read_bytes(evidence.intent_build_context_ref.artifact_id),
        strict=True,
    )
    changed_intent = build_intent_variants(
        changed_measurements,
        original_audit.interpretation,
        constraint_set,
        context,
    ).variants[0]
    intent_ref = catalog.put(
        run_id=catalog.run_id,
        kind="intent_ir",
        schema_version="intent_v3",
        content_type="application/json",
        data=changed_intent.model_dump_json().encode(),
    )

    result = verify_runtime_target_structure(
        evidence.model_copy(
            update={
                "measurements_ref": measurements_ref,
                "interpretation_audit_ref": changed_audit.audit_ref,
                "intent_ref": intent_ref,
                "target_hypothesis_hash": changed_hypothesis.hypothesis_hash,
            }
        ),
        resolver=catalog,
    )

    assert result.status == "rejected"
    assert result.reason_codes == ("instance_geometry_remeasurement_mismatch",)
