from __future__ import annotations

from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from shaderforge.compiler import (
    compile_diagnostic_passes,
    compile_effect_genome,
    materialize_compilation,
    materialize_diagnostic_compilation,
)
from shaderforge.contracts.taxonomy import REQUIRED_LAYER_ORDER
from shaderforge.evaluation import (
    DiagnosticRenderReceiptV3,
    RenderedStructureEvidenceV4,
    RenderedStructureVerificationV4,
    RendererEnvironmentReceiptV3,
    RendererRequestReceiptV1,
    RendererRequestReceiptV2,
    compute_rendered_structure_evidence_hash,
    compute_rendered_structure_verification_hash,
    compute_renderer_environment_hash,
    compute_renderer_request_hash,
    materialize_renderer_request,
    measure_instance_relation_v2,
    measure_instance_structure_v3,
    measure_rendered_topology_v2,
    measure_visible_delta_pixel_count_v2,
    project_visible_delta_mask_v3,
    rendered_structure_diagnostic_size_v2,
    verify_rendered_structure_evidence,
)
from shaderforge.evaluation.rendered_structure import (
    LayerContributionResultV2,
    _is_layer_contribution_visible_v3,
)
from shaderforge.seeding import expand_seed_plans
from shaderforge.store import LocalArtifactCatalog, LocalArtifactStore
from tests.unit_tests.test_seed_plan_expander_v2 import _intent


def _png(active: set[tuple[int, int]], size: int = 16) -> bytes:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pixels = image.load()
    assert pixels is not None
    for x, y in active:
        pixels[x, y] = (255, 255, 255, 255)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _one_pixel_value(value: int) -> bytes:
    image = Image.new("RGBA", (1, 1), (value, value, value, value))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _opaque_png(size: int = 16) -> bytes:
    image = Image.new("RGBA", (size, size), (17, 23, 31, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_outline_visible_delta_may_live_outside_subject_without_lowering_floor() -> None:
    assert _is_layer_contribution_visible_v3(
        layer="outline",
        visible_pixel_count=16,
        visible_area_ratio=0.01,
        subject_overlap_ratio=0.0,
    )
    assert not _is_layer_contribution_visible_v3(
        layer="base_fill",
        visible_pixel_count=16,
        visible_area_ratio=0.01,
        subject_overlap_ratio=0.0,
    )
    assert not _is_layer_contribution_visible_v3(
        layer="outline",
        visible_pixel_count=3,
        visible_area_ratio=0.01,
        subject_overlap_ratio=0.0,
    )
    with pytest.raises(ValidationError, match="predicted_visible"):
        LayerContributionResultV2(
            layer="outline",
            enabled_in_genome=True,
            required_by_intent=True,
            predicted_visible=True,
            visible_pixel_count=3,
            visible_area_ratio=0.01,
            subject_overlap_ratio=0.0,
        )


def test_rendered_structure_verifier_remeasures_beauty_and_contributions(
    tmp_path: Path,
) -> None:
    raw_intent = _intent()
    intent = raw_intent.model_copy(
        update={"canvas": raw_intent.canvas.model_copy(update={"image_size": (16, 16)})}
    )
    genome = expand_seed_plans(intent).expanded_seeds[1].genome
    diagnostic_product = compile_diagnostic_passes(genome)
    run = LocalArtifactStore(tmp_path).start_run("project", "run-rendered-structure")
    catalog = LocalArtifactCatalog(run, run_id="run-rendered-structure")
    diagnostic_bundle = materialize_diagnostic_compilation(
        diagnostic_product,
        catalog=catalog,
        run_id=catalog.run_id,
    )
    compilation_bundle = materialize_compilation(
        compile_effect_genome(genome),
        catalog=catalog,
        run_id=catalog.run_id,
    )
    intent_ref = catalog.put(
        run_id=catalog.run_id,
        kind="intent",
        schema_version="intent_v3",
        content_type="application/json",
        data=intent.model_dump_json().encode(),
    )
    genome_ref = catalog.put(
        run_id=catalog.run_id,
        kind="genome",
        schema_version="genome_v0",
        content_type="application/json",
        data=genome.model_dump_json().encode(),
    )
    compilation_ref = catalog.put(
        run_id=catalog.run_id,
        kind="compilation_bundle",
        schema_version="compilation_bundle_v1",
        content_type="application/json",
        data=compilation_bundle.model_dump_json().encode(),
    )
    diagnostic_ref = catalog.put(
        run_id=catalog.run_id,
        kind="diagnostic_compilation_bundle",
        schema_version="diagnostic_compilation_bundle_v3",
        content_type="application/json",
        data=diagnostic_bundle.model_dump_json().encode(),
    )
    environment_payload = {
        "renderer_version": "fixture-renderer-v2",
        "browser_version": "fixture-browser-v2",
        "gl_version": "WebGL 1 fixture",
        "glsl_version": "WebGL GLSL ES 1.00 fixture",
        "gl_vendor": "fixture-vendor",
        "gl_renderer": "fixture-device",
        "webgl_context_kind": "webgl1",
        "canvas_alpha": True,
        "canvas_antialias": False,
        "canvas_depth": False,
        "canvas_stencil": False,
        "canvas_alpha_mode": "preserve_transparent_alpha_v1",
        "canvas_clear_color_rgba": (0.0, 0.0, 0.0, 0.0),
        "premultiplied_alpha": False,
        "preserve_drawing_buffer": True,
        "environment_hash": "0" * 64,
    }
    environment_payload["environment_hash"] = compute_renderer_environment_hash(
        environment_payload
    )
    environment = RendererEnvironmentReceiptV3.model_validate(
        environment_payload, strict=True
    )
    environment_ref = catalog.put(
        run_id=catalog.run_id,
        kind="renderer_environment",
        schema_version="renderer_environment_receipt_v3",
        content_type="application/json",
        data=environment.model_dump_json().encode(),
    )
    subject_pixels = {(x, y) for x in range(4, 12) for y in range(4, 12)}
    beauty_ref = catalog.put(
        run_id=catalog.run_id,
        kind="render_png",
        schema_version="render_png_v2",
        content_type="image/png",
        data=_png(subject_pixels),
    )
    beauty_request_payload = {
        "schema_version": "renderer_request_receipt_v2",
        "hash_version": "renderer_request_hash_v2",
        "run_id": catalog.run_id,
        "attempt_id": "attempt-beauty",
        "target_hypothesis_hash": intent.target_hypothesis_hash,
        "semantic_genome_hash": diagnostic_bundle.semantic_genome_hash,
        "compilation_ref": compilation_ref,
        "glsl_ref": compilation_bundle.glsl_ref,
        "render_profile": "beauty_full_v1",
        "logical_request_ordinal": 1,
        "beauty_capture_index": 0,
        "diagnostic_pass_id": None,
        "width": 16,
        "height": 16,
        "request_hash": "0" * 64,
    }
    beauty_request_payload["request_hash"] = compute_renderer_request_hash(
        beauty_request_payload
    )
    beauty_request = RendererRequestReceiptV2.model_validate(
        beauty_request_payload, strict=True
    )
    beauty_request_ref = materialize_renderer_request(
        catalog=catalog,
        run_id=catalog.run_id,
        receipt=beauty_request,
    )
    receipts: list[DiagnosticRenderReceiptV3] = []
    for ordinal, item in enumerate(diagnostic_bundle.passes, start=2):
        request_payload = {
            "schema_version": "renderer_request_receipt_v2",
            "hash_version": "renderer_request_hash_v2",
            "run_id": catalog.run_id,
            "attempt_id": f"attempt-{item.pass_id}",
            "target_hypothesis_hash": intent.target_hypothesis_hash,
            "semantic_genome_hash": diagnostic_bundle.semantic_genome_hash,
            "compilation_ref": diagnostic_ref,
            "glsl_ref": item.source_ref,
            "render_profile": (
                "subject_visible_delta_full_v1"
                if item.pass_kind == "subject_visible_delta"
                else "instance_visible_delta_full_v1"
                if item.pass_kind == "instance_visible_delta"
                else "layer_visible_delta_lowres_v1"
            ),
            "logical_request_ordinal": ordinal,
            "beauty_capture_index": None,
            "diagnostic_pass_id": item.pass_id,
            "width": 16,
            "height": 16,
            "request_hash": "0" * 64,
        }
        request_payload["request_hash"] = compute_renderer_request_hash(request_payload)
        request = RendererRequestReceiptV2.model_validate(request_payload, strict=True)
        request_ref = materialize_renderer_request(
            catalog=catalog,
            run_id=catalog.run_id,
            receipt=request,
        )
        render_ref = catalog.put(
            run_id=catalog.run_id,
            kind="diagnostic_render_png",
            schema_version="diagnostic_render_png_v3",
            content_type="image/png",
            data=_png(subject_pixels),
        )
        receipts.append(
            DiagnosticRenderReceiptV3(
                pass_id=item.pass_id,
                pass_kind=item.pass_kind,
                canonical_node_id=item.canonical_node_id,
                ownership_policy_version=item.ownership_policy_version,
                source_ref=item.source_ref,
                source_sha256=item.source_sha256,
                instance_index=item.instance_index,
                layer=item.layer,
                renderer_request_ref=request_ref,
                renderer_request_artifact_sha256=request_ref.sha256,
                renderer_request_hash=request.request_hash,
                renderer_environment_ref=environment_ref,
                renderer_environment_artifact_sha256=environment_ref.sha256,
                renderer_environment_hash=environment.environment_hash,
                render_ref=render_ref,
                render_sha256=render_ref.sha256,
            )
        )
    payload = {
        "run_id": catalog.run_id,
        "candidate_id": "candidate-rendered-structure",
        "intent_id": intent.intent_id,
        "intent_ref": intent_ref,
        "intent_sha256": intent_ref.sha256,
        "target_hypothesis_id": intent.target_hypothesis_id,
        "target_hypothesis_hash": intent.target_hypothesis_hash,
        "genome_id": genome.genome_id,
        "genome_ref": genome_ref,
        "genome_sha256": genome_ref.sha256,
        "semantic_genome_hash": diagnostic_bundle.semantic_genome_hash,
        "ownership_policy_version": diagnostic_bundle.ownership_policy_version,
        "compilation_ref": compilation_ref,
        "compilation_sha256": compilation_ref.sha256,
        "diagnostic_compilation_ref": diagnostic_ref,
        "diagnostic_compilation_sha256": diagnostic_ref.sha256,
        "beauty_renderer_request_ref": beauty_request_ref,
        "beauty_renderer_request_artifact_sha256": beauty_request_ref.sha256,
        "beauty_renderer_request_hash": beauty_request.request_hash,
        "renderer_environment_ref": environment_ref,
        "renderer_environment_artifact_sha256": environment_ref.sha256,
        "renderer_environment_hash": environment.environment_hash,
        "beauty_render_ref": beauty_ref,
        "beauty_render_sha256": beauty_ref.sha256,
        "diagnostic_receipts": tuple(receipts),
        "record_hash": "0" * 64,
    }
    payload["record_hash"] = compute_rendered_structure_evidence_hash(payload)
    evidence = RenderedStructureEvidenceV4.model_validate(payload, strict=True)

    verification = verify_rendered_structure_evidence(
        evidence,
        resolver=catalog,
        intent=intent,
        genome=genome,
        compilation_bundle=compilation_bundle,
        diagnostic_bundle=diagnostic_bundle,
    )

    assert verification.status == "structure_verified", verification.reason_codes
    assert verification.metric_version == "rendered_structure_metric_v3_2"
    assert verification.measured_instance_count == 1
    assert verification.measured_hole_count == 0
    assert verification.diagnostic_union_iou == 1.0
    assert verification.beauty_subject_iou == 1.0
    assert verification.instance_structure_results[0].passed
    with pytest.raises(ValidationError, match="passed"):
        type(verification.instance_structure_results[0]).model_validate(
            {
                **verification.instance_structure_results[0].model_dump(mode="python"),
                "passed": False,
            },
            strict=True,
        )
    with pytest.raises(ValidationError, match="solid/open"):
        type(verification.instance_structure_results[0]).model_validate(
            {
                **verification.instance_structure_results[0].model_dump(mode="python"),
                "measured_hole_count": 1,
                "passed": False,
            },
            strict=True,
        )
    assert len(verification.layer_contribution_results) == len(REQUIRED_LAYER_ORDER)
    assert all(
        item.predicted_visible
        for item in verification.layer_contribution_results
        if item.required_by_intent
    )
    assert all(
        not item.predicted_visible
        for item in verification.layer_contribution_results
        if not item.enabled_in_genome
    )
    with pytest.raises(ValidationError):
        RenderedStructureEvidenceV4.model_validate(
            {
                **payload,
                "schema_version": "rendered_structure_evidence_v2",
                "hash_version": "rendered_structure_evidence_hash_v2",
            },
            strict=True,
        )
    with pytest.raises(ValidationError):
        RenderedStructureVerificationV4.model_validate(
            {
                **verification.model_dump(mode="python"),
                "schema_version": "rendered_structure_verification_v2",
                "hash_version": "rendered_structure_verification_hash_v2",
            },
            strict=True,
        )
    with pytest.raises(ValidationError):
        RenderedStructureVerificationV4.model_validate(
            {
                **verification.model_dump(mode="python"),
                "metric_version": "rendered_structure_metric_v3",
            },
            strict=True,
        )
    legacy_evidence = payload.copy()
    legacy_evidence.pop("ownership_policy_version")
    legacy_evidence.update(
        {
            "schema_version": "rendered_structure_evidence_v3",
            "hash_version": "rendered_structure_evidence_hash_v3",
            "record_hash": "0" * 64,
        }
    )
    legacy_evidence["record_hash"] = compute_rendered_structure_evidence_hash(
        legacy_evidence
    )
    with pytest.raises(ValidationError):
        RenderedStructureEvidenceV4.model_validate(legacy_evidence, strict=True)
    legacy_verification = verification.model_dump(mode="python")
    legacy_verification.pop("ownership_policy_version")
    legacy_verification.update(
        {
            "schema_version": "rendered_structure_verification_v3",
            "hash_version": "rendered_structure_verification_hash_v3",
            "metric_version": "rendered_structure_metric_v3_1",
            "record_hash": "0" * 64,
        }
    )
    legacy_verification["record_hash"] = (
        compute_rendered_structure_verification_hash(legacy_verification)
    )
    with pytest.raises(ValidationError):
        RenderedStructureVerificationV4.model_validate(
            legacy_verification, strict=True
        )
    internally_false = {
        **verification.model_dump(mode="python"),
        "instance_masks_mutually_exclusive": False,
        "record_hash": "0" * 64,
    }
    internally_false["record_hash"] = compute_rendered_structure_verification_hash(
        internally_false
    )
    with pytest.raises(ValidationError, match="内部结构测量闭包"):
        RenderedStructureVerificationV4.model_validate(
            internally_false,
            strict=True,
        )
    with pytest.raises(ValidationError):
        RenderedStructureVerificationV4.model_validate(
            {
                **verification.model_dump(mode="python"),
                "metric_version": "rendered_structure_metric_v2_1",
            },
            strict=True,
        )

    opaque_environment_payload = {
        **environment.model_dump(mode="python", exclude={"environment_hash"}),
        "canvas_alpha_mode": "force_opaque_alpha_v1",
        "canvas_alpha": False,
        "canvas_clear_color_rgba": (0.0, 0.0, 0.0, 1.0),
        "environment_hash": "0" * 64,
    }
    opaque_environment_payload["environment_hash"] = compute_renderer_environment_hash(
        opaque_environment_payload
    )
    opaque_environment = RendererEnvironmentReceiptV3.model_validate(
        opaque_environment_payload, strict=True
    )
    opaque_environment_ref = catalog.put(
        run_id=catalog.run_id,
        kind="renderer_environment",
        schema_version="renderer_environment_receipt_v3",
        content_type="application/json",
        data=opaque_environment.model_dump_json().encode(),
    )
    opaque_beauty_ref = catalog.put(
        run_id=catalog.run_id,
        kind="render_png",
        schema_version="render_png_v2",
        content_type="image/png",
        data=_opaque_png(),
    )
    smaller_subject_ref = catalog.put(
        run_id=catalog.run_id,
        kind="diagnostic_render_png",
        schema_version="diagnostic_render_png_v3",
        content_type="image/png",
        data=_png({(x, y) for x in range(6, 10) for y in range(6, 10)}),
    )
    transparent_receipts = tuple(
        item.model_copy(
            update={
                "render_ref": smaller_subject_ref,
                "render_sha256": smaller_subject_ref.sha256,
            }
        )
        if item.pass_kind == "subject_visible_delta"
        else item
        for item in receipts
    )
    transparent_payload = {**payload, "diagnostic_receipts": transparent_receipts}
    transparent_payload["record_hash"] = compute_rendered_structure_evidence_hash(
        transparent_payload
    )
    transparent_evidence = RenderedStructureEvidenceV4.model_validate(
        transparent_payload, strict=True
    )
    transparent_rejected = verify_rendered_structure_evidence(
        transparent_evidence,
        resolver=catalog,
        intent=intent,
        genome=genome,
        compilation_bundle=compilation_bundle,
        diagnostic_bundle=diagnostic_bundle,
    )
    assert transparent_rejected.beauty_subject_iou is not None
    assert transparent_rejected.beauty_subject_iou < 0.90
    assert (
        "transparent_beauty_subject_iou_below_threshold"
        in transparent_rejected.reason_codes
    )

    hollow_pixels = subject_pixels - {(x, y) for x in range(7, 9) for y in range(7, 9)}
    hollow_ref = catalog.put(
        run_id=catalog.run_id,
        kind="diagnostic_render_png",
        schema_version="diagnostic_render_png_v3",
        content_type="image/png",
        data=_png(hollow_pixels),
    )
    hollow_receipts = tuple(
        item.model_copy(
            update={"render_ref": hollow_ref, "render_sha256": hollow_ref.sha256}
        )
        if item.pass_kind in {"subject_visible_delta", "instance_visible_delta"}
        else item
        for item in receipts
    )
    hollow_payload = {**payload, "diagnostic_receipts": hollow_receipts}
    hollow_payload["record_hash"] = compute_rendered_structure_evidence_hash(
        hollow_payload
    )
    hollow_evidence = RenderedStructureEvidenceV4.model_validate(
        hollow_payload, strict=True
    )
    hollow_rejected = verify_rendered_structure_evidence(
        hollow_evidence,
        resolver=catalog,
        intent=intent,
        genome=genome,
        compilation_bundle=compilation_bundle,
        diagnostic_bundle=diagnostic_bundle,
    )
    instance_result = hollow_rejected.instance_structure_results[0]
    assert instance_result.measured_topology == "hollow"
    assert instance_result.measured_hole_count == 1
    assert not instance_result.passed
    assert "instance_topology_mismatch:0" in hollow_rejected.reason_codes
    assert "instance_hole_count_mismatch:0" in hollow_rejected.reason_codes
    noncanonical_reasons = {
        **hollow_rejected.model_dump(mode="python"),
        "reason_codes": (
            *hollow_rejected.reason_codes,
            hollow_rejected.reason_codes[0],
        ),
        "record_hash": "0" * 64,
    }
    noncanonical_reasons["record_hash"] = compute_rendered_structure_verification_hash(
        noncanonical_reasons
    )
    with pytest.raises(ValidationError, match="reason_codes"):
        RenderedStructureVerificationV4.model_validate(
            noncanonical_reasons,
            strict=True,
        )
    opaque_receipts = tuple(
        item.model_copy(
            update={
                "renderer_environment_ref": opaque_environment_ref,
                "renderer_environment_artifact_sha256": opaque_environment_ref.sha256,
                "renderer_environment_hash": opaque_environment.environment_hash,
                **(
                    {
                        "render_ref": smaller_subject_ref,
                        "render_sha256": smaller_subject_ref.sha256,
                    }
                    if item.pass_kind == "subject_visible_delta"
                    else {}
                ),
            }
        )
        for item in receipts
    )
    opaque_payload = {
        **payload,
        "renderer_environment_ref": opaque_environment_ref,
        "renderer_environment_artifact_sha256": opaque_environment_ref.sha256,
        "renderer_environment_hash": opaque_environment.environment_hash,
        "beauty_render_ref": opaque_beauty_ref,
        "beauty_render_sha256": opaque_beauty_ref.sha256,
        "diagnostic_receipts": opaque_receipts,
    }
    opaque_payload["record_hash"] = compute_rendered_structure_evidence_hash(
        opaque_payload
    )
    opaque_evidence = RenderedStructureEvidenceV4.model_validate(
        opaque_payload, strict=True
    )
    opaque_rejected = verify_rendered_structure_evidence(
        opaque_evidence,
        resolver=catalog,
        intent=intent,
        genome=genome,
        compilation_bundle=compilation_bundle,
        diagnostic_bundle=diagnostic_bundle,
    )
    assert opaque_rejected.renderer_canvas_contract == "force_opaque_alpha_v1"
    assert opaque_rejected.diagnostic_union_iou < 0.90
    assert (
        "diagnostic_union_subject_iou_below_threshold" in opaque_rejected.reason_codes
    )

    highlight_index = next(
        index for index, item in enumerate(receipts) if item.layer == "highlight"
    )
    noise_ref = catalog.put(
        run_id=catalog.run_id,
        kind="diagnostic_render_png",
        schema_version="diagnostic_render_png_v3",
        content_type="image/png",
        data=_png({(8, 8)}),
    )
    noisy_receipts = list(receipts)
    noisy_receipts[highlight_index] = receipts[highlight_index].model_copy(
        update={"render_ref": noise_ref, "render_sha256": noise_ref.sha256}
    )
    noisy_payload = {**payload, "diagnostic_receipts": tuple(noisy_receipts)}
    noisy_payload["record_hash"] = compute_rendered_structure_evidence_hash(
        noisy_payload
    )
    noisy = RenderedStructureEvidenceV4.model_validate(noisy_payload, strict=True)

    rejected = verify_rendered_structure_evidence(
        noisy,
        resolver=catalog,
        intent=intent,
        genome=genome,
        compilation_bundle=compilation_bundle,
        diagnostic_bundle=diagnostic_bundle,
    )

    assert rejected.status == "rejected"
    assert "required_layer_not_visible:highlight" in rejected.reason_codes

    wrong_kind_ref = replace(evidence.intent_ref, kind="genome")
    wrong_kind_payload = {**payload, "intent_ref": wrong_kind_ref}
    wrong_kind_payload["record_hash"] = compute_rendered_structure_evidence_hash(
        wrong_kind_payload
    )
    wrong_kind = RenderedStructureEvidenceV4.model_validate(
        wrong_kind_payload, strict=True
    )
    wrong_kind_result = verify_rendered_structure_evidence(
        wrong_kind,
        resolver=catalog,
        intent=intent,
        genome=genome,
        compilation_bundle=compilation_bundle,
        diagnostic_bundle=diagnostic_bundle,
    )
    assert "typed_artifact_recovery_failed" in wrong_kind_result.reason_codes

    wrong_genome_payload = {**payload, "genome_id": "genome-wrong-identity"}
    wrong_genome_payload["record_hash"] = compute_rendered_structure_evidence_hash(
        wrong_genome_payload
    )
    wrong_genome = RenderedStructureEvidenceV4.model_validate(
        wrong_genome_payload, strict=True
    )
    wrong_genome_result = verify_rendered_structure_evidence(
        wrong_genome,
        resolver=catalog,
        intent=intent,
        genome=genome,
        compilation_bundle=compilation_bundle,
        diagnostic_bundle=diagnostic_bundle,
    )
    assert "identity_binding_mismatch" in wrong_genome_result.reason_codes

    legacy_payload = {
        "run_id": catalog.run_id,
        "attempt_id": "attempt-legacy-beauty",
        "target_hypothesis_hash": intent.target_hypothesis_hash,
        "semantic_genome_hash": diagnostic_bundle.semantic_genome_hash,
        "compilation_ref": compilation_ref,
        "glsl_ref": compilation_bundle.glsl_ref,
        "width": 16,
        "height": 16,
        "request_hash": "0" * 64,
    }
    legacy_payload["request_hash"] = compute_renderer_request_hash(legacy_payload)
    legacy_request = RendererRequestReceiptV1.model_validate(
        legacy_payload, strict=True
    )
    legacy_ref = materialize_renderer_request(
        catalog=catalog,
        run_id=catalog.run_id,
        receipt=legacy_request,
    )
    legacy_evidence_payload = {
        **payload,
        "beauty_renderer_request_ref": legacy_ref,
        "beauty_renderer_request_artifact_sha256": legacy_ref.sha256,
        "beauty_renderer_request_hash": legacy_request.request_hash,
    }
    legacy_evidence_payload["record_hash"] = compute_rendered_structure_evidence_hash(
        legacy_evidence_payload
    )
    legacy_evidence = RenderedStructureEvidenceV4.model_validate(
        legacy_evidence_payload, strict=True
    )
    legacy_result = verify_rendered_structure_evidence(
        legacy_evidence,
        resolver=catalog,
        intent=intent,
        genome=genome,
        compilation_bundle=compilation_bundle,
        diagnostic_bundle=diagnostic_bundle,
    )
    assert "typed_artifact_recovery_failed" in legacy_result.reason_codes

    layer_receipt_index = next(
        index
        for index, item in enumerate(receipts)
        if item.pass_kind == "layer_visible_delta"
    )
    layer_receipt = receipts[layer_receipt_index]
    wrong_profile_payload = {
        "schema_version": "renderer_request_receipt_v2",
        "hash_version": "renderer_request_hash_v2",
        "run_id": catalog.run_id,
        "attempt_id": "attempt-wrong-diagnostic-profile",
        "target_hypothesis_hash": intent.target_hypothesis_hash,
        "semantic_genome_hash": diagnostic_bundle.semantic_genome_hash,
        "compilation_ref": diagnostic_ref,
        "glsl_ref": layer_receipt.source_ref,
        "render_profile": "instance_visible_delta_full_v1",
        "logical_request_ordinal": 99,
        "beauty_capture_index": None,
        "diagnostic_pass_id": layer_receipt.pass_id,
        "width": 16,
        "height": 16,
        "request_hash": "0" * 64,
    }
    wrong_profile_payload["request_hash"] = compute_renderer_request_hash(
        wrong_profile_payload
    )
    wrong_profile_request = RendererRequestReceiptV2.model_validate(
        wrong_profile_payload, strict=True
    )
    wrong_profile_ref = materialize_renderer_request(
        catalog=catalog,
        run_id=catalog.run_id,
        receipt=wrong_profile_request,
    )
    wrong_profile_receipts = list(receipts)
    wrong_profile_receipts[layer_receipt_index] = layer_receipt.model_copy(
        update={
            "renderer_request_ref": wrong_profile_ref,
            "renderer_request_artifact_sha256": wrong_profile_ref.sha256,
            "renderer_request_hash": wrong_profile_request.request_hash,
        }
    )
    wrong_profile_evidence_payload = {
        **payload,
        "diagnostic_receipts": tuple(wrong_profile_receipts),
    }
    wrong_profile_evidence_payload["record_hash"] = (
        compute_rendered_structure_evidence_hash(wrong_profile_evidence_payload)
    )
    wrong_profile_evidence = RenderedStructureEvidenceV4.model_validate(
        wrong_profile_evidence_payload, strict=True
    )
    wrong_profile_result = verify_rendered_structure_evidence(
        wrong_profile_evidence,
        resolver=catalog,
        intent=intent,
        genome=genome,
        compilation_bundle=compilation_bundle,
        diagnostic_bundle=diagnostic_bundle,
    )
    assert (
        f"diagnostic_artifact_recovery_failed:{layer_receipt.pass_id}"
        in wrong_profile_result.reason_codes
    )


def test_diagnostic_compiler_keeps_every_required_layer_in_pass_set(
    tmp_path: Path,
) -> None:
    del tmp_path
    intent = _intent()
    genome = expand_seed_plans(intent).expanded_seeds[0].genome
    product = compile_diagnostic_passes(genome)
    required = {
        f"layer_{item.role}_visible_delta" for item in intent.layers if item.required
    }
    assert required <= {item.pass_id for item in product.passes}


def test_renderer_environment_rejects_alpha_contract_drift() -> None:
    base = {
        "renderer_version": "fixture-renderer-v2",
        "browser_version": "fixture-browser-v2",
        "gl_version": "WebGL 1 fixture",
        "glsl_version": "WebGL GLSL ES 1.00 fixture",
        "gl_vendor": "fixture-vendor",
        "gl_renderer": "fixture-device",
        "webgl_context_kind": "webgl1",
        "canvas_alpha": False,
        "canvas_antialias": False,
        "canvas_depth": False,
        "canvas_stencil": False,
        "premultiplied_alpha": False,
        "preserve_drawing_buffer": True,
        "environment_hash": "0" * 64,
    }
    with pytest.raises(ValidationError, match="clear alpha"):
        RendererEnvironmentReceiptV3.model_validate(
            {
                **base,
                "canvas_alpha": True,
                "canvas_alpha_mode": "preserve_transparent_alpha_v1",
                "canvas_clear_color_rgba": (0.0, 0.0, 0.0, 1.0),
            },
            strict=True,
        )
    with pytest.raises(ValidationError, match="clear color"):
        RendererEnvironmentReceiptV3.model_validate(
            {
                **base,
                "canvas_alpha_mode": "force_opaque_alpha_v1",
                "canvas_clear_color_rgba": (1.1, 0.0, 0.0, 1.0),
            },
            strict=True,
        )
    with pytest.raises(ValidationError, match="alpha mode"):
        RendererEnvironmentReceiptV3.model_validate(
            {
                **base,
                "canvas_alpha_mode": "preserve_transparent_alpha_v1",
                "canvas_clear_color_rgba": (0.0, 0.0, 0.0, 0.0),
            },
            strict=True,
        )


def test_rendered_topology_metric_has_frozen_ring_hollow_open_boundaries() -> None:
    solid = {(x, y) for x in range(2, 14) for y in range(2, 14)}
    ring = {(x, y) for x, y in solid if not (4 <= x <= 11 and 4 <= y <= 11)}
    hollow = {(x, y) for x, y in solid if not (7 <= x <= 8 and 7 <= y <= 8)}
    opened = {pixel for pixel in ring if not (pixel[0] >= 10 and 5 <= pixel[1] <= 10)}

    assert measure_rendered_topology_v2(_png(solid))[0] == "solid"
    assert measure_rendered_topology_v2(_png(ring))[0] == "ring"
    assert measure_rendered_topology_v2(_png(hollow))[0] == "hollow"
    assert measure_rendered_topology_v2(_png(opened))[0] == "open"


def test_visible_delta_byte_threshold_and_diagnostic_sizes_are_frozen() -> None:
    assert measure_visible_delta_pixel_count_v2(_one_pixel_value(7)) == 0
    assert measure_visible_delta_pixel_count_v2(_one_pixel_value(8)) == 1
    projection = project_visible_delta_mask_v3(_png({(2, 3), (4, 5)}))
    assert (projection.width, projection.height) == (16, 16)
    assert projection.active_pixel_count == 2
    assert len(projection.canonical_bitmask_sha256) == 64
    assert projection == project_visible_delta_mask_v3(_png({(2, 3), (4, 5)}))
    assert projection != project_visible_delta_mask_v3(_png({(2, 3), (5, 4)}))
    with pytest.raises(ValidationError, match="画布面积"):
        type(projection).model_validate(
            {
                **projection.model_dump(mode="python"),
                "active_pixel_count": projection.width * projection.height + 1,
            },
            strict=True,
        )
    assert rendered_structure_diagnostic_size_v2(
        pass_kind="instance_visible_delta", width=192, height=96
    ) == (192, 96)
    assert rendered_structure_diagnostic_size_v2(
        pass_kind="layer_visible_delta", width=192, height=96
    ) == (64, 32)


def test_instance_relation_metric_distinguishes_overlap_touch_and_disjoint() -> None:
    overlap = measure_instance_relation_v2(
        relation_id="overlap",
        kind="overlap",
        subject_ref="a",
        object_ref="b",
        subject_png=_png({(2, 2), (3, 2)}),
        object_png=_png({(3, 2), (4, 2)}),
    )
    touches = measure_instance_relation_v2(
        relation_id="touches",
        kind="touches",
        subject_ref="a",
        object_ref="b",
        subject_png=_png({(2, 2)}),
        object_png=_png({(3, 2)}),
    )
    disjoint = measure_instance_relation_v2(
        relation_id="disjoint",
        kind="disjoint",
        subject_ref="a",
        object_ref="b",
        subject_png=_png({(2, 2)}),
        object_png=_png({(5, 2)}),
    )

    assert not overlap.passed and overlap.intersection_pixel_count == 1
    assert touches.passed and touches.boundary_touch_pixel_count == 1
    assert disjoint.passed


def test_instance_structure_metric_rejects_ring_hollow_and_open_solid_drift() -> None:
    size = 64
    center = (size - 1) / 2
    disk = {
        (x, y)
        for y in range(size)
        for x in range(size)
        if (x - center) ** 2 + (y - center) ** 2 <= 24**2
    }
    ring = {(x, y) for x, y in disk if (x - center) ** 2 + (y - center) ** 2 >= 12**2}
    opened = {
        (x, y) for x, y in ring if not (x >= int(center) and abs(y - center) <= 3)
    }

    ring_as_hollow = measure_instance_structure_v3(
        _png(ring, size=size),
        instance_index=0,
        instance_id="instance_0000",
        expected_topology="hollow",
        expected_component_count=1,
        expected_hole_count=1,
    )
    open_as_solid = measure_instance_structure_v3(
        _png(opened, size=size),
        instance_index=0,
        instance_id="instance_0000",
        expected_topology="solid",
        expected_component_count=1,
        expected_hole_count=0,
    )

    assert ring_as_hollow.measured_topology == "ring"
    assert ring_as_hollow.measured_hole_count == 1
    assert not ring_as_hollow.passed
    assert open_as_solid.measured_topology == "open"
    assert open_as_solid.measured_hole_count == 0
    assert not open_as_solid.passed
