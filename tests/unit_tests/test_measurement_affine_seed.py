from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from inspect import signature
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from shaderforge.analysis import measure_target, normalize_target_png
from shaderforge.generation import (
    MEASUREMENT_AFFINE_SEED_VERSION,
    build_measurement_affine_seed,
)
from shaderforge.validation import validate_shader

ROOT = Path(__file__).resolve().parents[2]


def _png(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG", compress_level=9)
    return buffer.getvalue()


def _affine_ellipse_reference(size: int = 96) -> bytes:
    image = Image.new("RGB", (size, size), "white")
    pixels = image.load()
    assert pixels is not None
    for row in range(size):
        for column in range(size):
            uv_x = (column + 0.5) / size
            uv_y = 1.0 - (row + 0.5) / size
            local_x = (uv_x - 0.5) / 0.32
            local_y = (uv_y - 0.5) / 0.28
            if local_x * local_x + local_y * local_y <= 1.0:
                pixels[column, row] = (
                    round(205 + 22 * local_x - 8 * local_y),
                    round(75 + 12 * local_x + 25 * local_y),
                    round(125 - 10 * local_x + 18 * local_y),
                )
    return normalize_target_png(_png(image))


def test_affine_seed_is_deterministic_bounded_and_texture_free() -> None:
    reference = _affine_ellipse_reference()
    measurements = measure_target(reference)

    first = build_measurement_affine_seed(reference, measurements)
    second = build_measurement_affine_seed(reference, measurements)

    assert first == second
    assert first.provenance.generator_version == MEASUREMENT_AFFINE_SEED_VERSION
    assert first.provenance.strategy == "foreground_affine_plane"
    assert first.provenance.fallback_reason is None
    assert first.provenance.fit_pixel_count >= 64
    assert first.provenance.fit_rmse is not None
    assert first.provenance.glsl_sha256 == sha256(first.glsl.encode()).hexdigest()
    assert first.provenance.glsl_chars == len(first.glsl) < 2_000
    assert any(abs(value) > 0.01 for value in first.provenance.coefficients[1])
    assert validate_shader(first.glsl).valid
    for forbidden in ("texture2D", "textureCube", "texture(", "texelFetch"):
        assert forbidden not in first.glsl
    assert first.provenance.to_dict()["strategy"] == "foreground_affine_plane"


def test_low_confidence_foreground_falls_back_to_palette_solid_ellipse() -> None:
    reference = normalize_target_png(_png(Image.new("RGB", (64, 64), "white")))
    measurements = measure_target(reference)

    seed = build_measurement_affine_seed(reference, measurements)

    assert seed.provenance.strategy == "palette_solid_ellipse"
    assert seed.provenance.fallback_reason == "foreground_low_confidence"
    assert seed.provenance.fit_pixel_count == 0
    assert seed.provenance.fit_rmse is None
    assert seed.provenance.coefficients[1:] == (
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
    )
    assert validate_shader(seed.glsl).valid


def test_missing_component_uses_audited_fit_fallback() -> None:
    reference = normalize_target_png(_png(Image.new("RGB", (64, 64), "white")))
    measured = measure_target(reference)
    measurements = replace(
        measured,
        foreground_confidence=1.0,
        foreground_bbox_uv=(0.2, 0.2, 0.8, 0.8),
    )

    seed = build_measurement_affine_seed(reference, measurements)

    assert seed.provenance.strategy == "palette_solid_ellipse"
    assert seed.provenance.fallback_reason == "affine_fit_unavailable"
    assert seed.provenance.fit_pixel_count == 0


def test_seed_rejects_unbound_or_non_normalized_reference() -> None:
    reference = _affine_ellipse_reference()
    measurements = measure_target(reference)
    other = normalize_target_png(_png(Image.new("RGB", (96, 96), "black")))

    with pytest.raises(ValueError, match="hash 不一致"):
        build_measurement_affine_seed(other, measurements)

    rgba = _png(Image.new("RGBA", (64, 64), (255, 0, 0, 128)))
    with pytest.raises(ValueError, match="RGB PNG"):
        build_measurement_affine_seed(rgba, measure_target(rgba))


def test_generation_kernel_is_fixture_and_gate_unaware() -> None:
    parameters = tuple(signature(build_measurement_affine_seed).parameters)
    assert parameters == ("reference_image", "measurements")

    source = (ROOT / "src/shaderforge/generation/measurement_affine.py").read_text(
        encoding="utf-8"
    )
    for forbidden in ("case_id", "golden", "manifest", "benchmark.gate"):
        assert forbidden not in source
