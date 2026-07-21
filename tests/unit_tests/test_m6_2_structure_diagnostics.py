from __future__ import annotations

import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from shaderforge.benchmark.m6_2_diagnostics import (
    M6_2StructureDiagnosticReport,
    assess_generator_capability,
    build_m6_2_structure_diagnostic_report,
    compute_m6_2_report_hash,
)
from shaderforge.benchmark.m6_2_selector_replay import (
    M6_2SelectorReplayReport,
    build_m6_2_selector_replay_report,
    compute_m6_2_selector_replay_hash,
)
from shaderforge.benchmark.v2_dataset import (
    LoadedV2Dataset,
    V2DatasetSample,
    load_v2_dataset_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
DATASET_MANIFEST = ROOT / "benchmarks/png_to_shader_v2/dataset_manifest.v1.json"
SYNTHETIC_ACCEPTANCE_POLICY = {
    "min_total_improvement": 0.005,
    "max_protected_regression": 0.02,
    "quality_threshold": 0.12,
    "stagnation_rounds": 2,
}


def _dataset() -> LoadedV2Dataset:
    return load_v2_dataset_manifest(DATASET_MANIFEST)


def _sample(dataset: LoadedV2Dataset, case_id: str) -> V2DatasetSample:
    return next(
        item
        for item in dataset.manifest.split("development").samples
        if item.case_id == case_id
    )


def test_measurement_seed_capability_uses_labels_without_case_branches() -> None:
    dataset = _dataset()

    ellipse = assess_generator_capability(
        _sample(dataset, "ellipse_gradient"),
        origin="deterministic",
        generator_version="measurement_affine_seed_v1",
    )
    dual = assess_generator_capability(
        _sample(dataset, "dual_disks"),
        origin="deterministic",
        generator_version="measurement_affine_seed_v1",
    )
    ring = assess_generator_capability(
        _sample(dataset, "neon_ring"),
        origin="deterministic",
        generator_version="measurement_affine_seed_v1",
    )
    layered = assess_generator_capability(
        _sample(dataset, "pink_gel"),
        origin="deterministic",
        generator_version="measurement_affine_seed_v1",
    )
    color_lobes = assess_generator_capability(
        _sample(dataset, "color_lobes"),
        origin="deterministic",
        generator_version="measurement_affine_seed_v1",
    )

    assert ellipse.status == "supported"
    assert dual.status == "unsupported"
    assert dual.instance_count_status == "unsupported"
    assert ring.status == "unsupported"
    assert ring.topology_status == "unsupported"
    assert ring.hole_count_status == "unsupported"
    assert layered.status == "unsupported"
    assert set(layered.unsupported_required_layers) >= {"rim", "highlight", "shadow"}
    assert color_lobes.status == "unsupported"
    assert color_lobes.unsupported_required_layers == ("color_lobe",)


@pytest.mark.parametrize(
    ("origin", "generator_version", "reason"),
    [
        ("model", None, "model_capability_not_declared"),
        ("deterministic", "future_seed_v2", "unknown_deterministic_generator"),
    ],
)
def test_unknown_capability_fails_closed(
    origin: str, generator_version: str | None, reason: str
) -> None:
    assessment = assess_generator_capability(
        _sample(_dataset(), "solid_circle"),
        origin=origin,  # type: ignore[arg-type]
        generator_version=generator_version,
    )

    assert assessment.status == "unknown"
    assert assessment.reason_codes == (reason,)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _candidate(
    *,
    candidate_id: str,
    render_sha256: str,
    render_ref: str,
    glsl_sha256: str,
    glsl_ref: str,
    provenance_ref: str,
    origin: str,
    generator_version: str | None,
    total_loss: float,
) -> dict[str, object]:
    prefix = f"candidates/{candidate_id}"
    score_summary = {
        "metric_version": "synthetic_metric_v1",
        "total_loss": total_loss,
        "global_rmse": total_loss,
        "global_mae": total_loss,
        "edge_loss": total_loss,
        "geometry_loss": total_loss,
        "representative_pixel_loss": total_loss,
        "roi_losses": {"subject": total_loss},
        "protected_region_losses": {"center": 0.1},
        "effective_weights": {"global_rmse": 1.0},
        "diagnostics": [],
    }
    return {
        "candidate_id": candidate_id,
        "parent_candidate_id": None,
        "render_sha256": render_sha256,
        "render_ref": render_ref,
        "glsl_sha256": glsl_sha256,
        "glsl_ref": glsl_ref,
        "author_ref": f"{prefix}/author.json",
        "provenance_ref": provenance_ref,
        "compile_ref": f"{prefix}/compile.json",
        "metrics_ref": f"{prefix}/metrics.json",
        "review_ref": None,
        "iteration": 0,
        "changed_problem_domain": "initial_build",
        "prompt_version": (
            "shader_author_initial_v1_1"
            if origin == "model"
            else "measurement_affine_seed_v1"
        ),
        "model_ref": (
            "fake:quality"
            if origin == "model"
            else "deterministic:measurement_affine_seed_v1"
        ),
        "score_summary": score_summary,
        "hard_constraints_passed": True,
        "origin": origin,
        "generator_version": generator_version,
    }


def _write_synthetic_case(
    suite: Path,
    artifacts: Path,
    *,
    dataset: LoadedV2Dataset,
    case_id: str,
    preference: str,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    project_id = f"project-{case_id}"
    run_id = f"run-{case_id}"
    case_root = suite / f"cases/{case_id}/ai-on"
    initial_bytes = f"initial:{case_id}".encode()
    final_bytes = f"final:{case_id}".encode()
    initial_render = case_root / "initial.png"
    final_render = case_root / "final.png"
    initial_render.parent.mkdir(parents=True, exist_ok=True)
    initial_render.write_bytes(initial_bytes)
    final_render.write_bytes(final_bytes)
    initial_provenance_ref = "candidates/candidate-0001/provenance.json"
    final_provenance_ref = "candidates/candidate-0002/provenance.json"
    run_root = artifacts / project_id / run_id
    sample = _sample(dataset, case_id)
    source_bytes = dataset.resolve_image(sample).read_bytes()
    normalized_reference = f"normalized:{case_id}".encode()
    (run_root / "input").mkdir(parents=True, exist_ok=True)
    (run_root / "input/source.bin").write_bytes(source_bytes)
    (run_root / "input/reference.png").write_bytes(normalized_reference)
    initial_render_ref = "candidates/candidate-0001/render.png"
    final_render_ref = "candidates/candidate-0002/render.png"
    initial_glsl_ref = "candidates/candidate-0001/shader.frag"
    final_glsl_ref = "candidates/candidate-0002/shader.frag"
    initial_glsl = f"initial glsl:{case_id}".encode()
    final_glsl = f"final glsl:{case_id}".encode()
    initial_glsl_sha = sha256(initial_glsl).hexdigest()
    final_glsl_sha = sha256(final_glsl).hexdigest()
    (run_root / initial_render_ref).parent.mkdir(parents=True, exist_ok=True)
    (run_root / initial_render_ref).write_bytes(initial_bytes)
    (run_root / final_render_ref).parent.mkdir(parents=True, exist_ok=True)
    (run_root / final_render_ref).write_bytes(final_bytes)
    (run_root / initial_glsl_ref).write_bytes(initial_glsl)
    (run_root / final_glsl_ref).write_bytes(final_glsl)
    _write_json(
        run_root / initial_provenance_ref,
        {"origin": "model", "glsl_sha256": initial_glsl_sha},
    )
    _write_json(
        run_root / final_provenance_ref,
        {
            "origin": "deterministic",
            "generator_version": "measurement_affine_seed_v1",
            "glsl_sha256": final_glsl_sha,
            "reference_sha256": sha256(normalized_reference).hexdigest(),
        },
    )
    records = [
        _candidate(
            candidate_id="candidate-0001",
            render_sha256=sha256(initial_bytes).hexdigest(),
            render_ref=initial_render_ref,
            glsl_sha256=initial_glsl_sha,
            glsl_ref=initial_glsl_ref,
            provenance_ref=initial_provenance_ref,
            origin="model",
            generator_version=None,
            total_loss=0.3,
        ),
        _candidate(
            candidate_id="candidate-0002",
            render_sha256=sha256(final_bytes).hexdigest(),
            render_ref=final_render_ref,
            glsl_sha256=final_glsl_sha,
            glsl_ref=final_glsl_ref,
            provenance_ref=final_provenance_ref,
            origin="deterministic",
            generator_version="measurement_affine_seed_v1",
            total_loss=0.1,
        ),
    ]
    for record in records:
        candidate_id = str(record["candidate_id"])
        prefix = run_root / f"candidates/{candidate_id}"
        source = initial_glsl if candidate_id == "candidate-0001" else final_glsl
        _write_json(prefix / "manifest.json", record)
        _write_json(prefix / "metrics.json", record["score_summary"])
        _write_json(
            prefix / "compile.json",
            {
                "success": True,
                "vertex_log": "",
                "fragment_log": "synthetic warning is allowed",
                "link_log": "",
                "draw_error": None,
                "static_validation": {
                    "contract_id": "webgl1_static_no_texture_v1",
                    "source_chars": len(source.decode("utf-8")),
                    "valid": True,
                    "violations": [],
                },
            },
        )
    evidence_path = f"cases/{case_id}/ai-on/run-evidence.json"
    _write_json(
        suite / evidence_path,
        {
            "project_id": project_id,
            "run_id": run_id,
            "candidate_records": records,
            "acceptance_policy": SYNTHETIC_ACCEPTANCE_POLICY,
        },
    )
    report_case: dict[str, object] = {
        "case_id": case_id,
        "ai_on": {
            "project_id": project_id,
            "run_id": run_id,
            "evidence_path": evidence_path,
            "initial_candidate_id": "candidate-0001",
            "final_candidate_id": "candidate-0002",
            "initial_render_path": f"cases/{case_id}/ai-on/initial.png",
            "final_render_path": f"cases/{case_id}/ai-on/final.png",
            "initial_objective_total_loss": 0.3,
            "final_objective_total_loss": 0.1,
        },
    }
    assignment: dict[str, object] = {
        "case_id": case_id,
        "a_role": "initial",
        "b_role": "final",
        "initial_render_path": f"cases/{case_id}/ai-on/initial.png",
        "final_render_path": f"cases/{case_id}/ai-on/final.png",
    }
    choice = {"initial": "A", "final": "B", "tie": "TIE"}[preference]
    review: dict[str, object] = {"case_id": case_id, "choice": choice}
    return report_case, assignment, review


def _synthetic_report(
    tmp_path: Path,
) -> tuple[Path, Path, M6_2StructureDiagnosticReport]:
    suite = tmp_path / "suite"
    artifacts = tmp_path / "artifacts"
    dataset = _dataset()
    cases = []
    assignments = []
    reviews = []
    for case_id, preference in (
        ("ellipse_gradient", "final"),
        ("dual_disks", "initial"),
    ):
        case, assignment, review = _write_synthetic_case(
            suite,
            artifacts,
            dataset=dataset,
            case_id=case_id,
            preference=preference,
        )
        cases.append(case)
        assignments.append(assignment)
        reviews.append(review)
    config_path = suite / "config.json"
    _write_json(
        config_path,
        {
            "schema_version": 3,
            "suite_id": "synthetic-suite",
            "suite_run_id": "synthetic",
            "acceptance_policy": SYNTHETIC_ACCEPTANCE_POLICY,
        },
    )
    _write_json(
        suite / "report.json",
        {
            "suite_run_id": "synthetic",
            "config_sha256": sha256(config_path.read_bytes()).hexdigest(),
            "cases": cases,
        },
    )
    _write_json(
        suite / "blind-review/assignments.private.json",
        {"schema_version": 1, "suite_run_id": "synthetic", "items": assignments},
    )
    _write_json(
        suite / "blind-review/human-review.json",
        {
            "schema_version": 1,
            "suite_run_id": "synthetic",
            "reviewer": "anonymous-test",
            "items": reviews,
        },
    )
    report = build_m6_2_structure_diagnostic_report(
        suite_root=suite,
        artifact_root=artifacts,
        dataset=dataset,
    )
    return suite, artifacts, report


def test_report_binds_human_preference_and_content_hashes(tmp_path: Path) -> None:
    suite, artifacts, report = _synthetic_report(tmp_path)

    assert report.case_count == 2
    assert report.schema_version == "png_to_shader_m6_2_structure_diagnostic_v2"
    assert report.capability_policy_version == "deterministic_generator_capability_v2"
    assert report.initial_preferred_count == 1
    assert report.capability_unsupported_count == 1
    assert report.initial_preferred_capability_unsupported_count == 1
    assert report.cases[0].final_capability.status == "supported"
    assert report.cases[1].final_capability.status == "unsupported"
    assert report.cases[0].target_image_sha256 == report.cases[0].source_input_sha256
    assert report.cases[0].normalized_reference_sha256
    assert report.cases[0].run_evidence_sha256
    assert report.cases[0].final.artifact_render_ref.endswith("render.png")
    assert report.cases[0].final.glsl_sha256
    assert report.report_hash == build_m6_2_structure_diagnostic_report(
        suite_root=suite,
        artifact_root=artifacts,
        dataset=_dataset(),
    ).report_hash
    assert M6_2StructureDiagnosticReport.model_validate_json(
        report.model_dump_json(), strict=True
    ) == report


def test_report_rejects_render_tampering(tmp_path: Path) -> None:
    suite, artifacts, _report = _synthetic_report(tmp_path)
    (suite / "cases/dual_disks/ai-on/final.png").write_bytes(b"tampered")

    with pytest.raises(ValueError, match="CandidateRecord 不一致"):
        build_m6_2_structure_diagnostic_report(
            suite_root=suite,
            artifact_root=artifacts,
            dataset=_dataset(),
        )


def test_report_rejects_artifact_render_and_reference_tampering(
    tmp_path: Path,
) -> None:
    suite, artifacts, _report = _synthetic_report(tmp_path)
    artifact_render = (
        artifacts
        / "project-dual_disks/run-dual_disks/candidates/candidate-0002/render.png"
    )
    artifact_render.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="Artifact render"):
        build_m6_2_structure_diagnostic_report(
            suite_root=suite,
            artifact_root=artifacts,
            dataset=_dataset(),
        )

    artifact_render.write_bytes(b"final:dual_disks")
    normalized_reference = (
        artifacts / "project-dual_disks/run-dual_disks/input/reference.png"
    )
    normalized_reference.write_bytes(b"different-normalized-reference")
    with pytest.raises(ValueError, match="normalized reference"):
        build_m6_2_structure_diagnostic_report(
            suite_root=suite,
            artifact_root=artifacts,
            dataset=_dataset(),
        )


def test_report_rejects_case_id_label_misbinding(tmp_path: Path) -> None:
    suite, artifacts, _report = _synthetic_report(tmp_path)
    for relative_path in (
        "report.json",
        "blind-review/assignments.private.json",
        "blind-review/human-review.json",
    ):
        path = suite / relative_path
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item in payload["cases" if relative_path == "report.json" else "items"]:
            if item["case_id"] == "ellipse_gradient":
                item["case_id"] = "solid_circle"
        _write_json(path, payload)

    with pytest.raises(ValueError, match="dataset label 与 source input"):
        build_m6_2_structure_diagnostic_report(
            suite_root=suite,
            artifact_root=artifacts,
            dataset=_dataset(),
        )


@pytest.mark.parametrize(
    ("field_path", "replacement", "message"),
    [
        (("cases", 0, "objective_improvement"), 99.0, "objective_improvement"),
        (
            ("cases", 0, "final_capability", "status"),
            "unsupported",
            "capability status",
        ),
    ],
)
def test_report_rejects_rehashed_semantic_contradictions(
    tmp_path: Path,
    field_path: tuple[str | int, ...],
    replacement: object,
    message: str,
) -> None:
    _suite, _artifacts, report = _synthetic_report(tmp_path)
    payload = report.model_dump(mode="json")
    target: object = payload
    for key in field_path[:-1]:
        target = target[key]  # type: ignore[index]
    target[field_path[-1]] = replacement  # type: ignore[index]
    payload["report_hash"] = compute_m6_2_report_hash(payload)

    with pytest.raises(ValueError, match=message):
        M6_2StructureDiagnosticReport.model_validate_json(
            json.dumps(payload), strict=True
        )


def test_cli_exclusively_creates_outside_source_roots(tmp_path: Path) -> None:
    suite, artifacts, _report = _synthetic_report(tmp_path)
    output = tmp_path / "diagnostics/report-v2.json"
    command = [
        sys.executable,
        "scripts/run_m6_2_structure_diagnostics.py",
        "--suite-output",
        str(suite),
        "--artifact-root",
        str(artifacts),
        "--dataset-manifest",
        str(DATASET_MANIFEST),
        "--output",
        str(output),
    ]

    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    original = output.read_bytes()
    duplicate = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert duplicate.returncode != 0
    assert output.read_bytes() == original

    artifact_output = artifacts / "diagnostic-report.json"
    forbidden = subprocess.run(
        [*command[:-1], str(artifact_output)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert forbidden.returncode != 0
    assert not artifact_output.exists()


def _write_diagnostic_report(
    path: Path, report: M6_2StructureDiagnosticReport
) -> bytes:
    document = (
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(document)
    return document


def test_selector_replay_uses_real_selector_and_fail_closed_admission(
    tmp_path: Path,
) -> None:
    suite, artifacts, diagnostic = _synthetic_report(tmp_path)
    diagnostic_bytes = _write_diagnostic_report(
        tmp_path / "diagnostics/report-capability-v2.json",
        diagnostic,
    )

    replay = build_m6_2_selector_replay_report(
        suite_root=suite,
        artifact_root=artifacts,
        diagnostic=diagnostic,
        diagnostic_document_sha256=sha256(diagnostic_bytes).hexdigest(),
    )

    assert replay.production_enabled is False
    assert replay.schema_version == "png_to_shader_m6_2_seed_admission_replay_v2"
    assert replay.selection_point == "initial_to_affine_seed_counterfactual"
    assert replay.case_count == 2
    assert replay.baseline_accepted_count == 2
    assert replay.admission_rejected_count == 1
    assert replay.initial_preferred_unsupported_rejected_count == 1
    assert replay.supported_admitted_count == 1
    supported = next(item for item in replay.cases if item.capability_status == "supported")
    unsupported = next(
        item for item in replay.cases if item.capability_status == "unsupported"
    )
    assert supported.admission_decision.accepted is True
    assert supported.admission_decision.reason == "improved"
    assert unsupported.admission_decision.accepted is False
    assert (
        unsupported.admission_decision.reason
        == "generator_capability_unsupported"
    )
    assert M6_2SelectorReplayReport.model_validate_json(
        replay.model_dump_json(), strict=True
    ) == replay


@pytest.mark.parametrize("artifact", ("manifest.json", "metrics.json"))
def test_selector_replay_rejects_candidate_or_metrics_tampering(
    tmp_path: Path,
    artifact: str,
) -> None:
    suite, artifacts, diagnostic = _synthetic_report(tmp_path)
    diagnostic_bytes = _write_diagnostic_report(
        tmp_path / "diagnostics/report-capability-v2.json",
        diagnostic,
    )
    path = (
        artifacts
        / "project-dual_disks/run-dual_disks/candidates/candidate-0002"
        / artifact
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if artifact == "manifest.json":
        payload["hard_constraints_passed"] = False
    else:
        payload["total_loss"] = 0.9
    _write_json(path, payload)

    with pytest.raises(ValueError, match="manifest|metrics"):
        build_m6_2_selector_replay_report(
            suite_root=suite,
            artifact_root=artifacts,
            diagnostic=diagnostic,
            diagnostic_document_sha256=sha256(diagnostic_bytes).hexdigest(),
        )


@pytest.mark.parametrize("tamper", ("success_false", "unknown_field"))
def test_selector_replay_rejects_compile_semantic_tampering(
    tmp_path: Path,
    tamper: str,
) -> None:
    suite, artifacts, diagnostic = _synthetic_report(tmp_path)
    diagnostic_bytes = _write_diagnostic_report(
        tmp_path / "diagnostics/report-capability-v2.json",
        diagnostic,
    )
    compile_path = (
        artifacts
        / "project-dual_disks/run-dual_disks/candidates/candidate-0002/compile.json"
    )
    payload = json.loads(compile_path.read_text(encoding="utf-8"))
    if tamper == "success_false":
        payload["success"] = False
    else:
        payload["unexpected"] = "must fail closed"
    _write_json(compile_path, payload)

    with pytest.raises(ValueError):
        build_m6_2_selector_replay_report(
            suite_root=suite,
            artifact_root=artifacts,
            diagnostic=diagnostic,
            diagnostic_document_sha256=sha256(diagnostic_bytes).hexdigest(),
        )


@pytest.mark.parametrize("tamper", ("missing", "bytes"))
def test_selector_replay_requires_source_anchored_suite_config(
    tmp_path: Path,
    tamper: str,
) -> None:
    suite, artifacts, diagnostic = _synthetic_report(tmp_path)
    diagnostic_bytes = _write_diagnostic_report(
        tmp_path / "diagnostics/report-capability-v2.json",
        diagnostic,
    )
    config_path = suite / "config.json"
    if tamper == "missing":
        config_path.unlink()
    else:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["acceptance_policy"]["quality_threshold"] = 0.11
        _write_json(config_path, config)

    with pytest.raises(ValueError, match="config"):
        build_m6_2_selector_replay_report(
            suite_root=suite,
            artifact_root=artifacts,
            diagnostic=diagnostic,
            diagnostic_document_sha256=sha256(diagnostic_bytes).hexdigest(),
        )


def test_selector_replay_rejects_run_policy_different_from_suite_config(
    tmp_path: Path,
) -> None:
    suite, artifacts, _diagnostic = _synthetic_report(tmp_path)
    evidence_path = suite / "cases/dual_disks/ai-on/run-evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["acceptance_policy"]["quality_threshold"] = 0.11
    _write_json(evidence_path, evidence)
    diagnostic = build_m6_2_structure_diagnostic_report(
        suite_root=suite,
        artifact_root=artifacts,
        dataset=_dataset(),
    )
    diagnostic_bytes = _write_diagnostic_report(
        tmp_path / "diagnostics/report-capability-v2.json",
        diagnostic,
    )

    with pytest.raises(ValueError, match="policy.*config"):
        build_m6_2_selector_replay_report(
            suite_root=suite,
            artifact_root=artifacts,
            diagnostic=diagnostic,
            diagnostic_document_sha256=sha256(diagnostic_bytes).hexdigest(),
        )


@pytest.mark.parametrize(
    ("field_path", "replacement", "message"),
    (
        (
            ("cases", 1, "admission_decision", "accepted"),
            True,
            "accepted.*reason",
        ),
        (
            ("cases", 1, "admission_decision", "admission_status"),
            "admitted",
            "status",
        ),
        (
            ("cases", 1, "capability_reason_codes"),
            ["tampered_reason"],
            "reason_codes",
        ),
    ),
)
def test_selector_replay_rejects_rehashed_cross_field_tampering(
    tmp_path: Path,
    field_path: tuple[str | int, ...],
    replacement: object,
    message: str,
) -> None:
    suite, artifacts, diagnostic = _synthetic_report(tmp_path)
    diagnostic_bytes = _write_diagnostic_report(
        tmp_path / "diagnostics/report-capability-v2.json",
        diagnostic,
    )
    replay = build_m6_2_selector_replay_report(
        suite_root=suite,
        artifact_root=artifacts,
        diagnostic=diagnostic,
        diagnostic_document_sha256=sha256(diagnostic_bytes).hexdigest(),
    )
    payload = replay.model_dump(mode="json")
    target: object = payload
    for key in field_path[:-1]:
        target = target[key]  # type: ignore[index]
    target[field_path[-1]] = replacement  # type: ignore[index]
    payload["report_hash"] = compute_m6_2_selector_replay_hash(payload)

    with pytest.raises(ValueError, match=message):
        M6_2SelectorReplayReport.model_validate_json(
            json.dumps(payload),
            strict=True,
        )


def test_selector_replay_cli_exclusive_create_and_input_roots_are_read_only(
    tmp_path: Path,
) -> None:
    suite, artifacts, diagnostic = _synthetic_report(tmp_path)
    diagnostic_path = tmp_path / "diagnostics/report-capability-v2.json"
    _write_diagnostic_report(diagnostic_path, diagnostic)
    output = tmp_path / "replay/report-admission-v2.json"
    command = [
        sys.executable,
        "scripts/run_m6_2_seed_admission_replay.py",
        "--suite-output",
        str(suite),
        "--artifact-root",
        str(artifacts),
        "--diagnostic-report",
        str(diagnostic_path),
        "--output",
        str(output),
    ]

    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    original = output.read_bytes()
    duplicate = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert duplicate.returncode != 0
    assert output.read_bytes() == original

    forbidden_output = artifacts / "replay.json"
    forbidden = subprocess.run(
        [*command[:-1], str(forbidden_output)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert forbidden.returncode != 0
    assert not forbidden_output.exists()
