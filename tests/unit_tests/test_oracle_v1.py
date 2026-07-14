from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageEnhance

from shaderforge.analysis import measure_target
from shaderforge.evaluation import (
    ImageSizeMismatchError,
    evaluate_render,
    max_protected_regression,
)

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK = ROOT / "benchmarks/png_to_shader_v1/images"


def _png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_identical_images_have_zero_losses() -> None:
    image = (BENCHMARK / "pink_gel.png").read_bytes()
    score = evaluate_render(image, image)

    assert score.metric_version == "basic_oracle_v1"
    assert score.total_loss == pytest.approx(0.0, abs=1e-12)
    assert score.global_rmse == pytest.approx(0.0, abs=1e-12)
    assert score.edge_loss == pytest.approx(0.0, abs=1e-12)
    assert all(loss == pytest.approx(0.0) for _, loss in score.roi_losses)


def test_progressive_brightness_error_increases_global_loss() -> None:
    reference = Image.open(BENCHMARK / "solid_circle.png").convert("RGB")
    mild = _png_bytes(ImageEnhance.Brightness(reference).enhance(0.85))
    strong = _png_bytes(ImageEnhance.Brightness(reference).enhance(0.55))
    reference_bytes = _png_bytes(reference)

    mild_score = evaluate_render(reference_bytes, mild)
    strong_score = evaluate_render(reference_bytes, strong)

    assert 0.0 < mild_score.global_mae < strong_score.global_mae
    assert mild_score.global_rmse < strong_score.global_rmse
    assert mild_score.total_loss < strong_score.total_loss


def test_position_shift_increases_geometry_and_edge_loss() -> None:
    reference = Image.new("RGB", (128, 128), "white")
    ImageDraw.Draw(reference).ellipse((32, 32, 96, 96), fill=(240, 30, 80))
    shifted = Image.new("RGB", (128, 128), "white")
    ImageDraw.Draw(shifted).ellipse((44, 32, 108, 96), fill=(240, 30, 80))
    reference_bytes = _png_bytes(reference)

    score = evaluate_render(
        reference_bytes,
        _png_bytes(shifted),
        measurements=measure_target(reference_bytes),
    )

    assert score.geometry_loss is not None
    assert score.geometry_loss > 0.01
    assert score.edge_loss > 0.01


def test_roi_loss_localizes_upper_left_change() -> None:
    reference = Image.open(BENCHMARK / "pink_gel.png").convert("RGB")
    candidate = reference.copy()
    ImageDraw.Draw(candidate).rectangle((20, 10, 80, 70), fill=(0, 0, 0))
    reference_bytes = _png_bytes(reference)
    score = evaluate_render(reference_bytes, _png_bytes(candidate))

    assert score.roi_loss_map["upper_left"] > score.roi_loss_map["lower_right"]
    assert score.protected_region_loss_map["protected_center"] >= 0.0
    assert score.to_dict()["roi_losses"]["upper_left"] > 0.0


def test_protected_regression_uses_shared_regions() -> None:
    reference = Image.open(BENCHMARK / "solid_circle.png").convert("RGB")
    reference_bytes = _png_bytes(reference)
    previous = evaluate_render(reference_bytes, reference_bytes)
    changed = reference.copy()
    ImageDraw.Draw(changed).rectangle((54, 54, 74, 74), fill=(0, 0, 0))
    candidate = evaluate_render(reference_bytes, _png_bytes(changed))

    assert max_protected_regression(previous, candidate) > 0.0


def test_size_mismatch_is_not_silently_resized() -> None:
    reference = (BENCHMARK / "solid_circle.png").read_bytes()
    candidate = _png_bytes(Image.new("RGB", (64, 64), "white"))

    with pytest.raises(ImageSizeMismatchError, match="尺寸"):
        evaluate_render(reference, candidate)
