from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from shaderforge.evaluation import (
    CandidateAttemptEvidenceV1,
    CandidateAttemptRecord,
    load_candidate_attempt,
    materialize_attempt_evidence,
    materialize_candidate_attempt,
)
from shaderforge.store import LocalArtifactCatalog, LocalArtifactStore


def _catalog(tmp_path: Path, run_id: str = "run-attempt-v2") -> LocalArtifactCatalog:
    run = LocalArtifactStore(tmp_path / "artifacts").start_run("project-v2", run_id)
    return LocalArtifactCatalog(run, run_id=run_id)


def test_candidate_attempt_strict_round_trip_binds_all_identity_fields(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    evidence = CandidateAttemptEvidenceV1(
        run_id="run-attempt-v2",
        attempt_id="attempt-v2-strict",
        target_hypothesis_hash="a" * 64,
        semantic_genome_hash="b" * 64,
        stage="compile",
        outcome="failure",
        error_code="compile_failed_strict",
    )
    evidence_ref = materialize_attempt_evidence(
        catalog=catalog, run_id=evidence.run_id, evidence=evidence
    )
    attempt = CandidateAttemptRecord(
        attempt_id=evidence.attempt_id,
        run_id=evidence.run_id,
        target_hypothesis_hash=evidence.target_hypothesis_hash,
        semantic_genome_hash=evidence.semantic_genome_hash,
        status="compile_failed",
        error_code=evidence.error_code or "missing",
        evidence_refs=(evidence_ref,),
    )
    ref = materialize_candidate_attempt(
        catalog=catalog, run_id=attempt.run_id, attempt=attempt
    )

    loaded = load_candidate_attempt(
        ref,
        resolver=catalog,
        run_id=attempt.run_id,
        expected_target_hypothesis_hash=attempt.target_hypothesis_hash,
        expected_semantic_genome_hash=attempt.semantic_genome_hash,
    )

    assert loaded.attempt == attempt
    assert loaded.evidence == (evidence,)


def test_candidate_attempt_loader_rejects_cross_run_hash_and_ref_tamper(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    evidence = CandidateAttemptEvidenceV1(
        run_id="run-attempt-v2",
        attempt_id="attempt-v2-strict",
        target_hypothesis_hash="a" * 64,
        semantic_genome_hash="b" * 64,
        stage="evaluate",
        outcome="failure",
        error_code="oracle_unavailable",
    )
    evidence_ref = materialize_attempt_evidence(
        catalog=catalog, run_id=evidence.run_id, evidence=evidence
    )
    ref = materialize_candidate_attempt(
        catalog=catalog,
        run_id=evidence.run_id,
        attempt=CandidateAttemptRecord(
            attempt_id=evidence.attempt_id,
            run_id=evidence.run_id,
            target_hypothesis_hash=evidence.target_hypothesis_hash,
            semantic_genome_hash=evidence.semantic_genome_hash,
            status="evaluation_failed",
            error_code=evidence.error_code or "missing",
            evidence_refs=(evidence_ref,),
        ),
    )

    with pytest.raises(ValueError, match="当前 run"):
        load_candidate_attempt(ref, resolver=catalog, run_id="other-run")
    with pytest.raises(ValueError, match="target hypothesis"):
        load_candidate_attempt(
            ref,
            resolver=catalog,
            run_id=evidence.run_id,
            expected_target_hypothesis_hash="c" * 64,
        )
    with pytest.raises(ValueError, match="semantic genome"):
        load_candidate_attempt(
            ref,
            resolver=catalog,
            run_id=evidence.run_id,
            expected_semantic_genome_hash="d" * 64,
        )
    tampered_ref = replace(ref, sha256="f" * 64)
    with pytest.raises(ValueError, match="身份不一致"):
        load_candidate_attempt(
            tampered_ref, resolver=catalog, run_id=evidence.run_id
        )


def test_candidate_attempt_materialization_rejects_mismatched_evidence(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    wrong = CandidateAttemptEvidenceV1(
        run_id="run-attempt-v2",
        attempt_id="wrong-attempt",
        target_hypothesis_hash="a" * 64,
        semantic_genome_hash="b" * 64,
        stage="materialize",
        outcome="failure",
        error_code="closure_failed",
    )
    wrong_ref = materialize_attempt_evidence(
        catalog=catalog, run_id=wrong.run_id, evidence=wrong
    )
    attempt = CandidateAttemptRecord(
        attempt_id="expected-attempt",
        run_id=wrong.run_id,
        target_hypothesis_hash=wrong.target_hypothesis_hash,
        semantic_genome_hash=wrong.semantic_genome_hash,
        status="rejected",
        error_code="closure_failed",
        evidence_refs=(wrong_ref,),
    )

    with pytest.raises(ValueError, match="identity 不一致"):
        materialize_candidate_attempt(
            catalog=catalog, run_id=wrong.run_id, attempt=attempt
        )
