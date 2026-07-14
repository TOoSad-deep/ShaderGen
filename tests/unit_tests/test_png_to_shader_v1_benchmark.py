from __future__ import annotations

import json
import shutil
from hashlib import sha256
from pathlib import Path

from scripts.run_png_to_shader_v1_benchmark import _build_report, _report_markdown
from shaderforge.analysis import measure_target
from shaderforge.benchmark import (
    build_ai_off_shader,
    evaluate_quality_gate,
    load_benchmark_suite,
    load_quality_gate_policy,
    write_blind_review_package,
)
from shaderforge.contracts import QualityPreset
from shaderforge.validation import validate_shader

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = ROOT / "benchmarks/png_to_shader_v1"
MANIFEST = BENCHMARK_ROOT / "manifest.yaml"
GATE_POLICY = BENCHMARK_ROOT / "m5_gate.yaml"


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


def test_manifest_and_gate_policy_are_frozen_and_valid() -> None:
    suite = load_benchmark_suite(MANIFEST)
    policy = load_quality_gate_policy(GATE_POLICY)

    assert suite.suite_id == "png_to_shader_v1_m0"
    assert len(suite.cases) == 10
    assert len({case.case_id for case in suite.cases}) == 10
    assert policy.suite_id == suite.suite_id
    assert policy.required_case_count == 10
    assert policy.min_improvement_rate == 0.70


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
    assignments = {
        "items": [
            {"case_id": case["case_id"], "a_role": "final", "b_role": "initial"}
            for case in cases
        ]
    }
    review = {
        "items": [
            {"case_id": case["case_id"], "choice": "A"} for case in cases
        ]
    }

    result = evaluate_quality_gate(
        cases,
        policy,
        human_review=review,
        assignments=assignments,
    )

    assert result.status == "passed"
    assert result.summary["human_final_preference_rate"] == 1.0


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

    assert "候选 A" in html
    assert "候选 B" in html
    assert "参考目标" in html
    assert "a_role" not in html
    assert assignments["items"][0]["a_role"] in {"initial", "final"}
    assert (tmp_path / "blind-review/assets/solid_circle-a.png").is_file()
    assert (
        tmp_path / "blind-review/assets/solid_circle-reference.png"
    ).is_file()


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
    assert report["human_review_sha256"] == sha256(
        human_review_path.read_bytes()
    ).hexdigest()
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
                },
            },
        }
    )
    assert "已载入 `10` 项人工盲评" in reviewed_markdown
    assert "final 偏好率为 `0.100`" in reviewed_markdown
