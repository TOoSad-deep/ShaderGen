from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from shaderforge.analysis import measure_target, normalize_target_png
from shaderforge.analysis.measurements import InvalidTargetImageError

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK = ROOT / "benchmarks/png_to_shader_v1/images"


def test_measure_solid_circle_recovers_background_and_bbox() -> None:
    measurements = measure_target((BENCHMARK / "solid_circle.png").read_bytes())

    assert measurements.schema_version == 1
    assert (measurements.image_width, measurements.image_height) == (192, 192)
    assert measurements.border_color_rgb == pytest.approx((255, 255, 255), abs=2)
    assert measurements.border_uniformity > 0.95
    assert measurements.foreground_confidence > 0.85
    assert measurements.foreground_bbox_uv == pytest.approx(
        (0.18, 0.18, 0.82, 0.82), abs=0.02
    )
    assert 0.28 < measurements.foreground_fraction < 0.36
    assert len(measurements.palette) >= 2
    assert len(measurements.representative_pixels) == 10
    assert {roi.region_id for roi in measurements.roi_candidates} >= {
        "subject",
        "background_border",
        "protected_center",
    }


def test_measure_pink_gel_exposes_shader_uv_and_edge_summary() -> None:
    measurements = measure_target((BENCHMARK / "pink_gel.png").read_bytes())

    assert measurements.foreground_bbox_uv == pytest.approx(
        (0.10, 0.10, 0.90, 0.90), abs=0.03
    )
    assert (
        measurements.edge_summary.p90_strength
        >= measurements.edge_summary.mean_strength
    )
    assert all(0.0 <= value <= 1.0 for value in measurements.edge_summary.strongest_uv)
    assert measurements.to_dict()["image_sha256"] == measurements.image_sha256


def test_measure_downsamples_analysis_but_preserves_original_size() -> None:
    image = Image.new("RGB", (1200, 600), "white")
    ImageDraw.Draw(image).ellipse((300, 100, 900, 500), fill=(240, 20, 80))
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    measurements = measure_target(buffer.getvalue(), max_long_side=300)

    assert (measurements.image_width, measurements.image_height) == (1200, 600)
    assert (measurements.analysis_width, measurements.analysis_height) == (300, 150)
    assert measurements.foreground_bbox_uv is not None


def test_normalize_target_png_matches_renderer_dimensions_and_white_alpha() -> None:
    image = Image.new("RGBA", (1200, 600), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((300, 100, 900, 500), fill=(240, 20, 80, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    normalized = normalize_target_png(buffer.getvalue(), max_long_side=300)
    measurements = measure_target(normalized)

    with Image.open(BytesIO(normalized)) as rendered_reference:
        assert rendered_reference.size == (300, 150)
        assert rendered_reference.mode == "RGB"
        assert rendered_reference.getpixel((0, 0)) == (255, 255, 255)
    assert (measurements.analysis_width, measurements.analysis_height) == (300, 150)


def test_measure_composites_transparent_pixels_over_white() -> None:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((16, 16, 48, 48), fill=(255, 0, 64, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    measurements = measure_target(buffer.getvalue())

    assert measurements.border_color_rgb == (255, 255, 255)
    assert measurements.foreground_bbox_uv == pytest.approx(
        (0.25, 0.234375, 0.765625, 0.75), abs=0.02
    )


def test_measure_rejects_empty_or_invalid_images() -> None:
    with pytest.raises(InvalidTargetImageError, match="不能为空"):
        measure_target(b"")
    with pytest.raises(InvalidTargetImageError, match="无法解码"):
        measure_target(b"not-an-image")
