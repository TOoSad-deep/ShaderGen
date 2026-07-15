from __future__ import annotations

from pathlib import Path

import pytest

from shaderforge.analysis import RegionOfInterest, measure_target, normalize_target_png
from shaderforge.evaluation import evaluate_render
from shaderforge.generation import build_measurement_affine_seed
from shaderforge.rendering import PlaywrightWebGL1Renderer
from shaderforge.validation import validate_shader

ROOT = Path(__file__).resolve().parents[2]
IMAGE_ROOT = ROOT / "benchmarks/png_to_shader_v1/images"

# 固定图片清单直接写在测试内；本测试不读取 benchmark manifest 或 gate。
REFERENCE_IMAGES = (
    "solid_circle.png",
    "ellipse_gradient.png",
    "shadow_disk.png",
    "rimmed_disk.png",
    "arc_highlight_orb.png",
    "color_lobes.png",
    "rounded_rect_glow.png",
    "neon_ring.png",
    "dual_disks.png",
    "pink_gel.png",
)

# pink_gel 的独立回归契约显式冻结在测试内，避免运行时读取 gate/manifest。
PINK_EXPECTED_BBOX_UV = (0.10, 0.10, 0.90, 0.90)
PINK_MAX_BBOX_ERROR_UV = 0.065
PINK_MAX_GLOBAL_RMSE = 0.16
PINK_ROIS = (
    RegionOfInterest(
        "highlight_upper_left", (0.16, 0.66, 0.50, 0.91), "highlight", 1.0
    ),
    RegionOfInterest(
        "highlight_lower_right", (0.54, 0.10, 0.88, 0.36), "highlight", 1.0
    ),
    RegionOfInterest("center_haze", (0.35, 0.34, 0.68, 0.64), "color", 1.0),
    RegionOfInterest("shadow", (0.18, 0.02, 0.86, 0.24), "shadow", 1.0),
)
PINK_MAX_ROI_LOSSES = {
    "highlight_upper_left": 0.275,
    "highlight_lower_right": 0.11,
    "center_haze": 0.04,
    "shadow": 0.11,
}


@pytest.mark.anyio
async def test_fixed_ten_measurement_seeds_cross_real_chromium_and_oracle() -> None:
    pink_reference: bytes | None = None
    pink_render: bytes | None = None
    pink_measurements = None

    async with PlaywrightWebGL1Renderer() as renderer:
        for filename in REFERENCE_IMAGES:
            reference = normalize_target_png((IMAGE_ROOT / filename).read_bytes())
            measurements = measure_target(reference)
            seed = build_measurement_affine_seed(reference, measurements)

            assert validate_shader(seed.glsl).valid, filename
            rendered = await renderer.render(
                seed.glsl,
                measurements.analysis_width,
                measurements.analysis_height,
            )
            assert rendered.success, filename
            assert rendered.compile.success, filename
            assert rendered.image_bytes is not None, filename
            score = evaluate_render(
                reference,
                rendered.image_bytes,
                measurements=measurements,
            )
            assert 0.0 <= score.total_loss < 1.0, filename

            if filename == "pink_gel.png":
                pink_reference = reference
                pink_render = rendered.image_bytes
                pink_measurements = measurements

    assert pink_reference is not None
    assert pink_render is not None
    assert pink_measurements is not None
    pink_score = evaluate_render(
        pink_reference,
        pink_render,
        measurements=pink_measurements,
        regions=PINK_ROIS,
    )
    candidate_bbox = measure_target(pink_render).foreground_bbox_uv
    assert candidate_bbox is not None
    bbox_error = max(
        abs(actual - expected)
        for actual, expected in zip(
            candidate_bbox,
            PINK_EXPECTED_BBOX_UV,
            strict=True,
        )
    )
    assert bbox_error <= PINK_MAX_BBOX_ERROR_UV
    assert pink_score.global_rmse <= PINK_MAX_GLOBAL_RMSE
    for region_id, maximum in PINK_MAX_ROI_LOSSES.items():
        assert pink_score.roi_loss_map[region_id] <= maximum
