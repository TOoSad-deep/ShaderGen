from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from shaderforge.compiler import (
    compile_diagnostic_passes,
    compile_effect_genome,
    materialize_compilation,
    materialize_diagnostic_compilation,
)
from shaderforge.evaluation import (
    CandidateAttemptEvidenceV1,
    DiagnosticRenderReceiptV3,
    GeneratorAdmissionDecision,
    MeasurementSeedAdmissionPolicy,
    RenderedStructureEvidenceV4,
    RendererEnvironmentReceiptV3,
    RendererRequestReceiptV2,
    compute_rendered_structure_evidence_hash,
    compute_rendered_structure_verification_hash,
    compute_renderer_environment_hash,
    compute_renderer_request_hash,
    decide_trusted_runtime_admission,
    materialize_attempt_evidence,
    materialize_renderer_request,
    materialize_runtime_target_structure_artifacts,
    rendered_structure_diagnostic_size_v2,
    verify_rendered_structure_evidence,
)
from shaderforge.evaluation.candidate_artifacts import (
    COMPILATION_ARTIFACT_KIND,
    CONSTRAINT_EVALUATION_ARTIFACT_KIND,
    EVALUATION_ARTIFACT_KIND,
    GENOME_ARTIFACT_KIND,
    GENOME_ARTIFACT_SCHEMA_VERSION,
    INTENT_ARTIFACT_KIND,
    INTENT_ARTIFACT_SCHEMA_VERSION,
    RENDER_ARTIFACT_KIND,
    RENDER_ARTIFACT_SCHEMA_VERSION,
    TYPED_COMPILATION_ARTIFACT_SCHEMA_VERSION,
    TYPED_CONSTRAINT_EVALUATION_ARTIFACT_SCHEMA_VERSION,
    TYPED_EVALUATION_ARTIFACT_SCHEMA_VERSION,
    CandidateMaterializationInputV2,
    TypedCandidateArtifactBundleV2,
    load_candidate_artifact_bundle,
    load_typed_candidate_artifacts,
    materialize_typed_candidate_artifacts,
)
from shaderforge.evaluation.models_v2 import (
    CandidateRecordV2,
    compute_candidate_record_hash,
)
from shaderforge.evaluation.render_runtime_artifacts import (
    RenderCallOutcomeV2,
    RenderPlanItemV2,
    RenderPlanV2,
    RenderProgressV2,
    build_repeatability_evidence,
    compute_render_plan_hash,
    compute_render_progress_hash,
    materialize_render_model,
)
from shaderforge.evaluation.runtime_admission import (
    RuntimeAdmissionRejected,
    TrustedRuntimeSelectorInput,
    load_trusted_runtime_selector_input,
)
from shaderforge.evaluation.typed_evaluation import (
    IntentConstraintEvaluationV3,
    compute_intent_constraint_evaluation_hash_v3,
    evaluate_intent_genome_constraints,
    evaluate_intent_genome_constraints_v3,
    with_basic_evaluation_record_hash,
)
from shaderforge.genome import TypedEffectGenome, compute_genome_hashes
from shaderforge.intent import IntentIR
from shaderforge.seeding import expand_seed_plans
from shaderforge.store import (
    ArtifactRefV2,
    LocalArtifactCatalog,
    LocalArtifactStore,
)
from tests.unit_tests.test_runtime_target_structure_verifier import _build_evidence

RUN_ID = "run-v2-structure"


def _put(
    catalog: LocalArtifactCatalog,
    *,
    kind: str,
    schema_version: str,
    content_type: str,
    data: bytes,
) -> ArtifactRefV2:
    return catalog.put(
        run_id=RUN_ID,
        kind=kind,
        schema_version=schema_version,
        content_type=content_type,
        data=data,
    )


def _typed_candidate(
    catalog: LocalArtifactCatalog,
    *,
    intent_source_ref: ArtifactRefV2,
    with_renderer_attempt: bool = False,
    generator_version: str = "effect_genome_expander_v2",
) -> TypedCandidateArtifactBundleV2:
    intent = IntentIR.model_validate_json(
        catalog.read_bytes(intent_source_ref.artifact_id),
        strict=True,
    )
    expansion = expand_seed_plans(intent, random_seed=17)
    genome = expansion.expanded_seeds[0].genome
    assert isinstance(genome, TypedEffectGenome)
    hashes = compute_genome_hashes(genome)
    intent_ref = _put(
        catalog,
        kind=INTENT_ARTIFACT_KIND,
        schema_version=INTENT_ARTIFACT_SCHEMA_VERSION,
        content_type="application/json",
        data=intent.model_dump_json().encode("utf-8"),
    )
    genome_ref = _put(
        catalog,
        kind=GENOME_ARTIFACT_KIND,
        schema_version=GENOME_ARTIFACT_SCHEMA_VERSION,
        content_type="application/json",
        data=genome.model_dump_json().encode("utf-8"),
    )
    product = compile_effect_genome(genome)
    compilation = materialize_compilation(product, catalog=catalog, run_id=RUN_ID)
    compilation_ref = _put(
        catalog,
        kind=COMPILATION_ARTIFACT_KIND,
        schema_version=TYPED_COMPILATION_ARTIFACT_SCHEMA_VERSION,
        content_type="application/json",
        data=compilation.model_dump_json().encode("utf-8"),
    )
    diagnostic = materialize_diagnostic_compilation(
        compile_diagnostic_passes(genome), catalog=catalog, run_id=RUN_ID
    )
    diagnostic_ref = _put(
        catalog,
        kind="diagnostic_compilation_bundle",
        schema_version="diagnostic_compilation_bundle_v3",
        content_type="application/json",
        data=diagnostic.model_dump_json().encode("utf-8"),
    )
    candidate_id = "candidate-typed-v2-0001"
    width, height = intent.canvas.image_size
    attempt_id = (
        "attempt-v2-transient-replay"
        if with_renderer_attempt
        else "attempt-v2-rendered-fixture"
    )
    plan_items = [
        RenderPlanItemV2(
            logical_request_ordinal=index + 1,
            profile="beauty_full_v1",
            compilation_ref=compilation_ref,
            source_ref=compilation.glsl_ref,
            width=width,
            height=height,
            beauty_capture_index=index,
        )
        for index in range(5)
    ]
    for diagnostic_pass in diagnostic.passes:
        diagnostic_width, diagnostic_height = rendered_structure_diagnostic_size_v2(
            pass_kind=diagnostic_pass.pass_kind,
            width=width,
            height=height,
        )
        profile = (
            "subject_visible_delta_full_v1"
            if diagnostic_pass.pass_kind == "subject_visible_delta"
            else (
                "instance_visible_delta_full_v1"
                if diagnostic_pass.pass_kind == "instance_visible_delta"
                else "layer_visible_delta_lowres_v1"
            )
        )
        plan_items.append(
            RenderPlanItemV2(
                logical_request_ordinal=len(plan_items) + 1,
                profile=profile,
                compilation_ref=diagnostic_ref,
                source_ref=diagnostic_pass.source_ref,
                width=diagnostic_width,
                height=diagnostic_height,
                diagnostic_pass_id=diagnostic_pass.pass_id,
            )
        )
    plan_payload = {
        "schema_version": "renderer_plan_v3",
        "hash_version": "renderer_plan_hash_v3",
        "run_id": RUN_ID,
        "attempt_id": attempt_id,
        "target_hypothesis_hash": intent.target_hypothesis_hash,
        "semantic_genome_hash": hashes.semantic_genome_hash,
        "budget_policy_hash": "f" * 64,
        "ownership_policy_version": diagnostic.ownership_policy_version,
        "items": tuple(plan_items),
        "plan_hash": "0" * 64,
    }
    plan_payload["plan_hash"] = compute_render_plan_hash(plan_payload)
    plan = RenderPlanV2.model_validate(plan_payload, strict=True)
    plan_ref = materialize_render_model(catalog=catalog, run_id=RUN_ID, value=plan)

    environment_payload = {
        "renderer_version": "fixture-renderer-v2.4",
        "browser_version": "fixture-browser-v2.4",
        "gl_version": "WebGL 1 fixture",
        "glsl_version": "WebGL GLSL ES 1.00 fixture",
        "gl_vendor": "fixture-vendor",
        "gl_renderer": "fixture-device",
        "webgl_context_kind": "webgl1",
        "canvas_alpha": False,
        "canvas_antialias": False,
        "canvas_depth": False,
        "canvas_stencil": False,
        "canvas_alpha_mode": "force_opaque_alpha_v1",
        "canvas_clear_color_rgba": (1.0, 1.0, 1.0, 1.0),
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
    environment_ref = _put(
        catalog,
        kind="renderer_environment",
        schema_version="renderer_environment_receipt_v3",
        content_type="application/json",
        data=environment.model_dump_json().encode("utf-8"),
    )

    subject_mask_ref = next(ref for ref in catalog.list_refs() if ref.kind == "subject_mask")
    with Image.open(BytesIO(catalog.read_bytes(subject_mask_ref.artifact_id))) as image:
        mask = image.convert("L")
        rgba = Image.new("RGBA", image.size, (0, 0, 0, 0))
        rgba.putalpha(mask)
        diagnostic_png = BytesIO()
        rgba.save(diagnostic_png, format="PNG")
    beauty_image = Image.new("RGBA", (width, height), (17, 23, 31, 255))
    beauty_png = BytesIO()
    beauty_image.save(beauty_png, format="PNG")
    beauty_ref = _put(
        catalog,
        kind=RENDER_ARTIFACT_KIND,
        schema_version=RENDER_ARTIFACT_SCHEMA_VERSION,
        content_type="image/png",
        data=beauty_png.getvalue(),
    )

    request_refs: list[ArtifactRefV2] = []
    evidence_refs: list[ArtifactRefV2] = []
    outcomes: list[RenderCallOutcomeV2] = []
    diagnostic_receipts: list[DiagnosticRenderReceiptV3] = []
    budget_revision = 1
    for item in plan.items:
        request_payload = {
            "schema_version": "renderer_request_receipt_v2",
            "hash_version": "renderer_request_hash_v2",
            "run_id": RUN_ID,
            "attempt_id": attempt_id,
            "target_hypothesis_hash": intent.target_hypothesis_hash,
            "semantic_genome_hash": hashes.semantic_genome_hash,
            "compilation_ref": item.compilation_ref,
            "glsl_ref": item.source_ref,
            "render_profile": item.profile,
            "logical_request_ordinal": item.logical_request_ordinal,
            "beauty_capture_index": item.beauty_capture_index,
            "diagnostic_pass_id": item.diagnostic_pass_id,
            "width": item.width,
            "height": item.height,
            "request_hash": "0" * 64,
        }
        request_payload["request_hash"] = compute_renderer_request_hash(request_payload)
        request = RendererRequestReceiptV2.model_validate(
            request_payload, strict=True
        )
        request_ref = materialize_renderer_request(
            catalog=catalog, run_id=RUN_ID, receipt=request
        )
        request_refs.append(request_ref)
        if with_renderer_attempt and item.logical_request_ordinal == 1:
            transient_ref = materialize_attempt_evidence(
                catalog=catalog,
                run_id=RUN_ID,
                evidence=CandidateAttemptEvidenceV1(
                    run_id=RUN_ID,
                    attempt_id=attempt_id,
                    target_hypothesis_hash=intent.target_hypothesis_hash,
                    semantic_genome_hash=hashes.semantic_genome_hash,
                    stage="render",
                    outcome="transient_failure",
                    error_code="renderer_transient_unavailable",
                    renderer_request_hash=request.request_hash,
                    call_ordinal=1,
                ),
            )
            evidence_refs.append(transient_ref)
            outcomes.append(
                RenderCallOutcomeV2(
                    logical_request_ordinal=item.logical_request_ordinal,
                    physical_call_ordinal=1,
                    renderer_request_ref=request_ref,
                    renderer_request_artifact_sha256=request_ref.sha256,
                    renderer_request_hash=request.request_hash,
                    outcome="transient_failure",
                    error_code="renderer_transient_unavailable",
                    attempt_evidence_ref=transient_ref,
                    budget_revision_reserved=budget_revision,
                    budget_revision_committed=budget_revision + 1,
                )
            )
            budget_revision += 2
            physical_ordinal = 2
        else:
            physical_ordinal = 1
        success_ref = materialize_attempt_evidence(
            catalog=catalog,
            run_id=RUN_ID,
            evidence=CandidateAttemptEvidenceV1(
                run_id=RUN_ID,
                attempt_id=attempt_id,
                target_hypothesis_hash=intent.target_hypothesis_hash,
                semantic_genome_hash=hashes.semantic_genome_hash,
                stage="render",
                outcome="success",
                error_code=None,
                renderer_request_hash=request.request_hash,
                call_ordinal=physical_ordinal,
            ),
        )
        evidence_refs.append(success_ref)
        if item.profile == "beauty_full_v1":
            render_ref = beauty_ref
        else:
            render_ref = _put(
                catalog,
                kind="diagnostic_render_png",
                schema_version="diagnostic_render_png_v3",
                content_type="image/png",
                data=diagnostic_png.getvalue(),
            )
        outcomes.append(
            RenderCallOutcomeV2(
                logical_request_ordinal=item.logical_request_ordinal,
                physical_call_ordinal=physical_ordinal,
                renderer_request_ref=request_ref,
                renderer_request_artifact_sha256=request_ref.sha256,
                renderer_request_hash=request.request_hash,
                outcome="success",
                renderer_environment_ref=environment_ref,
                renderer_environment_artifact_sha256=environment_ref.sha256,
                renderer_environment_hash=environment.environment_hash,
                render_ref=render_ref,
                render_sha256=render_ref.sha256,
                attempt_evidence_ref=success_ref,
                budget_revision_reserved=budget_revision,
                budget_revision_committed=budget_revision + 1,
            )
        )
        budget_revision += 2
        if item.diagnostic_pass_id is not None:
            diagnostic_pass = next(
                value
                for value in diagnostic.passes
                if value.pass_id == item.diagnostic_pass_id
            )
            diagnostic_receipts.append(
                DiagnosticRenderReceiptV3(
                    pass_id=diagnostic_pass.pass_id,
                    pass_kind=diagnostic_pass.pass_kind,
                    canonical_node_id=diagnostic_pass.canonical_node_id,
                    ownership_policy_version=(
                        diagnostic_pass.ownership_policy_version
                    ),
                    source_ref=diagnostic_pass.source_ref,
                    source_sha256=diagnostic_pass.source_sha256,
                    instance_index=diagnostic_pass.instance_index,
                    layer=diagnostic_pass.layer,
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

    progress_payload = {
        "schema_version": "renderer_progress_v2",
        "hash_version": "renderer_progress_hash_v2",
        "run_id": RUN_ID,
        "attempt_id": attempt_id,
        "plan_ref": plan_ref,
        "plan_hash": plan.plan_hash,
        "budget_policy_hash": "f" * 64,
        "outcomes": tuple(outcomes),
        "record_hash": "0" * 64,
    }
    progress_payload["record_hash"] = compute_render_progress_hash(progress_payload)
    progress = RenderProgressV2.model_validate(progress_payload, strict=True)
    progress_ref = materialize_render_model(
        catalog=catalog, run_id=RUN_ID, value=progress
    )
    successful = tuple(item for item in outcomes if item.outcome == "success")
    beauty_outcomes = successful[:5]
    repeatability = build_repeatability_evidence(
        run_id=RUN_ID,
        attempt_id=attempt_id,
        capture_request_refs=tuple(item.renderer_request_ref for item in beauty_outcomes),
        capture_render_refs=tuple(item.render_ref for item in beauty_outcomes if item.render_ref),
        renderer_environment_ref=environment_ref,
        resolver=catalog,
    )
    repeatability_ref = materialize_render_model(
        catalog=catalog, run_id=RUN_ID, value=repeatability
    )
    primary = beauty_outcomes[0]
    assert primary.render_ref is not None
    evidence_payload = {
        "run_id": RUN_ID,
        "candidate_id": candidate_id,
        "intent_id": intent.intent_id,
        "intent_ref": intent_ref,
        "intent_sha256": intent_ref.sha256,
        "target_hypothesis_id": intent.target_hypothesis_id,
        "target_hypothesis_hash": intent.target_hypothesis_hash,
        "genome_id": genome.genome_id,
        "genome_ref": genome_ref,
        "genome_sha256": genome_ref.sha256,
        "semantic_genome_hash": hashes.semantic_genome_hash,
        "ownership_policy_version": diagnostic.ownership_policy_version,
        "compilation_ref": compilation_ref,
        "compilation_sha256": compilation_ref.sha256,
        "diagnostic_compilation_ref": diagnostic_ref,
        "diagnostic_compilation_sha256": diagnostic_ref.sha256,
        "beauty_renderer_request_ref": primary.renderer_request_ref,
        "beauty_renderer_request_artifact_sha256": primary.renderer_request_ref.sha256,
        "beauty_renderer_request_hash": primary.renderer_request_hash,
        "renderer_environment_ref": environment_ref,
        "renderer_environment_artifact_sha256": environment_ref.sha256,
        "renderer_environment_hash": environment.environment_hash,
        "beauty_render_ref": primary.render_ref,
        "beauty_render_sha256": primary.render_ref.sha256,
        "diagnostic_receipts": tuple(diagnostic_receipts),
        "record_hash": "0" * 64,
    }
    evidence_payload["record_hash"] = compute_rendered_structure_evidence_hash(
        evidence_payload
    )
    rendered_evidence = RenderedStructureEvidenceV4.model_validate(
        evidence_payload, strict=True
    )
    rendered_evidence_ref = _put(
        catalog,
        kind="rendered_structure_evidence",
        schema_version="rendered_structure_evidence_v4",
        content_type="application/json",
        data=rendered_evidence.model_dump_json().encode("utf-8"),
    )
    verification = verify_rendered_structure_evidence(
        rendered_evidence,
        resolver=catalog,
        intent=intent,
        genome=genome,
        compilation_bundle=compilation,
        diagnostic_bundle=diagnostic,
    )
    if verification.status != "structure_verified":
        raise ValueError(f"hard constraint closure: {verification.reason_codes}")
    verification_ref = _put(
        catalog,
        kind="rendered_structure_verification",
        schema_version="rendered_structure_verification_v4",
        content_type="application/json",
        data=verification.model_dump_json().encode("utf-8"),
    )
    constraint_evaluation = evaluate_intent_genome_constraints_v3(
        intent,
        genome,
        product,
        candidate_id=candidate_id,
        target_measurements_ref=next(
            ref for ref in catalog.list_refs() if ref.kind == "target_measurements"
        ),
        intent_ref=intent_ref,
        genome_ref=genome_ref,
        compilation_ref=compilation_ref,
        rendered_structure_evidence_ref=rendered_evidence_ref,
        rendered_structure_evidence=rendered_evidence,
        rendered_structure_verification_ref=verification_ref,
        rendered_structure_verification=verification,
    )
    constraint_evaluation_ref = _put(
        catalog,
        kind=CONSTRAINT_EVALUATION_ARTIFACT_KIND,
        schema_version=TYPED_CONSTRAINT_EVALUATION_ARTIFACT_SCHEMA_VERSION,
        content_type="application/json",
        data=constraint_evaluation.model_dump_json().encode("utf-8"),
    )
    evaluation_refs = []
    for render_ref in tuple(item.render_ref for item in beauty_outcomes):
        assert render_ref is not None
        evaluation = with_basic_evaluation_record_hash(
            {
                "schema_version": "basic_evaluation_record_v2",
                "hash_version": "basic_evaluation_record_hash_v2",
                "run_id": RUN_ID,
                "candidate_id": candidate_id,
                "intent_id": intent.intent_id,
                "target_hypothesis_hash": intent.target_hypothesis_hash,
                "genome_id": genome.genome_id,
                "semantic_genome_hash": hashes.semantic_genome_hash,
                "compilation_sha256": compilation_ref.sha256,
                "glsl_sha256": compilation.glsl_ref.sha256,
                "render_ref": render_ref,
                "render_sha256": render_ref.sha256,
                "metric_version": "basic_image_metrics_v2_test",
                "total_loss": 0.1,
                "global_rmse": 0.1,
                "edge_loss": 0.1,
                "geometry_loss": 0.1,
                "alpha_loss": 0.1,
                "diagnostics": (),
                "record_hash": "0" * 64,
            }
        )
        evaluation_refs.append(
            _put(
                catalog,
                kind=EVALUATION_ARTIFACT_KIND,
                schema_version=TYPED_EVALUATION_ARTIFACT_SCHEMA_VERSION,
                content_type="application/json",
                data=evaluation.model_dump_json().encode("utf-8"),
            )
        )
    return materialize_typed_candidate_artifacts(
        catalog=catalog,
        run_id=RUN_ID,
        candidate_input=CandidateMaterializationInputV2(
            run_id=RUN_ID,
            candidate_id=candidate_id,
            parent_candidate_id=None,
            origin="deterministic",
            generator_id="effect-genome-expander",
            generator_version=generator_version,
            target_hypothesis_id=intent.target_hypothesis_id,
            target_hypothesis_hash=intent.target_hypothesis_hash,
            constraint_set_hash=intent.constraint_set_hash,
            intent_ref=intent_ref,
            genome_ref=genome_ref,
            topology_hash=hashes.topology_hash,
            parameter_layout_hash=hashes.parameter_layout_hash,
            semantic_genome_hash=hashes.semantic_genome_hash,
            compilation_ref=compilation_ref,
            diagnostic_compilation_ref=diagnostic_ref,
            glsl_ref=compilation.glsl_ref,
            render_refs=tuple(item.render_ref for item in beauty_outcomes),
            render_plan_ref=plan_ref,
            render_progress_ref=progress_ref,
            render_repeatability_ref=repeatability_ref,
            rendered_structure_evidence_ref=rendered_evidence_ref,
            rendered_structure_verification_ref=verification_ref,
            constraint_evaluation_ref=constraint_evaluation_ref,
            evaluation_refs=tuple(evaluation_refs),
            attempt_id=attempt_id,
            renderer_request_refs=tuple(request_refs),
            attempt_evidence_refs=tuple(evidence_refs),
        ),
    )


def test_typed_candidate_replays_full_closure_and_unlocks_runtime_adapter(
    tmp_path: Path,
) -> None:
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
    )
    structure = materialize_runtime_target_structure_artifacts(
        catalog=catalog,
        run_id=RUN_ID,
        evidence=evidence,
    )
    candidate = _typed_candidate(catalog, intent_source_ref=evidence.intent_ref)

    recovered = load_typed_candidate_artifacts(
        candidate.candidate_ref,
        resolver=catalog,
        run_id=RUN_ID,
    )
    dispatched = load_candidate_artifact_bundle(
        candidate.candidate_ref,
        resolver=catalog,
        run_id=RUN_ID,
    )
    trusted = load_trusted_runtime_selector_input(
        structure.envelope_ref,
        candidate.candidate_ref,
        resolver=catalog,
        run_id=RUN_ID,
    )

    assert recovered == candidate
    assert dispatched == candidate
    assert recovered.semantic_validation_status == (
        "admissible_v2_4_rendered_structure_verified"
    )
    assert recovered.provenance.downstream_semantic_validation == (
        "typed_candidate_semantics_v2_4_rendered_structure"
    )
    constraint = recovered.constraint_evaluation
    assert constraint.schema_version == "intent_constraint_evaluation_v3"
    assert constraint.target_structure_status == "rendered_structure_verified"
    assert constraint.candidate_id == recovered.candidate.candidate_id
    assert constraint.intent_ref == recovered.candidate.intent_ref
    assert constraint.genome_ref == recovered.candidate.genome_ref
    assert constraint.compilation_ref == recovered.candidate.compilation_ref
    assert (
        constraint.rendered_structure_evidence_ref
        == recovered.candidate.rendered_structure_evidence_ref
    )
    assert (
        constraint.rendered_structure_verification_ref
        == recovered.candidate.rendered_structure_verification_ref
    )
    assert constraint.target_measurements_ref in recovered.content_verified_refs
    projection = recovered.rendered_closure_projection
    assert projection.candidate_record_hash == recovered.candidate.record_hash
    assert projection.logical_request_count == len(recovered.render_plan.items)
    assert projection.physical_call_count == len(recovered.render_progress.outcomes)
    assert projection.replay_count == 0
    assert projection.beauty_request_hashes == tuple(
        item.renderer_request_hash
        for item in recovered.render_progress.outcomes[:5]
    )
    closure_ids = {item.artifact_id for item in recovered.content_verified_refs}
    assert all(
        ref.artifact_id in closure_ids
        for outcome in recovered.render_progress.outcomes
        for ref in (
            outcome.renderer_request_ref,
            outcome.attempt_evidence_ref,
            outcome.renderer_environment_ref,
            outcome.render_ref,
        )
        if ref is not None
    )
    assert all(
        item.source_ref.artifact_id in closure_ids
        for item in recovered.diagnostic_compilation_bundle.passes
    )
    assert trusted.candidate_id == candidate.candidate.candidate_id


def _runtime_admission_decision(
    candidate: TypedCandidateArtifactBundleV2,
    trusted: TrustedRuntimeSelectorInput,
    *,
    candidate_glsl_sha256: str | None = None,
) -> GeneratorAdmissionDecision:
    record = candidate.candidate
    provenance = candidate.provenance
    render_ref = record.render_refs[0]
    return decide_trusted_runtime_admission(
        candidate_id=record.candidate_id,
        candidate_glsl_sha256=candidate_glsl_sha256 or record.glsl_ref.sha256,
        candidate_glsl_ref=record.glsl_ref.artifact_id,
        candidate_render_sha256=render_ref.sha256,
        candidate_render_ref=render_ref.artifact_id,
        candidate_provenance_ref=record.provenance_ref.artifact_id,
        candidate_origin=provenance.origin,
        candidate_generator_version=provenance.generator_version,
        trusted_input=trusted,
        policy=MeasurementSeedAdmissionPolicy(),
    )


def test_real_typed_runtime_admission_matrix_is_fail_closed(tmp_path: Path) -> None:
    """真实 Artifact 闭包只准入 capability 内的 expander 候选。."""
    supported_catalog, supported_evidence = _build_evidence(
        tmp_path / "supported",
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
    )
    supported_structure = materialize_runtime_target_structure_artifacts(
        catalog=supported_catalog,
        run_id=RUN_ID,
        evidence=supported_evidence,
    )
    supported_candidate = _typed_candidate(
        supported_catalog,
        intent_source_ref=supported_evidence.intent_ref,
    )
    supported_trusted = load_trusted_runtime_selector_input(
        supported_structure.envelope_ref,
        supported_candidate.candidate_ref,
        resolver=supported_catalog,
        run_id=RUN_ID,
    )

    unknown_catalog, unknown_evidence = _build_evidence(
        tmp_path / "unknown",
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
    )
    unknown_structure = materialize_runtime_target_structure_artifacts(
        catalog=unknown_catalog,
        run_id=RUN_ID,
        evidence=unknown_evidence,
    )
    unknown_candidate = _typed_candidate(
        unknown_catalog,
        intent_source_ref=unknown_evidence.intent_ref,
        generator_version="unknown_expander_v9",
    )
    unknown_trusted = load_trusted_runtime_selector_input(
        unknown_structure.envelope_ref,
        unknown_candidate.candidate_ref,
        resolver=unknown_catalog,
        run_id=RUN_ID,
    )

    unsupported_catalog, unsupported_evidence = _build_evidence(
        tmp_path / "unsupported",
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
        include_highlight=True,
    )
    unsupported_structure = materialize_runtime_target_structure_artifacts(
        catalog=unsupported_catalog,
        run_id=RUN_ID,
        evidence=unsupported_evidence,
    )
    unsupported_candidate = _typed_candidate(
        unsupported_catalog,
        intent_source_ref=unsupported_evidence.intent_ref,
        generator_version="measurement_affine_seed_v1",
    )
    unsupported_trusted = load_trusted_runtime_selector_input(
        unsupported_structure.envelope_ref,
        unsupported_candidate.candidate_ref,
        resolver=unsupported_catalog,
        run_id=RUN_ID,
    )

    admitted = _runtime_admission_decision(supported_candidate, supported_trusted)
    unknown = _runtime_admission_decision(unknown_candidate, unknown_trusted)
    unsupported = _runtime_admission_decision(
        unsupported_candidate,
        unsupported_trusted,
    )
    identity_mismatch = _runtime_admission_decision(
        supported_candidate,
        supported_trusted,
        candidate_glsl_sha256="f" * 64,
    )

    assert admitted.status == "admitted"
    assert admitted.reason_codes == ("labels_within_generator_capability",)
    assert unknown.status == "unknown"
    assert unknown.reason_codes == ("unknown_deterministic_generator",)
    assert unsupported.status == "unsupported"
    assert unsupported.reason_codes == (
        "required_layers_exceed_generator_capability",
    )
    assert identity_mismatch.status == "unknown"
    assert identity_mismatch.reason_codes == (
        "generator_admission_identity_mismatch",
    )


def test_typed_candidate_root_recovers_transient_replay_receipts_and_rejects_tamper(
    tmp_path: Path,
) -> None:
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
    )
    candidate = _typed_candidate(
        catalog,
        intent_source_ref=evidence.intent_ref,
        with_renderer_attempt=True,
    )

    recovered = load_typed_candidate_artifacts(
        candidate.candidate_ref,
        resolver=catalog,
        run_id=RUN_ID,
    )

    assert recovered.provenance.attempt_id == "attempt-v2-transient-replay"
    assert len(recovered.provenance.renderer_request_refs) == len(
        recovered.render_plan.items
    )
    assert len(recovered.provenance.attempt_evidence_refs) == (
        len(recovered.render_plan.items) + 1
    )
    assert all(
        ref in recovered.content_verified_refs
        for ref in (
            *recovered.provenance.renderer_request_refs,
            *recovered.provenance.attempt_evidence_refs,
        )
    )

    run = LocalArtifactStore(tmp_path).start_run("project-v2", RUN_ID)
    manifest = json.loads(run.read_bytes(".artifact-catalog-v2/manifest.json"))
    first_evidence_ref = recovered.provenance.attempt_evidence_refs[0]
    entry = manifest["artifacts"][first_evidence_ref.artifact_id]
    run.write_bytes(entry["relative_path"], b'{"tampered":true}')

    with pytest.raises(ValueError, match="size|SHA-256"):
        load_typed_candidate_artifacts(
            candidate.candidate_ref,
            resolver=catalog,
            run_id=RUN_ID,
        )


def test_typed_candidate_rejects_nested_compiler_artifact_tampering(
    tmp_path: Path,
) -> None:
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
    )
    candidate = _typed_candidate(catalog, intent_source_ref=evidence.intent_ref)
    run = LocalArtifactStore(tmp_path).start_run("project-v2", RUN_ID)
    manifest = json.loads(run.read_bytes(".artifact-catalog-v2/manifest.json"))
    ast_ref = candidate.compilation_bundle.ast_ref
    entry = manifest["artifacts"][ast_ref.artifact_id]
    run.write_bytes(entry["relative_path"], b'{"tampered":true}')

    with pytest.raises(ValueError, match="size|SHA-256"):
        load_typed_candidate_artifacts(
            candidate.candidate_ref,
            resolver=catalog,
            run_id=RUN_ID,
        )


def test_typed_candidate_rejects_reencoded_verification_ref_with_valid_hashes(
    tmp_path: Path,
) -> None:
    """即使正文等价并重算 Candidate hash，Evaluation V3 receipt ref 也不得漂移。"""
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
    )
    candidate = _typed_candidate(catalog, intent_source_ref=evidence.intent_ref)

    old_verification = candidate.rendered_structure_verification.model_dump(
        mode="json"
    )
    old_verification["record_hash"] = compute_rendered_structure_verification_hash(
        old_verification
    )
    old_verification_ref = _put(
        catalog,
        kind="rendered_structure_verification",
        schema_version="rendered_structure_verification_v4",
        content_type="application/json",
        data=json.dumps(
            old_verification,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
    )

    forged_candidate_payload = {
        name: getattr(candidate.candidate, name)
        for name in CandidateRecordV2.model_fields
    }
    forged_candidate_payload["rendered_structure_verification_ref"] = (
        old_verification_ref
    )
    forged_candidate_payload["record_hash"] = compute_candidate_record_hash(
        forged_candidate_payload
    )
    forged_candidate = CandidateRecordV2.model_validate(
        forged_candidate_payload,
        strict=True,
    )
    forged_candidate_ref = _put(
        catalog,
        kind="candidate_record",
        schema_version="candidate_record_v3",
        content_type="application/json",
        data=forged_candidate.model_dump_json().encode("utf-8"),
    )

    with pytest.raises(ValueError, match="IntentConstraintEvaluation"):
        load_typed_candidate_artifacts(
            forged_candidate_ref,
            resolver=catalog,
            run_id=RUN_ID,
        )


def test_typed_candidate_rejects_hashed_basic_evaluation_identity_tampering(
    tmp_path: Path,
) -> None:
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
    )
    candidate = _typed_candidate(catalog, intent_source_ref=evidence.intent_ref)
    evaluation = candidate.basic_evaluations[0]
    forged = with_basic_evaluation_record_hash(
        evaluation.model_copy(update={"candidate_id": "another-candidate"})
    )
    forged_ref = _put(
        catalog,
        kind=EVALUATION_ARTIFACT_KIND,
        schema_version=TYPED_EVALUATION_ARTIFACT_SCHEMA_VERSION,
        content_type="application/json",
        data=forged.model_dump_json().encode("utf-8"),
    )
    record = candidate.candidate
    provenance = candidate.provenance
    with pytest.raises(ValueError, match="BasicEvaluation"):
        materialize_typed_candidate_artifacts(
            catalog=catalog,
            run_id=RUN_ID,
            candidate_input=CandidateMaterializationInputV2(
                run_id=record.run_id,
                candidate_id=record.candidate_id,
                parent_candidate_id=record.parent_candidate_id,
                origin=provenance.origin,
                generator_id=provenance.generator_id,
                generator_version=provenance.generator_version,
                target_hypothesis_id=record.target_hypothesis_id,
                target_hypothesis_hash=record.target_hypothesis_hash,
                constraint_set_hash=record.constraint_set_hash,
                intent_ref=record.intent_ref,
                genome_ref=record.genome_ref,
                topology_hash=record.topology_hash,
                parameter_layout_hash=record.parameter_layout_hash,
                semantic_genome_hash=record.semantic_genome_hash,
                compilation_ref=record.compilation_ref,
                diagnostic_compilation_ref=record.diagnostic_compilation_ref,
                glsl_ref=record.glsl_ref,
                render_refs=record.render_refs,
                render_plan_ref=record.render_plan_ref,
                render_progress_ref=record.render_progress_ref,
                render_repeatability_ref=record.render_repeatability_ref,
                rendered_structure_evidence_ref=(
                    record.rendered_structure_evidence_ref
                ),
                rendered_structure_verification_ref=(
                    record.rendered_structure_verification_ref
                ),
                constraint_evaluation_ref=record.constraint_evaluation_ref,
                evaluation_refs=(forged_ref, *record.evaluation_refs[1:]),
                attempt_id=provenance.attempt_id,
                renderer_request_refs=provenance.renderer_request_refs,
                attempt_evidence_refs=provenance.attempt_evidence_refs,
            ),
        )


def test_typed_candidate_rejects_forged_v3_receipt_candidate_binding(
    tmp_path: Path,
) -> None:
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
    )
    candidate = _typed_candidate(catalog, intent_source_ref=evidence.intent_ref)
    constraint = candidate.constraint_evaluation
    forged_payload = {
        name: getattr(constraint, name)
        for name in IntentConstraintEvaluationV3.model_fields
    }
    forged_payload["candidate_id"] = "candidate-forged-v3"
    forged_payload["record_hash"] = compute_intent_constraint_evaluation_hash_v3(
        forged_payload
    )
    forged = IntentConstraintEvaluationV3.model_validate(
        forged_payload, strict=True
    )
    forged_ref = _put(
        catalog,
        kind=CONSTRAINT_EVALUATION_ARTIFACT_KIND,
        schema_version=TYPED_CONSTRAINT_EVALUATION_ARTIFACT_SCHEMA_VERSION,
        content_type="application/json",
        data=forged.model_dump_json().encode("utf-8"),
    )
    record = candidate.candidate
    provenance = candidate.provenance
    with pytest.raises(ValueError, match="IntentConstraintEvaluation"):
        materialize_typed_candidate_artifacts(
            catalog=catalog,
            run_id=RUN_ID,
            candidate_input=CandidateMaterializationInputV2(
                run_id=record.run_id,
                candidate_id=record.candidate_id,
                parent_candidate_id=record.parent_candidate_id,
                origin=provenance.origin,
                generator_id=provenance.generator_id,
                generator_version=provenance.generator_version,
                target_hypothesis_id=record.target_hypothesis_id,
                target_hypothesis_hash=record.target_hypothesis_hash,
                constraint_set_hash=record.constraint_set_hash,
                intent_ref=record.intent_ref,
                genome_ref=record.genome_ref,
                topology_hash=record.topology_hash,
                parameter_layout_hash=record.parameter_layout_hash,
                semantic_genome_hash=record.semantic_genome_hash,
                compilation_ref=record.compilation_ref,
                diagnostic_compilation_ref=record.diagnostic_compilation_ref,
                glsl_ref=record.glsl_ref,
                render_refs=record.render_refs,
                render_plan_ref=record.render_plan_ref,
                render_progress_ref=record.render_progress_ref,
                render_repeatability_ref=record.render_repeatability_ref,
                rendered_structure_evidence_ref=(
                    record.rendered_structure_evidence_ref
                ),
                rendered_structure_verification_ref=(
                    record.rendered_structure_verification_ref
                ),
                constraint_evaluation_ref=forged_ref,
                evaluation_refs=record.evaluation_refs,
                attempt_id=provenance.attempt_id,
                renderer_request_refs=provenance.renderer_request_refs,
                attempt_evidence_refs=provenance.attempt_evidence_refs,
            ),
        )


def test_opaque_candidate_can_never_unlock_runtime_adapter(tmp_path: Path) -> None:
    from tests.unit_tests.test_runtime_admission_adapter import _candidate_bundle

    catalog, evidence = _build_evidence(
        tmp_path,
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
    )
    structure = materialize_runtime_target_structure_artifacts(
        catalog=catalog,
        run_id=RUN_ID,
        evidence=evidence,
    )
    candidate = _candidate_bundle(catalog, intent_source_ref=evidence.intent_ref)
    with pytest.raises(RuntimeAdmissionRejected) as caught:
        load_trusted_runtime_selector_input(
            structure.envelope_ref,
            candidate.candidate_ref,
            resolver=catalog,
            run_id=RUN_ID,
        )
    assert caught.value.code == "runtime_candidate_typed_semantics_not_verified"


def test_ring_target_with_non_exact_instance_receipt_is_not_admissible(
    tmp_path: Path,
) -> None:
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="ring",
        hole_count=1,
        mask_has_hole=True,
    )

    with pytest.raises(ValueError, match="hard constraint closure"):
        _typed_candidate(catalog, intent_source_ref=evidence.intent_ref)

    intent = IntentIR.model_validate_json(
        catalog.read_bytes(evidence.intent_ref.artifact_id), strict=True
    )
    genome = expand_seed_plans(intent, random_seed=17).expanded_seeds[0].genome
    assert isinstance(genome, TypedEffectGenome)
    static = evaluate_intent_genome_constraints(
        intent, genome, compile_effect_genome(genome)
    )
    assert static.target_structure_status == "unsupported"
    assert not static.hard_constraints_passed
