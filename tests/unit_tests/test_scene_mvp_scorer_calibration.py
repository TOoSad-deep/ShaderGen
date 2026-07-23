"""固定 7 例 scorer 校准脚本的纯函数测试。"""

from __future__ import annotations

from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from scripts.run_scene_mvp_scorer_calibration import (
    classify_direction_conflict,
    primitive_normalized_distance,
    semantic_region_breakdown,
)
from shaderforge.perception import MinPerception, perceive_min_target


def _perception_fixture() -> tuple[np.ndarray, MinPerception]:
    image = Image.new("RGB", (32, 32), "white")
    pixels = image.load()
    assert pixels is not None
    for y in range(6, 26):
        for x in range(6, 26):
            if (x - 15.5) ** 2 + (y - 15.5) ** 2 <= 9.5**2:
                pixels[x, y] = (230, 80, 130)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    perception = perceive_min_target(buffer.getvalue())
    return perception.target_rgb, perception


def test_semantic_regions_are_zero_for_identical_render() -> None:
    reference, perception_value = _perception_fixture()
    perception = perception_value

    distance = primitive_normalized_distance(perception.fallback_scene)
    breakdown = semantic_region_breakdown(
        reference,
        reference.copy(),
        perception.fallback_scene,
        perception.fallback_scene.canvas.background,
    )

    assert distance.shape == reference.shape[:2]
    assert float(distance.min()) < 0.1
    assert breakdown["subject_interior_mae"] == pytest.approx(0.0)
    assert breakdown["subject_edge_band_mae"] == pytest.approx(0.0)
    assert breakdown["protected_background_mae"] == pytest.approx(0.0)
    assert breakdown["subject_foreground_iou_loss"] == pytest.approx(0.0)
    assert breakdown["exterior_effect_iou_loss"] == pytest.approx(0.0)


def test_semantic_regions_detect_exterior_false_positive() -> None:
    reference, perception_value = _perception_fixture()
    perception = perception_value
    rendered = reference.copy()
    rendered[:3, :3] = (0.0, 0.0, 0.0)

    breakdown = semantic_region_breakdown(
        reference,
        rendered,
        perception.fallback_scene,
        perception.fallback_scene.canvas.background,
    )

    assert breakdown["protected_background_mae"] is not None
    assert float(breakdown["protected_background_mae"]) > 0.0
    assert breakdown["exterior_effect_iou_loss"] is not None
    assert float(breakdown["exterior_effect_iou_loss"]) > 0.0
    assert (
        float(breakdown["candidate_exterior_effect_ratio"])
        > float(breakdown["reference_exterior_effect_ratio"])
    )


def test_direction_conflict_requires_composite_improvement_and_proxy_regression() -> None:
    baseline_metrics = {
        "total_loss": 0.5,
        "global_mae": 0.1,
        "foreground_mae": 0.1,
        "background_mae": 0.1,
        "geometry_mask_loss": 0.4,
        "edge_loss": 0.1,
        "worst_tile_mae": 0.1,
    }
    optimized_metrics = {
        **baseline_metrics,
        "total_loss": 0.4,
        "global_mae": 0.12,
        "geometry_mask_loss": 0.2,
    }
    semantic = {
        "subject_interior_mae": 0.1,
        "subject_edge_band_mae": 0.1,
        "reference_exterior_effect_mae": 0.1,
        "protected_background_mae": 0.1,
        "subject_foreground_iou_loss": 0.1,
        "exterior_effect_iou_loss": 0.1,
    }

    result = classify_direction_conflict(
        baseline_metrics,
        optimized_metrics,
        semantic,
        semantic,
        {"highlight": 0.01},
    )

    assert result["composite_improved"] is True
    assert result["geometry_improved"] is True
    assert result["objective_direction_conflict"] is True
    assert result["material_objective_direction_conflict"] is True
    assert result["worsened_visual_proxies"] == [
        "global_mae",
        "roi:highlight",
    ]
    assert result["materially_worsened_visual_proxies"] == ["global_mae"]
