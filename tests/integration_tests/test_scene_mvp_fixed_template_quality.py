"""scene_mvp 固定模板扩展的 7 例同口径外部质量回归。."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shaderforge.analysis import RegionOfInterest
from shaderforge.benchmark import load_benchmark_suite
from shaderforge.evaluation import evaluate_render
from shaderforge.generation import MIN_TEMPLATE_VERSION, materialize_min_shader
from shaderforge.perception import perceive_min_target
from shaderforge.rendering import PlaywrightWebGL1Renderer

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = ROOT / "benchmarks/png_to_shader_v1"
BASELINE_PATH = BENCHMARK_ROOT / "scene_mvp_fixed_template_v3_baseline.json"


@pytest.mark.anyio
async def test_fixed_template_v3_improves_frozen_v2_baseline_on_supported_cases() -> None:
    """真实 Chromium 下至少 5/7 改善，其他例不得越过冻结回归容差。."""
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    suite = load_benchmark_suite(BENCHMARK_ROOT / "manifest.yaml")
    supported = baseline["supported_cases"]
    cases = [case for case in suite.cases if case.case_id in supported]
    assert len(cases) == len(supported) == 7
    assert MIN_TEMPLATE_VERSION == baseline["candidate_template_version"]
    improved = 0

    async with PlaywrightWebGL1Renderer() as renderer:
        for case in cases:
            reference = case.image_path.read_bytes()
            perception = perceive_min_target(reference)
            frozen = supported[case.case_id]
            assert perception.fallback_scene.object.color_field.model == (
                frozen["expected_color_field_model"]
            )
            materialized = materialize_min_shader(perception.fallback_scene)
            prepared = await renderer.prepare(
                materialized.webgl1_source,
                perception.width,
                perception.height,
                materialized.uniform_schema,
            )
            result = await prepared.render_uniforms(
                materialized.uniform_values, capture_png=True
            )
            assert result.success and result.image_bytes is not None
            regions = tuple(
                RegionOfInterest(
                    region_id=region.region_id,
                    bbox_uv=region.bbox_uv,
                    purpose=region.purpose,
                    confidence=1.0,
                )
                for region in case.key_rois
            )
            score = evaluate_render(reference, result.image_bytes, regions=regions)
            if score.total_loss < (
                frozen["total_loss"] - baseline["improvement_epsilon"]
            ):
                improved += 1
            assert score.total_loss <= (
                frozen["total_loss"] + baseline["max_total_loss_regression"]
            )
            assert score.geometry_loss is not None
            assert score.geometry_loss <= (
                frozen["geometry_loss"] + baseline["max_geometry_loss_regression"]
            )
            for region_id, loss in score.roi_losses:
                assert loss <= (
                    frozen["roi_losses"][region_id]
                    + baseline["max_roi_loss_regression"]
                )

    assert improved >= baseline["minimum_improved_cases"]


def test_quality_baseline_excludes_known_out_of_scope_cases() -> None:
    """多对象、ring 和 rounded_rect 不得计入本轮支持率分母。."""
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    assert baseline["unsupported_cases"] == [
        "rounded_rect_glow",
        "neon_ring",
        "dual_disks",
    ]
    assert not set(baseline["unsupported_cases"]) & set(baseline["supported_cases"])
