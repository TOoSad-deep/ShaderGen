"""scene_mvp 通用区域复合指标单元测试。"""

import numpy as np
import pytest

from shaderforge.evaluation import evaluate_min_scene


def test_min_scene_metric_exposes_generic_region_losses() -> None:
    reference = np.zeros((12, 12, 3), dtype=np.float32)
    reference[2:10, 2:10] = 0.5
    rendered = reference.copy()
    rendered[2:5, 2:5] = 0.0

    metric = evaluate_min_scene(reference, rendered, (0.0, 0.0, 0.0))

    assert metric.metric_version == "min_scene_composite_v3"
    assert metric.foreground_mae > metric.global_mae
    assert metric.worst_tile_mae > metric.global_mae
    assert metric.background_mae == pytest.approx(0.0)
    assert metric.geometry_mask_loss > 0.0
    assert metric.total_loss > metric.global_mae
    assert sum(metric.effective_weights.values()) == pytest.approx(1.0)


def test_min_scene_metric_falls_back_safely_when_no_foreground_is_detected() -> None:
    reference = np.full((8, 8, 3), 0.5, dtype=np.float32)
    rendered = np.full((8, 8, 3), 0.4, dtype=np.float32)

    metric = evaluate_min_scene(reference, rendered, (0.5, 0.5, 0.5))

    assert metric.foreground_ratio == 0.0
    assert metric.total_loss == pytest.approx(metric.global_mae)
    assert metric.foreground_mae == pytest.approx(metric.global_mae)
    assert metric.effective_weights["background_mae"] == 0.0
    assert metric.effective_weights["geometry_mask_loss"] == 0.0


def test_worst_tile_penalizes_local_error_more_than_spread_error() -> None:
    reference = np.zeros((16, 16, 3), dtype=np.float32)
    reference[2:14, 2:14] = 0.5
    local = reference.copy()
    local[2:6, 2:6] = 0.0
    spread = reference.copy()
    spread[2:14:3, 2:14] = 0.35

    local_metric = evaluate_min_scene(reference, local, (0.0, 0.0, 0.0))
    spread_metric = evaluate_min_scene(reference, spread, (0.0, 0.0, 0.0))

    assert local_metric.worst_tile_mae > spread_metric.worst_tile_mae


def test_geometry_mask_loss_detects_shifted_subject() -> None:
    reference = np.zeros((16, 16, 3), dtype=np.float32)
    reference[3:11, 3:11] = 0.8
    shifted = np.zeros_like(reference)
    shifted[5:13, 5:13] = 0.8

    metric = evaluate_min_scene(reference, shifted, (0.0, 0.0, 0.0))

    assert metric.geometry_mask_loss > 0.4
    assert metric.edge_loss > 0.0
