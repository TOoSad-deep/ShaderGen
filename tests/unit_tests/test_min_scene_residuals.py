"""scene_mvp 空间残差安全摘要测试。"""

import numpy as np
import pytest

from shaderforge.evaluation import (
    MIN_SCENE_METRIC_VERSION,
    dominant_metric_component,
    evaluate_min_scene,
    summarize_spatial_residual,
)


def test_spatial_residual_reports_top_two_tiles_with_signed_bias() -> None:
    reference = np.full((8, 8, 3), 0.5, dtype=np.float32)
    rendered = reference.copy()
    rendered[0:2, 0:2] += np.asarray((0.3, 0.2, 0.1), dtype=np.float32)
    rendered[2:4, 2:4] -= np.asarray((0.15, 0.1, 0.05), dtype=np.float32)

    summary = summarize_spatial_residual(reference, rendered)

    assert summary["tile_grid"] == 4
    assert summary["worst_tile_count"] == 2
    assert summary["bias_convention"] == "rendered_minus_reference"
    first, second = summary["worst_tiles"]
    assert (first["row"], first["column"]) == (0, 0)
    assert first["mae"] == pytest.approx(0.2)
    assert first["signed_luminance_bias"] > 0.0
    assert first["signed_rgb_bias"] == pytest.approx([0.3, 0.2, 0.1])
    assert (second["row"], second["column"]) == (1, 1)
    assert second["mae"] == pytest.approx(0.1)
    assert second["signed_luminance_bias"] < 0.0
    assert second["signed_rgb_bias"] == pytest.approx([-0.15, -0.1, -0.05])


def test_spatial_residual_ties_use_row_then_column_order() -> None:
    reference = np.zeros((8, 8, 3), dtype=np.float32)
    rendered = reference.copy()
    rendered[0:2, 2:4] = 0.25
    rendered[4:6, 6:8] = 0.25

    summary = summarize_spatial_residual(reference, rendered)

    assert [
        (item["row"], item["column"]) for item in summary["worst_tiles"]
    ] == [(0, 1), (2, 3)]


def test_spatial_residual_rejects_invalid_or_too_small_images() -> None:
    with pytest.raises(ValueError, match="相同尺寸"):
        summarize_spatial_residual(
            np.zeros((8, 8, 3), dtype=np.float32),
            np.zeros((7, 8, 3), dtype=np.float32),
        )
    with pytest.raises(ValueError, match="至少为 4"):
        summarize_spatial_residual(
            np.zeros((3, 4, 3), dtype=np.float32),
            np.zeros((3, 4, 3), dtype=np.float32),
        )


def test_dominant_component_uses_weighted_contribution_without_changing_metric() -> None:
    reference = np.zeros((16, 16, 3), dtype=np.float32)
    reference[3:13, 3:13] = 0.8
    rendered = reference.copy()
    rendered[3:8, 3:8] = 0.0

    metric = evaluate_min_scene(reference, rendered, (0.0, 0.0, 0.0))
    before = metric.to_dict()

    component = dominant_metric_component(metric)

    assert component in metric.effective_weights
    assert component == max(
        metric.effective_weights,
        key=lambda name: (
            before[name] * metric.effective_weights[name],
            -list(metric.effective_weights).index(name),
        ),
    )
    assert metric.metric_version == MIN_SCENE_METRIC_VERSION
    assert metric.to_dict() == before


def test_dominant_component_has_stable_tie_break_and_rejects_empty_metrics() -> None:
    metric = {
        "global_mae": 1.0,
        "foreground_mae": 1.0,
        "effective_weights": {"global_mae": 0.5, "foreground_mae": 0.5},
    }

    assert dominant_metric_component(metric) == "global_mae"
    with pytest.raises(ValueError, match="主导"):
        dominant_metric_component({})
