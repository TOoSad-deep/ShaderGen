from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shaderforge.contracts import AcceptancePolicy
from shaderforge.evaluation import (
    CandidateRecord,
    MeasurementSeedAdmissionPolicy,
    ScoreBreakdownV1,
    materialize_runtime_target_structure_artifacts,
    select_current_best,
)
from shaderforge.evaluation.candidate_artifacts import (
    COMPILATION_ARTIFACT_KIND,
    COMPILATION_ARTIFACT_SCHEMA_VERSION,
    CONSTRAINT_EVALUATION_ARTIFACT_KIND,
    CONSTRAINT_EVALUATION_ARTIFACT_SCHEMA_VERSION,
    EVALUATION_ARTIFACT_KIND,
    EVALUATION_ARTIFACT_SCHEMA_VERSION,
    GENOME_ARTIFACT_KIND,
    GENOME_ARTIFACT_SCHEMA_VERSION,
    GLSL_ARTIFACT_KIND,
    GLSL_ARTIFACT_SCHEMA_VERSION,
    INTENT_ARTIFACT_KIND,
    INTENT_ARTIFACT_SCHEMA_VERSION,
    RENDER_ARTIFACT_KIND,
    RENDER_ARTIFACT_SCHEMA_VERSION,
    CandidateArtifactBundleV2,
    CandidateMaterializationInputV2,
    materialize_candidate_artifacts,
)
from shaderforge.evaluation.runtime_admission import (
    RuntimeAdmissionRejected,
    TrustedRuntimeSelectorInput,
    VerifiedRuntimeStructureAdmission,
    load_trusted_runtime_selector_input,
    load_verified_runtime_structure_admission,
)
from shaderforge.genome import compute_genome_hashes
from shaderforge.intent.ir import IntentIR
from shaderforge.store import ArtifactRefV2, LocalArtifactCatalog, LocalArtifactStore
from tests.fixtures.png_to_shader_v2_contracts import make_genome
from tests.unit_tests.test_candidate_artifact_recovery import _png_bytes
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


def _candidate_bundle(
    catalog: LocalArtifactCatalog,
    *,
    intent_source_ref: ArtifactRefV2,
) -> CandidateArtifactBundleV2:
    intent_bytes = catalog.read_bytes(intent_source_ref.artifact_id)
    intent = IntentIR.model_validate_json(intent_bytes, strict=True)
    base_genome = make_genome()
    genome = base_genome.model_copy(
        update={
            "provenance": base_genome.provenance.model_copy(
                update={
                    "intent_id": intent.intent_id,
                    "target_hypothesis_id": intent.target_hypothesis_id,
                    "target_hypothesis_hash": intent.target_hypothesis_hash,
                }
            )
        }
    )
    hashes = compute_genome_hashes(genome)
    intent_ref = _put(
        catalog,
        kind=INTENT_ARTIFACT_KIND,
        schema_version=INTENT_ARTIFACT_SCHEMA_VERSION,
        content_type="application/json",
        data=intent_bytes,
    )
    genome_ref = _put(
        catalog,
        kind=GENOME_ARTIFACT_KIND,
        schema_version=GENOME_ARTIFACT_SCHEMA_VERSION,
        content_type="application/json",
        data=genome.model_dump_json().encode(),
    )
    compilation_ref = _put(
        catalog,
        kind=COMPILATION_ARTIFACT_KIND,
        schema_version=COMPILATION_ARTIFACT_SCHEMA_VERSION,
        content_type="application/json",
        data=b'{"opaque":"typed CompilationBundleV2 pending"}',
    )
    diagnostic_compilation_ref = _put(
        catalog,
        kind="diagnostic_compilation_bundle",
        schema_version="diagnostic_compilation_bundle_v3",
        content_type="application/json",
        data=b'{"opaque":"typed diagnostics pending"}',
    )
    glsl_ref = _put(
        catalog,
        kind=GLSL_ARTIFACT_KIND,
        schema_version=GLSL_ARTIFACT_SCHEMA_VERSION,
        content_type="text/plain; charset=utf-8",
        data=b"void main(){gl_FragColor=vec4(1.0);}",
    )
    render_ref = _put(
        catalog,
        kind=RENDER_ARTIFACT_KIND,
        schema_version=RENDER_ARTIFACT_SCHEMA_VERSION,
        content_type="image/png",
        data=_png_bytes(),
    )
    constraint_evaluation_ref = _put(
        catalog,
        kind=CONSTRAINT_EVALUATION_ARTIFACT_KIND,
        schema_version=CONSTRAINT_EVALUATION_ARTIFACT_SCHEMA_VERSION,
        content_type="application/json",
        data=b'{"opaque":"typed IntentConstraintEvaluationV2 pending"}',
    )
    evaluation_ref = _put(
        catalog,
        kind=EVALUATION_ARTIFACT_KIND,
        schema_version=EVALUATION_ARTIFACT_SCHEMA_VERSION,
        content_type="application/json",
        data=b'{"opaque":"typed BasicEvaluationRecordV2 pending"}',
    )
    render_plan_ref = _put(
        catalog,
        kind="renderer_plan",
        schema_version="renderer_plan_v3",
        content_type="application/json",
        data=b'{"opaque":"render plan pending"}',
    )
    render_progress_ref = _put(
        catalog,
        kind="renderer_progress",
        schema_version="renderer_progress_v2",
        content_type="application/json",
        data=b'{"opaque":"render progress pending"}',
    )
    render_repeatability_ref = _put(
        catalog,
        kind="render_repeatability_evidence",
        schema_version="render_repeatability_evidence_v2",
        content_type="application/json",
        data=b'{"opaque":"repeatability pending"}',
    )
    rendered_structure_evidence_ref = _put(
        catalog,
        kind="rendered_structure_evidence",
        schema_version="rendered_structure_evidence_v4",
        content_type="application/json",
        data=b'{"opaque":"rendered evidence pending"}',
    )
    rendered_structure_verification_ref = _put(
        catalog,
        kind="rendered_structure_verification",
        schema_version="rendered_structure_verification_v4",
        content_type="application/json",
        data=b'{"opaque":"rendered verification pending"}',
    )
    return materialize_candidate_artifacts(
        catalog=catalog,
        run_id=RUN_ID,
        candidate_input=CandidateMaterializationInputV2(
            run_id=RUN_ID,
            candidate_id="candidate-runtime-0001",
            parent_candidate_id=None,
            origin="deterministic",
            generator_id="measurement-affine-seed",
            generator_version="measurement_affine_seed_v1",
            target_hypothesis_id=intent.target_hypothesis_id,
            target_hypothesis_hash=intent.target_hypothesis_hash,
            constraint_set_hash=intent.constraint_set_hash,
            intent_ref=intent_ref,
            genome_ref=genome_ref,
            topology_hash=hashes.topology_hash,
            parameter_layout_hash=hashes.parameter_layout_hash,
            semantic_genome_hash=hashes.semantic_genome_hash,
            compilation_ref=compilation_ref,
            diagnostic_compilation_ref=diagnostic_compilation_ref,
            glsl_ref=glsl_ref,
            render_refs=(render_ref,) * 5,
            render_plan_ref=render_plan_ref,
            render_progress_ref=render_progress_ref,
            render_repeatability_ref=render_repeatability_ref,
            rendered_structure_evidence_ref=rendered_structure_evidence_ref,
            rendered_structure_verification_ref=(
                rendered_structure_verification_ref
            ),
            constraint_evaluation_ref=constraint_evaluation_ref,
            evaluation_refs=(evaluation_ref,) * 5,
        ),
    )


def _score(value: float) -> ScoreBreakdownV1:
    return ScoreBreakdownV1(
        metric_version="test_metric_v1",
        total_loss=value,
        global_rmse=value,
        global_mae=value,
        edge_loss=value,
        geometry_loss=value,
        representative_pixel_loss=value,
        roi_losses=(("subject", value),),
        protected_region_losses=(("center", value),),
        effective_weights=(("global_rmse", 1.0),),
        diagnostics=(),
    )


def _selector_candidate(bundle: CandidateArtifactBundleV2) -> CandidateRecord:
    candidate = bundle.candidate
    provenance = bundle.provenance
    return CandidateRecord(
        candidate_id=candidate.candidate_id,
        parent_candidate_id=None,
        glsl_sha256=candidate.glsl_ref.sha256,
        glsl_ref=candidate.glsl_ref.artifact_id,
        author_ref="not-used-by-selector",
        provenance_ref=candidate.provenance_ref.artifact_id,
        compile_ref=candidate.compilation_ref.artifact_id,
        render_ref=candidate.render_refs[0].artifact_id,
        render_sha256=candidate.render_refs[0].sha256,
        metrics_ref=candidate.evaluation_refs[0].artifact_id,
        review_ref=None,
        iteration=0,
        changed_problem_domain="initial_build",
        prompt_version="deterministic-v2",
        model_ref="deterministic",
        score_summary=_score(0.1),
        hard_constraints_passed=True,
        origin=provenance.origin,
        generator_version=provenance.generator_version,
    )


def test_structure_envelope_replay_is_the_only_structure_capability_constructor(
    tmp_path: Path,
) -> None:
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
    )
    materialized = materialize_runtime_target_structure_artifacts(
        catalog=catalog,
        run_id=RUN_ID,
        evidence=evidence,
    )

    verified = load_verified_runtime_structure_admission(
        materialized.envelope_ref,
        resolver=catalog,
        run_id=RUN_ID,
    )

    assert verified.envelope_ref == materialized.envelope_ref
    assert verified.target.topology == "solid"
    with pytest.raises(TypeError, match="只能由"):
        VerifiedRuntimeStructureAdmission()
    with pytest.raises(TypeError, match="只能由"):
        TrustedRuntimeSelectorInput()
    with pytest.raises(ValueError, match="factory_token_invalid"):
        VerifiedRuntimeStructureAdmission._from_bundle(
            materialized,
            factory_token=object(),
        )
    with pytest.raises(ValueError, match="factory_token_invalid"):
        TrustedRuntimeSelectorInput._from_verified_artifacts(
            evidence=object(),  # type: ignore[arg-type]
            structure_envelope_ref=materialized.envelope_ref,
            candidate_ref=materialized.envelope_ref,
            candidate_glsl_ref=materialized.envelope_ref,
            candidate_render_ref=materialized.envelope_ref,
            candidate_provenance_ref=materialized.envelope_ref,
            candidate_generator_id="forged",
            factory_token=object(),
        )


def test_rejected_structure_envelope_never_produces_capability(tmp_path: Path) -> None:
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="ring",
        hole_count=1,
        mask_has_hole=False,
    )
    materialized = materialize_runtime_target_structure_artifacts(
        catalog=catalog,
        run_id=RUN_ID,
        evidence=evidence,
    )

    with pytest.raises(ValueError, match="runtime_structure_not_verified"):
        load_verified_runtime_structure_admission(
            materialized.envelope_ref,
            resolver=catalog,
            run_id=RUN_ID,
        )


def test_adapter_reads_candidate_closure_but_rejects_opaque_v2_2_semantics(
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
    candidate = _candidate_bundle(catalog, intent_source_ref=evidence.intent_ref)

    with pytest.raises(RuntimeAdmissionRejected) as caught:
        load_trusted_runtime_selector_input(
            structure.envelope_ref,
            candidate.candidate_ref,
            resolver=catalog,
            run_id=RUN_ID,
        )

    assert caught.value.code == "runtime_candidate_typed_semantics_not_verified"


def test_adapter_maps_nested_candidate_tampering_to_fail_closed_code(
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
    candidate = _candidate_bundle(catalog, intent_source_ref=evidence.intent_ref)
    run = LocalArtifactStore(tmp_path).start_run("project-v2", RUN_ID)
    manifest = json.loads(run.read_bytes(".artifact-catalog-v2/manifest.json"))
    entry = manifest["artifacts"][candidate.candidate.glsl_ref.artifact_id]
    run.write_bytes(entry["relative_path"], b"tampered")

    with pytest.raises(RuntimeAdmissionRejected) as caught:
        load_trusted_runtime_selector_input(
            structure.envelope_ref,
            candidate.candidate_ref,
            resolver=catalog,
            run_id=RUN_ID,
        )

    assert caught.value.code == "runtime_candidate_recovery_failed"


def test_public_adapter_is_unique_positive_path_and_selector_checks_artifact_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    candidate = _candidate_bundle(catalog, intent_source_ref=evidence.intent_ref)
    # 模拟 V2.4 typed loader 完成后的唯一状态升级；所有 refs/bytes 已由真实 loader
    # 物化和验证，本测试只覆盖未来状态转换后的 sealed Selector 集成点。
    future_bundle = SimpleNamespace(
        candidate_ref=candidate.candidate_ref,
        candidate=candidate.candidate,
        provenance=candidate.provenance,
        semantic_validation_status="admissible_v2_4_rendered_structure_verified",
    )
    def future_loader(
        candidate_ref: ArtifactRefV2,
        *,
        resolver: object,
        run_id: str,
    ) -> object:
        assert candidate_ref == candidate.candidate_ref
        assert resolver is catalog
        assert run_id == RUN_ID
        return future_bundle

    monkeypatch.setattr(
        "shaderforge.evaluation.runtime_admission.load_candidate_artifacts",
        future_loader,
    )
    trusted = load_trusted_runtime_selector_input(
        structure.envelope_ref,
        candidate.candidate_ref,
        resolver=catalog,
        run_id=RUN_ID,
    )
    selector_candidate = _selector_candidate(candidate)

    admitted = select_current_best(
        None,
        selector_candidate,
        AcceptancePolicy(),
        admission_policy=MeasurementSeedAdmissionPolicy(),
        trusted_runtime_admission=trusted,
    )
    ref_tampered = select_current_best(
        None,
        selector_candidate.__class__(
            **{**selector_candidate.to_dict(), "glsl_ref": "forged-ref"}
        ),
        AcceptancePolicy(),
        admission_policy=MeasurementSeedAdmissionPolicy(),
        trusted_runtime_admission=trusted,
    )

    assert admitted.accepted is True
    assert admitted.admission_status == "admitted"
    assert ref_tampered.accepted is False
    assert ref_tampered.admission_reason_codes == (
        "generator_admission_identity_mismatch",
    )

    forged = object.__new__(TrustedRuntimeSelectorInput)
    forged_decision = select_current_best(
        None,
        selector_candidate,
        AcceptancePolicy(),
        admission_policy=MeasurementSeedAdmissionPolicy(),
        trusted_runtime_admission=forged,
    )
    assert forged_decision.accepted is False
    assert forged_decision.admission_reason_codes == (
        "runtime_selector_input_not_trusted",
    )
