from __future__ import annotations

import argparse
import asyncio
import copy
import json
import shutil
from hashlib import sha256
from pathlib import Path

import pytest

from scripts.run_png_to_shader_v1_benchmark import (
    INITIAL_SELECTION_POLICY,
    M5_OBJECTIVE_VERSION,
    M5_QUALITY_PRESET_NAMES,
    _bit_identical_case_ids,
    _build_report,
    _build_summary,
    _candidate_generation,
    _CandidateGeneration,
    _evaluate_objective_candidate,
    _m5_quality_preset,
    _objective_pair_fields,
    _report_markdown,
    _resume_model_call_charge,
    _run,
    _select_model_initial,
    _traceability,
    _verify_frozen_blind_review_anchor,
)
from shaderforge.analysis import measure_target
from shaderforge.benchmark import (
    BLIND_REVIEW_EVIDENCE_SCHEMA,
    build_ai_off_shader,
    build_blind_assignments,
    evaluate_quality_gate,
    load_benchmark_suite,
    load_quality_gate_policy,
    verify_blind_review_package,
    verify_legacy_blind_review_package,
    write_blind_review_package,
)
from shaderforge.contracts import QualityPreset
from shaderforge.evaluation import CandidateRecord, ScoreBreakdownV1
from shaderforge.validation import validate_shader

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = ROOT / "benchmarks/png_to_shader_v1"
MANIFEST = BENCHMARK_ROOT / "manifest.yaml"
GATE_POLICY = BENCHMARK_ROOT / "m5_gate.yaml"


def test_m5_quality_presets_remain_frozen_without_online_ultra() -> None:
    assert M5_QUALITY_PRESET_NAMES == ("fast", "balanced", "high")
    assert _m5_quality_preset("high") is QualityPreset.HIGH
    with pytest.raises(ValueError, match="M5 quality preset"):
        _m5_quality_preset("ultra")


class _MemoryArtifactStore:
    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = files or {}

    def read_bytes(self, relative_path: str) -> bytes:
        try:
            return self.files[relative_path]
        except KeyError as exc:
            raise FileNotFoundError(relative_path) from exc


def _score(total_loss: float) -> ScoreBreakdownV1:
    return ScoreBreakdownV1(
        metric_version="test_v1",
        total_loss=total_loss,
        global_rmse=total_loss,
        global_mae=total_loss,
        edge_loss=total_loss,
        geometry_loss=total_loss,
        representative_pixel_loss=total_loss,
        roi_losses=(),
        protected_region_losses=(),
        effective_weights=(),
        diagnostics=(),
    )


def _candidate_record(
    candidate_id: str,
    *,
    iteration: int,
    internal_total_loss: float,
    origin: str = "model",
    generator_version: str | None = None,
) -> CandidateRecord:
    glsl = f"// {candidate_id}".encode()
    return CandidateRecord(
        candidate_id=candidate_id,
        parent_candidate_id=None,
        glsl_sha256=sha256(glsl).hexdigest(),
        glsl_ref=f"{candidate_id}.frag",
        author_ref=f"{candidate_id}.author.json",
        provenance_ref=f"{candidate_id}.provenance.json",
        compile_ref=f"{candidate_id}.compile.json",
        render_ref=f"{candidate_id}.png",
        render_sha256=None,
        metrics_ref=f"{candidate_id}.metrics.json",
        review_ref=None,
        iteration=iteration,
        changed_problem_domain="test",
        prompt_version="test_prompt_v1" if origin == "model" else "",
        model_ref="test:model" if origin == "model" else "",
        score_summary=_score(internal_total_loss),
        hard_constraints_passed=True,
        origin=origin,
        generator_version=generator_version,
    )


def _passing_cases() -> list[dict]:
    cases = []
    for index, case_id in enumerate(
        [
            "solid_circle",
            "ellipse_gradient",
            "shadow_disk",
            "rimmed_disk",
            "arc_highlight_orb",
            "color_lobes",
            "rounded_rect_glow",
            "neon_ring",
            "dual_disks",
            "pink_gel",
        ]
    ):
        initial = 0.30 + index * 0.001
        ai_on = {
            "success": True,
            "final_compile_passed": True,
            "final_static_passed": True,
            "initial_total_loss": initial,
            "final_total_loss": initial - 0.02,
            "final_matches_current_best": True,
            "best_updates_monotonic": True,
            "traceability_passed": True,
            "model_call_count": 6,
            "elapsed_seconds": 10.0,
            "best_update_count": 2,
        }
        if case_id == "pink_gel":
            ai_on.update(
                {
                    "bbox_max_error_uv": 0.03,
                    "global_rmse": 0.10,
                    "key_roi_losses": {
                        "highlight_upper_left": 0.20,
                        "highlight_lower_right": 0.08,
                        "center_haze": 0.03,
                        "shadow": 0.08,
                    },
                }
            )
        cases.append(
            {
                "case_id": case_id,
                "ai_off": {"compile_passed": True, "static_passed": True},
                "ai_on": ai_on,
            }
        )
    return cases


def _valid_review_evidence(
    cases: list[dict],
    *,
    suite_run_id: str = "suite-1",
) -> tuple[dict, dict]:
    assignments = {
        "schema_version": 1,
        "suite_run_id": suite_run_id,
        "items": [
            {
                "case_id": case["case_id"],
                "a_role": "final",
                "b_role": "initial",
                "initial_render_path": f"cases/{case['case_id']}/initial.png",
                "final_render_path": f"cases/{case['case_id']}/final.png",
            }
            for case in cases
        ],
    }
    review = {
        "schema_version": 1,
        "suite_run_id": suite_run_id,
        "reviewer": "reviewer-1",
        "items": [{"case_id": case["case_id"], "choice": "A"} for case in cases],
    }
    return assignments, review


def _ai_on_runner_args(
    output_dir: Path,
    *,
    suite_run_id: str,
    model_call_budget: int = 8,
) -> argparse.Namespace:
    return argparse.Namespace(
        mode="ai-on",
        quality_preset=QualityPreset.BALANCED.value,
        cases="solid_circle,ellipse_gradient",
        suite_run_id=suite_run_id,
        output_dir=output_dir,
        allow_model_calls=True,
        model_call_budget=model_call_budget,
        instruction="",
        human_review=None,
        require_gate_passed=False,
    )


def _patch_model_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scripts.run_png_to_shader_v1_benchmark._structured_model_routing_snapshot",
        lambda: {
            "shader_author": {"model_ref": "test:model"},
            "visual_analysis": {"model_ref": "test:model"},
            "visual_critic": {"model_ref": "test:model"},
        },
    )


def _safe_model_audit(attempt: int) -> dict:
    return {
        "role": "shader_author",
        "mode": "initial",
        "attempt": attempt,
        "requested_model_ref": "test:model",
        "model_ref": "test:model",
        "model_identity_source": "response_metadata",
        "response_format": "json_object",
        "prompt_version": "test_prompt_v1",
        "latency_ms": 10.0,
        "output_sha256": f"{attempt:x}" * 64,
        "parse_status": "success",
        "error_codes": [],
        "validation_issues": [],
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
    }


def test_postprocessing_failure_recovers_actual_calls_and_reduces_remaining_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_model_routing(monkeypatch)
    remaining_values: list[int] = []

    async def injected_case_failure(
        case,
        _suite_root,
        _suite_run_id,
        _preset,
        _instruction,
        remaining_model_calls,
        *,
        failure_context=None,
    ):
        remaining_values.append(remaining_model_calls)
        if case.case_id == "solid_circle":
            assert failure_context is not None
            failure_context.state = {
                "model_calls": tuple(_safe_model_audit(index) for index in range(1, 4)),
                "events": (
                    {
                        "stage": "render_evaluate",
                        "event_type": "current_best_updated",
                        "payload": {"candidate_id": "candidate-1"},
                    },
                ),
                "candidate_records": (
                    _candidate_record(
                        "candidate-1",
                        iteration=0,
                        internal_total_loss=0.2,
                    ),
                ),
                "current_best_id": "candidate-1",
                "final_result": {
                    "success": True,
                    "candidate_id": "candidate-1",
                    "model_call_count": 3,
                    "elapsed_seconds": 1.25,
                },
            }
            raise RuntimeError("provider-secret-must-not-be-persisted")
        return {
            "success": False,
            "failure_reason": "global_model_budget_exhausted",
            "model_call_count": 0,
            "final_compile_passed": False,
            "final_static_passed": False,
            "final_matches_current_best": False,
            "best_updates_monotonic": False,
            "traceability_passed": False,
        }

    monkeypatch.setattr(
        "scripts.run_png_to_shader_v1_benchmark._run_ai_on_case",
        injected_case_failure,
    )

    suite_root, report = asyncio.run(
        _run(
            _ai_on_runner_args(
                tmp_path,
                suite_run_id="suite-postprocess-failure",
            )
        )
    )

    assert suite_root == tmp_path
    assert remaining_values == [8, 5]
    case_result = json.loads(
        (tmp_path / "cases/solid_circle/result.json").read_text(encoding="utf-8")
    )["ai_on"]
    assert case_result["model_call_count"] == 3
    assert case_result["observed_model_call_count"] == 3
    assert case_result["model_call_accounting"] == "recovered_actual"
    assert case_result["input_tokens"] == 30
    assert case_result["output_tokens"] == 15
    assert case_result["total_tokens"] == 45
    assert report["summary"]["model_call_total"] == 3

    evidence = json.loads(
        (tmp_path / case_result["failure_evidence_path"]).read_text(encoding="utf-8")
    )
    assert evidence["model_call_accounting"] == {
        "allocated_model_calls": 8,
        "charged_model_calls": 3,
        "observed_model_calls": 3,
        "strategy": "recovered_actual",
    }
    assert len(evidence["model_calls"]) == 3
    assert evidence["candidate_records"][0]["candidate_id"] == "candidate-1"
    assert "provider-secret-must-not-be-persisted" not in json.dumps(evidence)


def test_failure_without_reliable_state_charges_allocated_case_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_model_routing(monkeypatch)
    remaining_values: list[int] = []

    async def injected_unknown_failure(
        case,
        _suite_root,
        _suite_run_id,
        _preset,
        _instruction,
        remaining_model_calls,
        *,
        failure_context=None,
    ):
        remaining_values.append(remaining_model_calls)
        if case.case_id == "solid_circle":
            assert failure_context is not None
            assert failure_context.state is None
            raise RuntimeError("unknown-provider-secret")
        return {
            "success": False,
            "failure_reason": "global_model_budget_exhausted",
            "model_call_count": 0,
            "final_compile_passed": False,
            "final_static_passed": False,
            "final_matches_current_best": False,
            "best_updates_monotonic": False,
            "traceability_passed": False,
        }

    monkeypatch.setattr(
        "scripts.run_png_to_shader_v1_benchmark._run_ai_on_case",
        injected_unknown_failure,
    )

    _suite_root, report = asyncio.run(
        _run(
            _ai_on_runner_args(
                tmp_path,
                suite_run_id="suite-unknown-failure",
            )
        )
    )

    assert remaining_values == [8, 0]
    case_result = json.loads(
        (tmp_path / "cases/solid_circle/result.json").read_text(encoding="utf-8")
    )["ai_on"]
    assert case_result["model_call_count"] == 8
    assert case_result["observed_model_call_count"] is None
    assert case_result["model_call_accounting"] == "allocated_limit_fail_closed"
    assert report["summary"]["model_call_total"] == 8

    evidence = json.loads(
        (tmp_path / case_result["failure_evidence_path"]).read_text(encoding="utf-8")
    )
    assert evidence["model_call_accounting"] == {
        "allocated_model_calls": 8,
        "charged_model_calls": 8,
        "observed_model_calls": None,
        "strategy": "allocated_limit_fail_closed",
    }
    assert evidence["model_calls"] == []
    assert "unknown-provider-secret" not in json.dumps(evidence)


def test_resume_fail_closes_legacy_exception_result_without_usage_evidence() -> None:
    assert (
        _resume_model_call_charge(
            {
                "success": False,
                "failure_reason": "RuntimeError",
                "model_call_count": 0,
            },
            QualityPreset.BALANCED,
        )
        == 8
    )
    assert (
        _resume_model_call_charge(
            {
                "success": False,
                "failure_reason": "RuntimeError",
                "model_call_count": 3,
                "evidence_path": "cases/example/ai-on/failure.json",
            },
            QualityPreset.BALANCED,
        )
        == 3
    )


def test_manifest_and_gate_policy_are_frozen_and_valid() -> None:
    suite = load_benchmark_suite(MANIFEST)
    policy = load_quality_gate_policy(GATE_POLICY)

    assert suite.suite_id == "png_to_shader_v1_m0"
    assert len(suite.cases) == 10
    assert len({case.case_id for case in suite.cases}) == 10
    assert policy.suite_id == suite.suite_id
    assert policy.required_case_count == 10
    assert policy.min_improvement_rate == 0.70


def test_model_initial_and_deterministic_final_use_same_manifest_objective() -> None:
    suite = load_benchmark_suite(MANIFEST)
    case = next(case for case in suite.cases if case.case_id == "solid_circle")
    reference = case.image_path.read_bytes()
    model = _candidate_record(
        "model-initial",
        iteration=0,
        internal_total_loss=0.01,
    )
    deterministic = _candidate_record(
        "deterministic-final",
        iteration=0,
        internal_total_loss=0.90,
        origin="deterministic",
        generator_version="measurement_affine_seed_v1",
    )
    generations = {
        deterministic.candidate_id: _CandidateGeneration(
            deterministic.origin,
            deterministic.generator_version,
        ),
        model.candidate_id: _CandidateGeneration(model.origin, model.generator_version),
    }

    initial = _select_model_initial((deterministic, model), generations)
    assert initial == model
    initial_objective = _evaluate_objective_candidate(
        case,
        reference,
        (BENCHMARK_ROOT / "images/neon_ring.png").read_bytes(),
    )
    final_objective = _evaluate_objective_candidate(case, reference, reference)
    fields = _objective_pair_fields(
        initial,
        deterministic,
        initial_objective,
        final_objective,
    )

    assert fields["objective_version"] == M5_OBJECTIVE_VERSION
    assert fields["objective_comparable"] is True
    assert fields["initial_total_loss"] == initial_objective.score.total_loss
    assert fields["final_total_loss"] == final_objective.score.total_loss == 0.0
    assert fields["initial_total_loss"] - fields["final_total_loss"] > 0.005
    assert fields["initial_internal_total_loss"] == 0.01
    assert fields["final_internal_total_loss"] == 0.90
    assert generations[deterministic.candidate_id] == _CandidateGeneration(
        "deterministic",
        "measurement_affine_seed_v1",
    )


def test_objective_bbox_uses_manifest_expected_bbox_not_measured_reference() -> None:
    suite = load_benchmark_suite(MANIFEST)
    case = next(case for case in suite.cases if case.case_id == "pink_gel")
    reference = case.image_path.read_bytes()

    objective = _evaluate_objective_candidate(case, reference, reference)
    measured_bbox = measure_target(reference).foreground_bbox_uv
    assert measured_bbox is not None
    expected_error = max(
        abs(expected - measured)
        for expected, measured in zip(
            case.expected_foreground_bbox_uv,
            measured_bbox,
            strict=True,
        )
    )

    assert expected_error > 0.0
    assert objective.bbox_max_error_uv == pytest.approx(expected_error)


def test_no_model_initial_is_incomparable_and_excluded_from_improvement() -> None:
    suite = load_benchmark_suite(MANIFEST)
    case = next(case for case in suite.cases if case.case_id == "solid_circle")
    deterministic = _candidate_record(
        "deterministic-only",
        iteration=0,
        internal_total_loss=0.0,
        origin="deterministic",
        generator_version="measurement_affine_seed_v1",
    )
    generations = {
        deterministic.candidate_id: _CandidateGeneration(
            deterministic.origin,
            deterministic.generator_version,
        )
    }

    initial = _select_model_initial((deterministic,), generations)
    final_objective = _evaluate_objective_candidate(
        case,
        case.image_path.read_bytes(),
        case.image_path.read_bytes(),
    )
    fields = _objective_pair_fields(None, deterministic, None, final_objective)
    summary = _build_summary(
        [{"case_id": case.case_id, "ai_off": {}, "ai_on": fields}],
        minimum_improvement=0.005,
    )

    assert initial is None
    assert fields["objective_comparable"] is False
    assert fields["initial_total_loss"] is None
    assert fields["final_total_loss"] == 0.0
    assert summary["improvement_comparable_case_count"] == 0
    assert summary["improved_case_count"] == 0
    assert summary["improvement_rate"] is None


def test_deterministic_generation_requires_version_for_traceability() -> None:
    record = _candidate_record(
        "deterministic",
        iteration=0,
        internal_total_loss=0.0,
        origin="deterministic",
        generator_version="measurement_affine_seed_v1",
    )
    glsl = b"// deterministic"
    record = CandidateRecord(
        **{
            **record.to_dict(),
            "glsl_sha256": sha256(glsl).hexdigest(),
            "score_summary": record.score_summary,
            "hard_constraints_passed": False,
        }
    )
    store = _MemoryArtifactStore(
        {
            record.glsl_ref: glsl,
            record.author_ref: b"{}",
            record.provenance_ref: b"{}",
            str(record.compile_ref): b"{}",
        }
    )
    generation = _candidate_generation(store, record, record)

    passed, errors = _traceability(
        store,
        (record,),
        {record.candidate_id: generation},
        {},
    )
    missing_version_passed, missing_version_errors = _traceability(
        store,
        (record,),
        {record.candidate_id: _CandidateGeneration(record.origin, None)},
        {},
    )
    forged_origin_passed, forged_origin_errors = _traceability(
        store,
        (record,),
        {record.candidate_id: _CandidateGeneration("deterministic-forged", None)},
        {},
    )

    assert generation == _CandidateGeneration(
        "deterministic",
        "measurement_affine_seed_v1",
    )
    assert passed is True
    assert errors == []
    assert missing_version_passed is False
    assert "deterministic:generator_version_missing" in missing_version_errors
    assert forged_origin_passed is False
    assert "deterministic:origin_unsupported" in forged_origin_errors

    conflicting_store = _MemoryArtifactStore(
        {record.provenance_ref: b'{"origin":"model"}'}
    )
    with pytest.raises(ValueError, match="origin 证据不一致"):
        _candidate_generation(conflicting_store, record, record)


def test_ai_off_shader_is_static_textureless_webgl1() -> None:
    image = (BENCHMARK_ROOT / "images/pink_gel.png").read_bytes()
    shader = build_ai_off_shader(measure_target(image))
    validation = validate_shader(shader)

    assert validation.valid is True
    assert "texture2D" not in shader
    assert "ai_off_baseline: measurement_ellipse_v1" in shader


def test_quality_gate_requires_human_review_after_automatic_checks_pass() -> None:
    policy = load_quality_gate_policy(GATE_POLICY)
    result = evaluate_quality_gate(_passing_cases(), policy)

    assert result.status == "pending_human_review"
    assert all(check.passed for check in result.checks[:-2])
    assert result.checks[-2].check_id == "human_blind_review_count"


def test_quality_gate_decodes_blind_assignments_and_passes() -> None:
    policy = load_quality_gate_policy(GATE_POLICY)
    cases = _passing_cases()
    assignments, review = _valid_review_evidence(cases)

    result = evaluate_quality_gate(
        cases,
        policy,
        human_review=review,
        assignments=assignments,
        expected_suite_run_id="suite-1",
        bit_identical_case_ids=("solid_circle", "pink_gel"),
    )

    assert result.status == "passed"
    assert result.summary["human_final_preference_rate"] == 1.0
    assert result.summary["final_win_count"] == 10
    assert result.summary["initial_win_count"] == 0
    assert result.summary["tie_count"] == 0
    assert result.summary["distinct_pair_count"] == 8
    assert result.summary["bit_identical_case_ids"] == ["solid_circle", "pink_gel"]


def test_quality_gate_reports_final_initial_and_tie_counts() -> None:
    policy = load_quality_gate_policy(GATE_POLICY)
    cases = _passing_cases()
    assignments, review = _valid_review_evidence(cases)
    for index, item in enumerate(review["items"]):
        item["choice"] = "A" if index < 5 else "B" if index < 7 else "TIE"

    result = evaluate_quality_gate(
        cases,
        policy,
        human_review=review,
        assignments=assignments,
        expected_suite_run_id="suite-1",
        bit_identical_case_ids=tuple(case["case_id"] for case in cases[5:]),
    )

    assert result.summary["human_final_preference_rate"] == 0.5
    assert result.summary["final_win_count"] == 5
    assert result.summary["initial_win_count"] == 2
    assert result.summary["tie_count"] == 3
    assert result.summary["distinct_pair_count"] == 5


@pytest.mark.parametrize(
    "invalid_kind",
    (
        "assignment_schema",
        "assignment_suite",
        "assignment_duplicate",
        "assignment_missing",
        "assignment_extra",
        "assignment_roles",
        "assignment_path",
        "review_schema",
        "review_suite",
        "reviewer",
        "review_duplicate",
        "review_missing",
        "review_extra",
        "review_choice",
    ),
)
def test_quality_gate_rejects_invalid_human_review_evidence(
    invalid_kind: str,
) -> None:
    policy = load_quality_gate_policy(GATE_POLICY)
    cases = _passing_cases()
    assignments, review = _valid_review_evidence(cases)
    assignments = copy.deepcopy(assignments)
    review = copy.deepcopy(review)

    if invalid_kind == "assignment_schema":
        assignments["schema_version"] = 2
    elif invalid_kind == "assignment_suite":
        assignments["suite_run_id"] = "other-suite"
    elif invalid_kind == "assignment_duplicate":
        assignments["items"].append(copy.deepcopy(assignments["items"][0]))
    elif invalid_kind == "assignment_missing":
        assignments["items"].pop()
    elif invalid_kind == "assignment_extra":
        assignments["items"].append(
            {
                **copy.deepcopy(assignments["items"][0]),
                "case_id": "unknown-case",
            }
        )
    elif invalid_kind == "assignment_roles":
        assignments["items"][0]["b_role"] = "final"
    elif invalid_kind == "assignment_path":
        cases[0]["ai_on"]["initial_render_path"] = "expected/initial.png"
        cases[0]["ai_on"]["final_render_path"] = "expected/final.png"
    elif invalid_kind == "review_schema":
        review["schema_version"] = 2
    elif invalid_kind == "review_suite":
        review["suite_run_id"] = "other-suite"
    elif invalid_kind == "reviewer":
        review["reviewer"] = " "
    elif invalid_kind == "review_duplicate":
        review["items"].append(copy.deepcopy(review["items"][0]))
    elif invalid_kind == "review_missing":
        review["items"].pop()
    elif invalid_kind == "review_extra":
        review["items"].append({"case_id": "unknown-case", "choice": "TIE"})
    elif invalid_kind == "review_choice":
        review["items"][0]["choice"] = "FINAL"

    with pytest.raises(ValueError):
        evaluate_quality_gate(
            cases,
            policy,
            human_review=review,
            assignments=assignments,
            expected_suite_run_id="suite-1",
        )


def test_quality_gate_rejects_review_without_assignments() -> None:
    policy = load_quality_gate_policy(GATE_POLICY)
    cases = _passing_cases()
    _, review = _valid_review_evidence(cases)

    with pytest.raises(ValueError, match="assignments"):
        evaluate_quality_gate(cases, policy, human_review=review)


def test_bit_identical_case_ids_uses_frozen_render_bytes(tmp_path: Path) -> None:
    same_initial = tmp_path / "cases/same/initial.png"
    same_final = tmp_path / "cases/same/final.png"
    changed_initial = tmp_path / "cases/changed/initial.png"
    changed_final = tmp_path / "cases/changed/final.png"
    for path, content in (
        (same_initial, b"same"),
        (same_final, b"same"),
        (changed_initial, b"initial"),
        (changed_final, b"final"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    cases = [
        {
            "case_id": case_id,
            "ai_on": {
                "initial_render_path": f"cases/{case_id}/initial.png",
                "final_render_path": f"cases/{case_id}/final.png",
            },
        }
        for case_id in ("same", "changed")
    ]

    assert _bit_identical_case_ids(tmp_path, cases) == ("same",)


def test_quality_gate_fails_when_improvement_rate_is_below_seventy_percent() -> None:
    policy = load_quality_gate_policy(GATE_POLICY)
    cases = _passing_cases()
    for case in cases[:4]:
        case["ai_on"]["final_total_loss"] = case["ai_on"]["initial_total_loss"]

    result = evaluate_quality_gate(cases, policy)

    assert result.status == "failed"
    improvement = next(
        check for check in result.checks if check.check_id == "metric_improvement_rate"
    )
    assert improvement.actual == 0.6
    assert improvement.passed is False


def test_blind_review_page_hides_role_mapping_and_writes_private_assignment(
    tmp_path: Path,
) -> None:
    source = BENCHMARK_ROOT / "images/solid_circle.png"
    case_root = tmp_path / "cases/solid_circle"
    case_root.mkdir(parents=True)
    shutil.copyfile(source, case_root / "reference.png")
    shutil.copyfile(source, case_root / "initial.png")
    shutil.copyfile(source, case_root / "final.png")
    cases = [
        {
            "case_id": "solid_circle",
            "ai_on": {
                "initial_render_path": "cases/solid_circle/initial.png",
                "final_render_path": "cases/solid_circle/final.png",
            },
        }
    ]

    index = write_blind_review_package(tmp_path, "suite-1", cases)
    html = index.read_text(encoding="utf-8")
    assignments = json.loads(
        (tmp_path / "blind-review/assignments.private.json").read_text()
    )
    manifest = json.loads(
        (tmp_path / "blind-review/evidence-manifest.json").read_text()
    )
    reviewer_root = tmp_path / "blind-review/reviewer"

    assert index == reviewer_root / "index.html"
    assert "候选 A" in html
    assert "候选 B" in html
    assert "参考目标" in html
    assert "a_role" not in html
    assert assignments["items"][0]["a_role"] in {"initial", "final"}
    assert not (reviewer_root / "assignments.private.json").exists()
    assert not tuple(reviewer_root.rglob("assignments.private.json"))
    assert (reviewer_root / "assets/solid_circle-a.png").is_file()
    assert (reviewer_root / "assets/solid_circle-reference.png").is_file()
    assert manifest["schema_version"] == BLIND_REVIEW_EVIDENCE_SCHEMA
    assert manifest["reviewer_root"] == "blind-review/reviewer"
    assert manifest["assignment_path"] == "blind-review/assignments.private.json"
    assert len(manifest["entries"]) == 9
    verify_blind_review_package(tmp_path, expected_suite_run_id="suite-1")

    (reviewer_root / "assignments.private.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="不得包含 assignments.private.json"):
        verify_blind_review_package(tmp_path, expected_suite_run_id="suite-1")


@pytest.mark.parametrize(
    "relative_path",
    (
        "cases/solid_circle/initial.png",
        "blind-review/reviewer/assets/solid_circle-a.png",
        "blind-review/reviewer/index.html",
        "blind-review/reviewer/human-review.template.json",
        "blind-review/assignments.private.json",
    ),
)
def test_blind_review_manifest_rejects_frozen_evidence_drift(
    tmp_path: Path,
    relative_path: str,
) -> None:
    source = BENCHMARK_ROOT / "images/solid_circle.png"
    case_root = tmp_path / "cases/solid_circle"
    case_root.mkdir(parents=True)
    for name in ("reference.png", "initial.png", "final.png"):
        shutil.copyfile(source, case_root / name)
    cases = [
        {
            "case_id": "solid_circle",
            "ai_on": {
                "initial_render_path": "cases/solid_circle/initial.png",
                "final_render_path": "cases/solid_circle/final.png",
            },
        }
    ]
    write_blind_review_package(tmp_path, "suite-1", cases)
    drifted = tmp_path / relative_path
    drifted.write_bytes(drifted.read_bytes() + b"drift")

    with pytest.raises(ValueError, match="漂移"):
        verify_blind_review_package(tmp_path, expected_suite_run_id="suite-1")


def test_blind_review_package_never_overwrites_frozen_evidence(tmp_path: Path) -> None:
    source = BENCHMARK_ROOT / "images/solid_circle.png"
    case_root = tmp_path / "cases/solid_circle"
    case_root.mkdir(parents=True)
    for name in ("reference.png", "initial.png", "final.png"):
        shutil.copyfile(source, case_root / name)
    cases = [
        {
            "case_id": "solid_circle",
            "ai_on": {
                "initial_render_path": "cases/solid_circle/initial.png",
                "final_render_path": "cases/solid_circle/final.png",
            },
        }
    ]
    write_blind_review_package(tmp_path, "suite-1", cases)
    asset = tmp_path / "blind-review/reviewer/assets/solid_circle-a.png"
    asset.write_bytes(b"preserve-this-drifted-evidence")

    with pytest.raises(ValueError, match="拒绝覆盖|内容不一致|冻结"):
        write_blind_review_package(tmp_path, "suite-1", cases)
    assert asset.read_bytes() == b"preserve-this-drifted-evidence"


def test_evaluate_anchor_rejects_rehashed_tampered_manifest(tmp_path: Path) -> None:
    suite = load_benchmark_suite(MANIFEST)
    source = BENCHMARK_ROOT / "images/solid_circle.png"
    case_root = tmp_path / "cases/solid_circle"
    case_root.mkdir(parents=True)
    for name in ("reference.png", "initial.png", "final.png"):
        shutil.copyfile(source, case_root / name)
    cases = [
        {
            "case_id": "solid_circle",
            "ai_on": {
                "initial_render_path": "cases/solid_circle/initial.png",
                "final_render_path": "cases/solid_circle/final.png",
            },
        }
    ]
    write_blind_review_package(tmp_path, "suite-1", cases)
    config = {
        "schema_version": 3,
        "suite_run_id": "suite-1",
        "suite_id": suite.suite_id,
        "manifest_sha256": suite.manifest_sha256,
        "gate_policy_sha256": sha256(GATE_POLICY.read_bytes()).hexdigest(),
        "blind_review_evidence_schema": BLIND_REVIEW_EVIDENCE_SCHEMA,
        "objective_version": M5_OBJECTIVE_VERSION,
        "initial_selection_policy": INITIAL_SELECTION_POLICY,
        "case_ids": [case.case_id for case in suite.cases],
        "quality_preset": QualityPreset.BALANCED.value,
        "model_call_budget": 80,
        "model_ref": None,
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    manifest_path = tmp_path / "blind-review/evidence-manifest.json"
    original_manifest_sha256 = sha256(manifest_path.read_bytes()).hexdigest()
    report = {
        "suite_run_id": "suite-1",
        "suite_id": suite.suite_id,
        "config_schema_version": 3,
        "config_sha256": sha256(config_path.read_bytes()).hexdigest(),
        "manifest_sha256": suite.manifest_sha256,
        "gate_policy_sha256": sha256(GATE_POLICY.read_bytes()).hexdigest(),
        "blind_review_evidence_schema": BLIND_REVIEW_EVIDENCE_SCHEMA,
        "blind_review_manifest_path": "blind-review/evidence-manifest.json",
        "blind_review_manifest_sha256": original_manifest_sha256,
        "blind_review_reviewer_path": "blind-review/reviewer/index.html",
    }
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")

    asset_path = tmp_path / "blind-review/reviewer/assets/solid_circle-a.png"
    asset_path.write_bytes(asset_path.read_bytes() + b"tampered")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    asset_relative = "blind-review/reviewer/assets/solid_circle-a.png"
    asset_entry = next(
        entry for entry in manifest["entries"] if entry["path"] == asset_relative
    )
    asset_entry["byte_size"] = asset_path.stat().st_size
    asset_entry["sha256"] = sha256(asset_path.read_bytes()).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    verify_blind_review_package(tmp_path, expected_suite_run_id="suite-1")

    with pytest.raises(ValueError, match="report.json 锚点不一致"):
        _verify_frozen_blind_review_anchor(
            suite_root=tmp_path,
            config_path=config_path,
            config=config,
            suite_run_id="suite-1",
        )

    human_review_path = tmp_path / "invalid-human-review.json"
    human_review_path.write_text("{not-json", encoding="utf-8")
    report_path = tmp_path / "report.json"
    report_markdown_path = tmp_path / "report.md"
    report_markdown_path.write_text("frozen report\n", encoding="utf-8")
    frozen_report_bytes = report_path.read_bytes()
    frozen_markdown_bytes = report_markdown_path.read_bytes()
    args = argparse.Namespace(
        mode="evaluate",
        human_review=human_review_path,
        cases=None,
        output_dir=tmp_path,
        suite_run_id=None,
    )

    with pytest.raises(ValueError, match="report.json 锚点不一致"):
        asyncio.run(_run(args))
    assert report_path.read_bytes() == frozen_report_bytes
    assert report_markdown_path.read_bytes() == frozen_markdown_bytes


def test_legacy_blind_review_verification_keeps_frozen_run_compatible(
    tmp_path: Path,
) -> None:
    source = BENCHMARK_ROOT / "images/solid_circle.png"
    case_root = tmp_path / "cases/solid_circle"
    review_root = tmp_path / "blind-review"
    assets = review_root / "assets"
    case_root.mkdir(parents=True)
    assets.mkdir(parents=True)
    for name in ("reference.png", "initial.png", "final.png"):
        shutil.copyfile(source, case_root / name)
    cases = [
        {
            "case_id": "solid_circle",
            "ai_on": {
                "initial_render_path": "cases/solid_circle/initial.png",
                "final_render_path": "cases/solid_circle/final.png",
            },
        }
    ]
    assignments = build_blind_assignments("suite-1", cases)
    item = assignments["items"][0]
    role_to_path = {
        "initial": case_root / "initial.png",
        "final": case_root / "final.png",
    }
    shutil.copyfile(case_root / "reference.png", assets / "solid_circle-reference.png")
    shutil.copyfile(role_to_path[item["a_role"]], assets / "solid_circle-a.png")
    shutil.copyfile(role_to_path[item["b_role"]], assets / "solid_circle-b.png")
    public_items = [
        {
            "case_id": "solid_circle",
            "reference_image": "assets/solid_circle-reference.png",
            "a_image": "assets/solid_circle-a.png",
            "b_image": "assets/solid_circle-b.png",
        }
    ]
    (review_root / "assignments.private.json").write_text(
        json.dumps(assignments, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (review_root / "human-review.template.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "suite_run_id": "suite-1",
                "reviewer": "human-reviewer",
                "items": [{"case_id": "solid_circle", "choice": "A|B|TIE"}],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    from shaderforge.benchmark.blind_review import _review_html_v1

    (review_root / "index.html").write_text(
        _review_html_v1(public_items, "suite-1"),
        encoding="utf-8",
    )
    (review_root / "human-review.completed.json").write_text(
        '{"archived": true}\n',
        encoding="utf-8",
    )

    verify_legacy_blind_review_package(tmp_path, "suite-1", cases)
    assert not (review_root / "evidence-manifest.json").exists()

    (assets / "assignments.private.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="assets 文件集合"):
        verify_legacy_blind_review_package(tmp_path, "suite-1", cases)


def test_report_distinguishes_configured_snapshot_from_audited_model(
    tmp_path: Path,
) -> None:
    suite = load_benchmark_suite(MANIFEST)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "model_routing": {
                    "shader_author": {"model_ref": "configured:fallback"}
                },
            }
        ),
        encoding="utf-8",
    )
    evidence_path = tmp_path / "cases/solid_circle/ai-on/run-evidence.json"
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_text(
        json.dumps(
            {
                "model_calls": [
                    {
                        "requested_model_ref": "dashscope:qwen3.7-plus",
                        "model_ref": "dashscope:qwen3.7-plus",
                        "model_identity_source": "response_metadata",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    human_review_path = tmp_path / "human-review.json"
    human_review_path.write_text(
        json.dumps({"schema_version": 1, "items": []}),
        encoding="utf-8",
    )
    report = _build_report(
        suite=suite,
        suite_root=tmp_path,
        suite_run_id="suite-model-audit",
        mode="ai-on",
        preset=QualityPreset.BALANCED,
        model_ref="configured:fallback",
        model_call_budget=8,
        case_results=[
            {
                "case_id": "solid_circle",
                "ai_off": {},
                "ai_on": {
                    "evidence_path": "cases/solid_circle/ai-on/run-evidence.json"
                },
            }
        ],
        human_review_path=human_review_path,
    )

    assert report["model_ref"] == "configured:fallback"
    assert report["requested_model_refs"] == ["dashscope:qwen3.7-plus"]
    assert report["actual_model_refs"] == ["dashscope:qwen3.7-plus"]
    assert report["model_identity_sources"] == ["response_metadata"]
    assert report["config_sha256"] == sha256(config_path.read_bytes()).hexdigest()
    assert (
        report["human_review_sha256"]
        == sha256(human_review_path.read_bytes()).hexdigest()
    )
    markdown = _report_markdown(report)
    assert "configured model snapshot: `configured:fallback`" in markdown
    assert "audited actual models: `dashscope:qwen3.7-plus`" in markdown

    reviewed_markdown = _report_markdown(
        {
            **report,
            "quality_gate": {
                "status": "failed",
                "checks": [],
                "summary": {
                    "human_review_count": 10,
                    "human_final_preference_rate": 0.1,
                    "final_win_count": 1,
                    "initial_win_count": 0,
                    "tie_count": 9,
                    "distinct_pair_count": 1,
                    "bit_identical_case_ids": ["solid_circle", "pink_gel"],
                },
            },
        }
    )
    assert "已载入 `10` 项人工盲评" in reviewed_markdown
    assert "final 偏好率为 `0.100`" in reviewed_markdown
    assert "final/initial/tie 为 `1/0/9`" in reviewed_markdown
    assert "候选图对中 `1` 项不同" in reviewed_markdown


def test_report_schema_v3_exposes_objective_and_candidate_generation(
    tmp_path: Path,
) -> None:
    suite = load_benchmark_suite(MANIFEST)
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "objective_version": M5_OBJECTIVE_VERSION,
                "initial_selection_policy": INITIAL_SELECTION_POLICY,
                "model_routing": None,
            }
        ),
        encoding="utf-8",
    )
    ai_on = {
        "success": True,
        "objective_version": M5_OBJECTIVE_VERSION,
        "initial_selection_policy": INITIAL_SELECTION_POLICY,
        "initial_origin": "model",
        "final_origin": "deterministic",
        "final_generator_version": "measurement_affine_seed_v1",
        "initial_total_loss": 0.2,
        "final_total_loss": 0.1,
    }

    report = _build_report(
        suite=suite,
        suite_root=tmp_path,
        suite_run_id="suite-objective-v3",
        mode="ai-on",
        preset=QualityPreset.BALANCED,
        model_ref=None,
        model_call_budget=8,
        case_results=[
            {
                "case_id": "solid_circle",
                "ai_off": {},
                "ai_on": ai_on,
            }
        ],
        human_review_path=None,
    )

    assert report["schema_version"] == 3
    assert report["objective_version"] == M5_OBJECTIVE_VERSION
    assert report["initial_selection_policy"] == INITIAL_SELECTION_POLICY
    assert report["blind_review_reviewer_path"] is None
    markdown = _report_markdown(report)
    assert (
        "| solid_circle | model | deterministic@measurement_affine_seed_v1 |"
        in markdown
    )


def test_ai_off_markdown_does_not_claim_missing_ai_on_or_blind_review() -> None:
    markdown = _report_markdown(
        {
            "mode": "ai-off",
            "summary": {
                "case_count": 10,
                "ai_off_static_pass_count": 10,
                "ai_off_compile_pass_count": 10,
                "ai_on_final_compile_pass_count": 0,
            },
            "cases": [
                {
                    "case_id": "solid_circle",
                    "ai_off": {
                        "static_passed": True,
                        "compile_passed": True,
                    },
                    "ai_on": {},
                }
            ],
        }
    )

    assert "AI-off static: `10/10`" in markdown
    assert "AI-off compile: `10/10`" in markdown
    assert "AI-off smoke 不生成模型候选图对或人工盲评包" in markdown
    assert "打开 `blind-review/index.html`" not in markdown
    assert "AI-on compile" not in markdown
    assert "| solid_circle | yes | yes | — | — | — |" in markdown
