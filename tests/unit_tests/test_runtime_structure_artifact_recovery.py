from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from shaderforge.contracts.canonical import canonical_json_bytes, canonical_sha256
from shaderforge.evaluation.runtime_structure import RuntimeTargetStructureEvidence
from shaderforge.evaluation.runtime_structure_artifacts import (
    RUNTIME_TARGET_STRUCTURE_ARTIFACT_ENVELOPE_KIND,
    RUNTIME_TARGET_STRUCTURE_ARTIFACT_ENVELOPE_SCHEMA_VERSION,
    RUNTIME_TARGET_STRUCTURE_EVIDENCE_ARTIFACT_KIND,
    RuntimeTargetStructureArtifactEnvelope,
    load_runtime_target_structure_artifacts,
    materialize_runtime_target_structure_artifacts,
)
from shaderforge.store import ArtifactRefV2, LocalArtifactCatalog, LocalArtifactStore
from shaderforge.store.artifact_catalog import (
    ArtifactCatalogError,
    ArtifactIntegrityError,
)
from shaderforge.store.local_artifacts import RunArtifactStore
from tests.unit_tests.test_runtime_target_structure_verifier import (
    _build_evidence as _build_verified_evidence,
)

RUN_ID = "run-v2-structure"


def _build_evidence(
    tmp_path: Path,
) -> tuple[LocalArtifactCatalog, RunArtifactStore, RuntimeTargetStructureEvidence]:
    catalog, evidence = _build_verified_evidence(
        tmp_path,
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
    )
    run = LocalArtifactStore(tmp_path).start_run("project-v2", RUN_ID)
    return catalog, run, evidence


class _OverlayResolver:
    def __init__(self, fallback: LocalArtifactCatalog) -> None:
        self.fallback = fallback
        self.values: dict[str, tuple[ArtifactRefV2, bytes]] = {}

    def add(
        self,
        data: bytes,
        *,
        kind: str,
        schema_version: str,
        content_type: str = "application/json",
    ) -> ArtifactRefV2:
        digest = sha256(data).hexdigest()
        ref = ArtifactRefV2(
            artifact_id=f"art_overlay_{len(self.values)}_{digest}",
            sha256=digest,
            kind=kind,
            schema_version=schema_version,
            content_type=content_type,
            size_bytes=len(data),
        )
        self.values[ref.artifact_id] = (ref, data)
        return ref

    def resolve(self, artifact_id: str) -> ArtifactRefV2:
        if artifact_id in self.values:
            return self.values[artifact_id][0]
        return self.fallback.resolve(artifact_id)

    def read_bytes(self, artifact_id: str) -> bytes:
        if artifact_id in self.values:
            return self.values[artifact_id][1]
        return self.fallback.read_bytes(artifact_id)


def _overlay_envelope(
    resolver: _OverlayResolver,
    envelope: RuntimeTargetStructureArtifactEnvelope,
) -> ArtifactRefV2:
    return resolver.add(
        canonical_json_bytes(envelope),
        kind=RUNTIME_TARGET_STRUCTURE_ARTIFACT_ENVELOPE_KIND,
        schema_version=RUNTIME_TARGET_STRUCTURE_ARTIFACT_ENVELOPE_SCHEMA_VERSION,
    )


def test_runtime_structure_artifacts_recover_after_new_catalog_instance(
    tmp_path: Path,
) -> None:
    catalog, _, evidence = _build_evidence(tmp_path)
    materialized = materialize_runtime_target_structure_artifacts(
        catalog=catalog,
        run_id=RUN_ID,
        evidence=evidence,
    )

    restarted_run = LocalArtifactStore(tmp_path).start_run("project-v2", RUN_ID)
    restarted = LocalArtifactCatalog(restarted_run, run_id=RUN_ID)
    recovered = load_runtime_target_structure_artifacts(
        materialized.envelope_ref,
        resolver=restarted,
        run_id=RUN_ID,
    )

    assert recovered == materialized
    assert recovered.envelope.verification_status == "structure_verified"
    assert recovered.verification.status == "structure_verified"
    assert recovered.verification.target is not None
    assert recovered.envelope.evidence_canonical_sha256 == canonical_sha256(evidence)
    assert recovered.verification.evidence_sha256 == canonical_sha256(evidence)


def test_runtime_structure_artifacts_preserve_rejected_status_without_target(
    tmp_path: Path,
) -> None:
    catalog, _, evidence = _build_evidence(tmp_path)
    rejected_evidence = evidence.model_copy(
        update={"target_source_sha256": sha256(b"different-source").hexdigest()}
    )

    materialized = materialize_runtime_target_structure_artifacts(
        catalog=catalog,
        run_id=RUN_ID,
        evidence=rejected_evidence,
    )
    recovered = load_runtime_target_structure_artifacts(
        materialized.envelope_ref,
        resolver=catalog,
        run_id=RUN_ID,
    )

    assert recovered.envelope.verification_status == "rejected"
    assert recovered.verification.status == "rejected"
    assert recovered.verification.target is None


def test_runtime_structure_artifacts_reject_wrong_run_or_catalog(tmp_path: Path) -> None:
    catalog, _, evidence = _build_evidence(tmp_path)
    materialized = materialize_runtime_target_structure_artifacts(
        catalog=catalog,
        run_id=RUN_ID,
        evidence=evidence,
    )

    with pytest.raises(ValueError, match="run_id"):
        load_runtime_target_structure_artifacts(
            materialized.envelope_ref,
            resolver=catalog,
            run_id="another-run",
        )
    with pytest.raises(ValueError, match="run_id.*不一致"):
        materialize_runtime_target_structure_artifacts(
            catalog=catalog,
            run_id="another-run",
            evidence=evidence,
        )

    other_run = LocalArtifactStore(tmp_path).start_run("project-v2", "other-run")
    other_catalog = LocalArtifactCatalog(other_run, run_id="other-run")
    with pytest.raises(FileNotFoundError):
        load_runtime_target_structure_artifacts(
            materialized.envelope_ref,
            resolver=other_catalog,
            run_id=RUN_ID,
        )


@pytest.mark.parametrize("tamper", ["missing", "bytes"])
def test_runtime_structure_artifacts_reject_missing_or_tampered_bytes(
    tmp_path: Path,
    tamper: str,
) -> None:
    catalog, run, evidence = _build_evidence(tmp_path)
    materialized = materialize_runtime_target_structure_artifacts(
        catalog=catalog,
        run_id=RUN_ID,
        evidence=evidence,
    )
    manifest = json.loads(run.read_bytes(".artifact-catalog-v2/manifest.json"))
    entry = manifest["artifacts"][materialized.envelope.evidence_ref.artifact_id]
    blob_path = run.path_for(entry["relative_path"])
    if tamper == "missing":
        blob_path.unlink()
    else:
        blob_path.write_bytes(b"tampered")

    with pytest.raises((ArtifactIntegrityError, FileNotFoundError)):
        load_runtime_target_structure_artifacts(
            materialized.envelope_ref,
            resolver=catalog,
            run_id=RUN_ID,
        )


def test_runtime_structure_artifacts_reject_catalog_manifest_tampering(
    tmp_path: Path,
) -> None:
    catalog, run, evidence = _build_evidence(tmp_path)
    materialized = materialize_runtime_target_structure_artifacts(
        catalog=catalog,
        run_id=RUN_ID,
        evidence=evidence,
    )
    manifest = json.loads(run.read_bytes(".artifact-catalog-v2/manifest.json"))
    manifest["artifacts"][materialized.envelope_ref.artifact_id]["kind"] = "wrong"
    run.write_json(".artifact-catalog-v2/manifest.json", manifest)

    with pytest.raises(ArtifactCatalogError, match="artifact_id"):
        load_runtime_target_structure_artifacts(
            materialized.envelope_ref,
            resolver=catalog,
            run_id=RUN_ID,
        )


@pytest.mark.parametrize(
    "invalid_json",
    [
        b'{"schema_version":"runtime_target_structure_evidence_v2",'
        b'"schema_version":"runtime_target_structure_evidence_v2"}',
        b'{"schema_version":"runtime_target_structure_evidence_v2","unknown":1}',
    ],
)
def test_runtime_structure_artifacts_reject_duplicate_or_unknown_json(
    tmp_path: Path,
    invalid_json: bytes,
) -> None:
    catalog, _, evidence = _build_evidence(tmp_path)
    materialized = materialize_runtime_target_structure_artifacts(
        catalog=catalog,
        run_id=RUN_ID,
        evidence=evidence,
    )
    resolver = _OverlayResolver(catalog)
    bad_evidence_ref = resolver.add(
        invalid_json,
        kind=RUNTIME_TARGET_STRUCTURE_EVIDENCE_ARTIFACT_KIND,
        schema_version="runtime_target_structure_evidence_v2",
    )
    bad_envelope = materialized.envelope.model_copy(
        update={"evidence_ref": bad_evidence_ref}
    )
    bad_envelope_ref = _overlay_envelope(resolver, bad_envelope)

    with pytest.raises(ValueError, match="重复 key|unknown|validation error"):
        load_runtime_target_structure_artifacts(
            bad_envelope_ref,
            resolver=resolver,
            run_id=RUN_ID,
        )


def test_runtime_structure_artifacts_reject_persisted_conclusion_tampering(
    tmp_path: Path,
) -> None:
    catalog, _, evidence = _build_evidence(tmp_path)
    materialized = materialize_runtime_target_structure_artifacts(
        catalog=catalog,
        run_id=RUN_ID,
        evidence=evidence,
    )
    resolver = _OverlayResolver(catalog)
    tampered_verification = materialized.verification.model_copy(
        update={"computed_component_count": 2}
    )
    verification_ref = resolver.add(
        canonical_json_bytes(tampered_verification),
        kind="runtime_target_structure_verification",
        schema_version="runtime_target_structure_verification_v2",
    )
    tampered_envelope = materialized.envelope.model_copy(
        update={"verification_ref": verification_ref}
    )
    tampered_envelope_ref = _overlay_envelope(resolver, tampered_envelope)

    with pytest.raises(ValueError, match="逐字段不一致"):
        load_runtime_target_structure_artifacts(
            tampered_envelope_ref,
            resolver=resolver,
            run_id=RUN_ID,
        )
