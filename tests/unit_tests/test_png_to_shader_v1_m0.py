from __future__ import annotations

import struct
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from shaderforge.public import (
    DEFAULT_ACCEPTANCE_POLICY,
    PROBLEM_DOMAINS,
    QUALITY_PRESETS,
    STOP_REASONS,
    WEBGL1_STATIC_NO_TEXTURE_V1,
    AcceptancePolicy,
    BudgetPolicy,
    ProblemDomain,
    QualityPreset,
    RenderContract,
    StopReason,
    budget_for_preset,
)

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = ROOT / "benchmarks/png_to_shader_v1"


def test_webgl1_no_texture_contract_is_frozen() -> None:
    contract = WEBGL1_STATIC_NO_TEXTURE_V1

    assert contract.contract_id == "webgl1_static_no_texture_v1"
    assert contract.glsl_version == "GLSL_ES_100"
    assert contract.precision == "mediump"
    assert contract.varying_name == "v_uv"
    assert contract.fragment_output == "gl_FragColor"
    assert contract.uv_origin == "bottom_left"
    assert contract.texture_sampling_allowed is False
    assert contract.animation_enabled is False
    assert ("u_image", "sampler2D") in contract.required_uniforms
    assert ("u_resolution", "vec2") in contract.required_uniforms
    assert ("u_time", "float") in contract.required_uniforms
    assert "texture2D" in contract.forbidden_tokens
    assert contract.to_dict()["contract_id"] == contract.contract_id


def test_problem_domains_and_stop_reasons_are_complete() -> None:
    assert PROBLEM_DOMAINS == tuple(item.value for item in ProblemDomain)
    assert STOP_REASONS == tuple(item.value for item in StopReason)
    assert set(PROBLEM_DOMAINS) == {
        "runtime_compile",
        "geometry",
        "background_shadow",
        "base_color_field",
        "rim_edge",
        "highlight",
        "fine_detail",
        "global_balance",
    }
    assert "quality_threshold_met" in STOP_REASONS
    assert "completed_with_best_effort" in STOP_REASONS


def test_quality_presets_are_bounded_and_balanced_is_default_shape() -> None:
    assert set(QUALITY_PRESETS) == set(QualityPreset)
    balanced = budget_for_preset("balanced")

    assert balanced == BudgetPolicy(
        max_visual_refinements=2,
        max_compile_repairs=2,
        max_model_calls=8,
        max_wall_time_seconds=300,
    )
    assert budget_for_preset(QualityPreset.FAST).max_model_calls < balanced.max_model_calls
    assert budget_for_preset(QualityPreset.HIGH).max_model_calls > balanced.max_model_calls
    assert DEFAULT_ACCEPTANCE_POLICY == AcceptancePolicy()


def test_contract_and_policy_reject_invalid_values() -> None:
    with pytest.raises(ValueError, match="max_long_side"):
        RenderContract(
            contract_id="invalid",
            glsl_version="GLSL_ES_100",
            precision="mediump",
            varying_name="v_uv",
            required_uniforms=(),
            fragment_output="gl_FragColor",
            uv_origin="bottom_left",
            texture_sampling_allowed=False,
            animation_enabled=False,
            max_long_side=0,
            required_declarations=(),
            forbidden_tokens=(),
        )
    with pytest.raises(ValueError, match="quality preset"):
        budget_for_preset("unbounded")
    with pytest.raises(ValueError, match="stagnation_rounds"):
        AcceptancePolicy(stagnation_rounds=0)


def test_benchmark_manifest_has_ten_valid_png_cases() -> None:
    manifest = yaml.safe_load((BENCHMARK_ROOT / "manifest.yaml").read_text())
    cases = manifest["cases"]

    assert manifest["schema_version"] == 1
    assert manifest["contract_id"] == WEBGL1_STATIC_NO_TEXTURE_V1.contract_id
    assert 8 <= len(cases) <= 12
    assert len(cases) == 10
    assert len({case["id"] for case in cases}) == len(cases)

    for case in cases:
        image_path = BENCHMARK_ROOT / case["image"]
        image = image_path.read_bytes()
        assert image.startswith(b"\x89PNG\r\n\x1a\n")
        assert sha256(image).hexdigest() == case["sha256"]
        width, height = struct.unpack(">II", image[16:24])
        assert [width, height] == case["resolution"]
        assert case["target_features"]
        assert case["expected_primitives"]
        assert 0.0 < case["max_bbox_error_uv"] <= 0.1
        for value in case["expected_foreground_bbox_uv"]:
            assert 0.0 <= value <= 1.0
        for roi in case["key_rois"]:
            assert roi["id"]
            assert roi["purpose"]
            assert len(roi["bbox_uv"]) == 4
            assert all(0.0 <= value <= 1.0 for value in roi["bbox_uv"])


def test_f09_is_the_only_active_feature() -> None:
    feature_lines = [
        line
        for line in (ROOT / "docs/FEATURES.md").read_text().splitlines()
        if line.startswith("| ") and "| active |" in line
    ]

    assert len(feature_lines) == 1
    assert feature_lines[0].startswith("| F09 |")
