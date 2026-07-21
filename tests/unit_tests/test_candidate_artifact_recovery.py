from __future__ import annotations

import json
from dataclasses import asdict, replace
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from shaderforge.evaluation.candidate_artifacts import (
    CANDIDATE_PROVENANCE_ARTIFACT_KIND,
    CANDIDATE_PROVENANCE_SCHEMA_VERSION,
    CANDIDATE_RECORD_ARTIFACT_KIND,
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
    CandidateMaterializationInputV2,
    load_candidate_artifacts,
    materialize_candidate_artifacts,
)
from shaderforge.evaluation.models_v2 import (
    CandidateProvenanceV2,
    CandidateRecordV2,
    compute_candidate_provenance_hash,
    compute_candidate_record_hash,
)
from shaderforge.genome import compute_genome_hashes
from shaderforge.intent.builder import build_intent_variants
from shaderforge.store import ArtifactRefV2, LocalArtifactCatalog, LocalArtifactStore
from shaderforge.store.local_artifacts import RunArtifactStore
from tests.fixtures.png_to_shader_v2_contracts import (
    make_constraint_set,
    make_genome,
    make_target_measurements,
)
from tests.unit_tests.test_intent_ir import _context, _interpretation

RUN_ID = "run-v2-candidate"


def _png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGBA", (2, 2), (20, 40, 60, 255)).save(output, format="PNG")
    return output.getvalue()


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


def _fixture(
    tmp_path: Path,
) -> tuple[
    LocalArtifactCatalog,
    RunArtifactStore,
    CandidateMaterializationInputV2,
]:
    run = LocalArtifactStore(tmp_path).start_run("project-v2", RUN_ID)
    catalog = LocalArtifactCatalog(run, run_id=RUN_ID)
    measurements = make_target_measurements()
    constraints = make_constraint_set()
    intent = build_intent_variants(
        measurements,
        _interpretation(),
        constraints,
        _context(),
    ).variants[0]
    hypothesis = measurements.target_hypotheses[0]
    base_genome = make_genome()
    genome = base_genome.model_copy(
        update={
            "provenance": base_genome.provenance.model_copy(
                update={
                    "intent_id": intent.intent_id,
                    "target_hypothesis_id": hypothesis.hypothesis_id,
                    "target_hypothesis_hash": hypothesis.hypothesis_hash,
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
        data=intent.model_dump_json().encode(),
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
        data=b'{"opaque":"typed DiagnosticCompilationBundleV2 pending"}',
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
        data=b'{"opaque":"typed RenderPlanV2 pending"}',
    )
    render_progress_ref = _put(
        catalog,
        kind="renderer_progress",
        schema_version="renderer_progress_v2",
        content_type="application/json",
        data=b'{"opaque":"typed RenderProgressV2 pending"}',
    )
    render_repeatability_ref = _put(
        catalog,
        kind="render_repeatability_evidence",
        schema_version="render_repeatability_evidence_v2",
        content_type="application/json",
        data=b'{"opaque":"typed RenderRepeatabilityEvidenceV2 pending"}',
    )
    rendered_structure_evidence_ref = _put(
        catalog,
        kind="rendered_structure_evidence",
        schema_version="rendered_structure_evidence_v4",
        content_type="application/json",
        data=b'{"opaque":"typed RenderedStructureEvidenceV3 pending"}',
    )
    rendered_structure_verification_ref = _put(
        catalog,
        kind="rendered_structure_verification",
        schema_version="rendered_structure_verification_v4",
        content_type="application/json",
        data=b'{"opaque":"typed RenderedStructureVerificationV3 pending"}',
    )
    candidate_input = CandidateMaterializationInputV2(
        run_id=RUN_ID,
        candidate_id="candidate-v2-0001",
        parent_candidate_id=None,
        origin="deterministic",
        generator_id="effect-genome-expander",
        generator_version="effect-genome-expander-v2-test",
        target_hypothesis_id=hypothesis.hypothesis_id,
        target_hypothesis_hash=hypothesis.hypothesis_hash,
        constraint_set_hash=constraints.constraint_set_hash,
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
        rendered_structure_verification_ref=rendered_structure_verification_ref,
        constraint_evaluation_ref=constraint_evaluation_ref,
        evaluation_refs=(evaluation_ref,) * 5,
    )
    return catalog, run, candidate_input


def _blob_path(run: RunArtifactStore, ref: ArtifactRefV2) -> Path:
    manifest = json.loads(run.read_bytes(".artifact-catalog-v2/manifest.json"))
    return run.path_for(manifest["artifacts"][ref.artifact_id]["relative_path"])


class _OverlayResolver:
    def __init__(
        self,
        fallback: LocalArtifactCatalog,
        ref: ArtifactRefV2,
        data: bytes,
    ) -> None:
        self.fallback = fallback
        self.run_id = fallback.run_id
        self.ref = ref
        self.data = data

    def resolve(self, artifact_id: str) -> ArtifactRefV2:
        if artifact_id == self.ref.artifact_id:
            return self.ref
        return self.fallback.resolve(artifact_id)

    def read_bytes(self, artifact_id: str) -> bytes:
        if artifact_id == self.ref.artifact_id:
            return self.data
        return self.fallback.read_bytes(artifact_id)


def _overlay_ref(data: bytes, *, kind: str, schema_version: str) -> ArtifactRefV2:
    digest = sha256(data).hexdigest()
    return ArtifactRefV2(
        artifact_id=f"overlay_{digest}",
        sha256=digest,
        kind=kind,
        schema_version=schema_version,
        content_type="application/json",
        size_bytes=len(data),
    )


def test_candidate_closure_recovers_from_new_catalog_and_is_not_admissible(
    tmp_path: Path,
) -> None:
    catalog, run, candidate_input = _fixture(tmp_path)
    materialized = materialize_candidate_artifacts(
        catalog=catalog,
        run_id=RUN_ID,
        candidate_input=candidate_input,
    )

    restarted = LocalArtifactCatalog(run, run_id=RUN_ID)
    recovered = load_candidate_artifacts(
        materialized.candidate_ref,
        resolver=restarted,
        run_id=RUN_ID,
    )

    assert recovered == materialized
    assert recovered.provenance.origin == "deterministic"
    assert recovered.provenance.generator_version == (
        "effect-genome-expander-v2-test"
    )
    assert recovered.semantic_validation_status == (
        "not_admissible_v2_2_typed_schemas_unavailable"
    )
    assert recovered.provenance.downstream_semantic_validation == (
        "opaque_content_verified_not_admissible_until_v2_2"
    )
    assert recovered.content_verified_refs[0] == recovered.candidate_ref
    assert recovered.content_verified_refs[1] == recovered.candidate.provenance_ref


def test_candidate_closure_rejects_wrong_run_or_cross_run_catalog(
    tmp_path: Path,
) -> None:
    catalog, _, candidate_input = _fixture(tmp_path)
    materialized = materialize_candidate_artifacts(
        catalog=catalog,
        run_id=RUN_ID,
        candidate_input=candidate_input,
    )
    with pytest.raises(ValueError, match="run_id"):
        load_candidate_artifacts(
            materialized.candidate_ref,
            resolver=catalog,
            run_id="another-run",
        )
    with pytest.raises(ValueError, match="run_id"):
        materialize_candidate_artifacts(
            catalog=catalog,
            run_id="another-run",
            candidate_input=candidate_input,
        )

    other_run = LocalArtifactStore(tmp_path).start_run("project-v2", "other-run")
    other_catalog = LocalArtifactCatalog(other_run, run_id="other-run")
    with pytest.raises(ValueError, match="run_id"):
        load_candidate_artifacts(
            materialized.candidate_ref,
            resolver=other_catalog,
            run_id=RUN_ID,
        )


@pytest.mark.parametrize("failure", ("missing", "tampered"))
def test_candidate_closure_rejects_missing_or_tampered_nested_bytes(
    tmp_path: Path,
    failure: str,
) -> None:
    catalog, run, candidate_input = _fixture(tmp_path)
    materialized = materialize_candidate_artifacts(
        catalog=catalog,
        run_id=RUN_ID,
        candidate_input=candidate_input,
    )
    target = materialized.candidate.compilation_ref
    path = _blob_path(run, target)
    if failure == "missing":
        path.unlink()
    else:
        run.write_bytes(path.relative_to(run.root), b"tampered")

    with pytest.raises((FileNotFoundError, ValueError), match="缺失|size|SHA-256"):
        load_candidate_artifacts(
            materialized.candidate_ref,
            resolver=catalog,
            run_id=RUN_ID,
        )


def test_candidate_closure_rejects_wrong_ref_contract_before_writing_candidate(
    tmp_path: Path,
) -> None:
    catalog, _, candidate_input = _fixture(tmp_path)
    wrong_ref = replace(candidate_input.compilation_ref, kind="untrusted_json")

    with pytest.raises(ValueError, match="kind/schema/content-type"):
        materialize_candidate_artifacts(
            catalog=catalog,
            run_id=RUN_ID,
            candidate_input=candidate_input.model_copy(
                update={"compilation_ref": wrong_ref}
            ),
        )


def test_candidate_closure_rejects_duplicate_keys_in_typed_and_opaque_json(
    tmp_path: Path,
) -> None:
    catalog, _, candidate_input = _fixture(tmp_path)
    duplicate_compilation = _put(
        catalog,
        kind=COMPILATION_ARTIFACT_KIND,
        schema_version=COMPILATION_ARTIFACT_SCHEMA_VERSION,
        content_type="application/json",
        data=b'{"opaque":1,"opaque":2}',
    )
    with pytest.raises(ValueError, match="重复 key"):
        materialize_candidate_artifacts(
            catalog=catalog,
            run_id=RUN_ID,
            candidate_input=candidate_input.model_copy(
                update={"compilation_ref": duplicate_compilation}
            ),
        )

    materialized = materialize_candidate_artifacts(
        catalog=catalog,
        run_id=RUN_ID,
        candidate_input=candidate_input,
    )
    original = catalog.read_bytes(materialized.candidate_ref.artifact_id)
    duplicate_candidate = original.replace(
        b'{"schema_version":',
        b'{"candidate_id":"duplicate","schema_version":',
        1,
    )
    overlay_ref = _overlay_ref(
        duplicate_candidate,
        kind=CANDIDATE_RECORD_ARTIFACT_KIND,
        schema_version="candidate_record_v3",
    )
    with pytest.raises(ValueError, match="重复 key"):
        load_candidate_artifacts(
            overlay_ref,
            resolver=_OverlayResolver(catalog, overlay_ref, duplicate_candidate),
            run_id=RUN_ID,
        )


def test_candidate_closure_rejects_rehashed_cross_identity_mismatch(
    tmp_path: Path,
) -> None:
    catalog, _, candidate_input = _fixture(tmp_path)
    materialized = materialize_candidate_artifacts(
        catalog=catalog,
        run_id=RUN_ID,
        candidate_input=candidate_input,
    )
    provenance_raw = materialized.provenance.model_dump(mode="python")
    provenance_raw["candidate_id"] = "candidate-v2-other"
    provenance_raw["record_hash"] = compute_candidate_provenance_hash(provenance_raw)
    altered_provenance = CandidateProvenanceV2.model_validate_json(
        json.dumps(provenance_raw), strict=True
    )
    altered_provenance_ref = _put(
        catalog,
        kind=CANDIDATE_PROVENANCE_ARTIFACT_KIND,
        schema_version=CANDIDATE_PROVENANCE_SCHEMA_VERSION,
        content_type="application/json",
        data=altered_provenance.model_dump_json().encode(),
    )
    candidate_raw = materialized.candidate.model_dump(mode="json")
    candidate_raw["provenance_ref"] = asdict(altered_provenance_ref)
    candidate_raw["record_hash"] = compute_candidate_record_hash(candidate_raw)
    altered_candidate = CandidateRecordV2.model_validate_json(
        json.dumps(candidate_raw), strict=True
    )
    altered_candidate_ref = _put(
        catalog,
        kind=CANDIDATE_RECORD_ARTIFACT_KIND,
        schema_version="candidate_record_v3",
        content_type="application/json",
        data=altered_candidate.model_dump_json().encode(),
    )

    with pytest.raises(ValueError, match="身份不一致"):
        load_candidate_artifacts(
            altered_candidate_ref,
            resolver=catalog,
            run_id=RUN_ID,
        )


def test_candidate_provenance_rejects_ref_hash_drift_even_if_record_rehashed(
    tmp_path: Path,
) -> None:
    catalog, _, candidate_input = _fixture(tmp_path)
    materialized = materialize_candidate_artifacts(
        catalog=catalog,
        run_id=RUN_ID,
        candidate_input=candidate_input,
    )
    raw = materialized.provenance.model_dump(mode="python")
    raw["glsl_sha256"] = "f" * 64
    raw["record_hash"] = compute_candidate_provenance_hash(raw)

    with pytest.raises(ValueError, match="glsl hash"):
        CandidateProvenanceV2.model_validate_json(json.dumps(raw), strict=True)
