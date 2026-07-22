"""scene_mvp 局部复合指标单元测试。"""

import numpy as np
import pytest

from shaderforge.evaluation import evaluate_min_scene


def test_min_scene_metric_exposes_foreground_highlight_and_shadow_losses() -> None:
    reference = np.zeros((10, 10, 3), dtype=np.float32)
    reference[2:8, 2:8] = 0.5
    reference[2:4, 2:8] = 1.0
    reference[6:8, 2:8] = 0.2
    rendered = reference.copy()
    rendered[2:4, 2:8] = 0.0

    metric = evaluate_min_scene(reference, rendered, (0.0, 0.0, 0.0))

    assert metric.metric_version == "min_scene_composite_v2"
    assert metric.highlight_mae > metric.global_mae
    assert metric.foreground_mae > metric.global_mae
    assert metric.total_loss > metric.global_mae
    assert metric.shadow_mae == pytest.approx(0.0)


def test_min_scene_metric_falls_back_safely_when_no_foreground_is_detected() -> None:
    reference = np.full((8, 8, 3), 0.5, dtype=np.float32)
    rendered = np.full((8, 8, 3), 0.4, dtype=np.float32)

    metric = evaluate_min_scene(reference, rendered, (0.5, 0.5, 0.5))

    assert metric.foreground_ratio == 0.0
    assert metric.total_loss == pytest.approx(metric.global_mae)
    assert metric.foreground_mae == pytest.approx(metric.global_mae)


def test_local_object_quality_can_outweigh_a_slightly_better_global_mae() -> None:
    reference = np.zeros((10, 10, 3), dtype=np.float32)
    reference[2:8, 2:8] = 0.4
    reference[2:4, 2:8] = 1.0
    lost_highlight = reference.copy()
    lost_highlight[2:4, 2:8] = 0.0
    imperfect_background = reference.copy()
    background = np.ones((10, 10), dtype=bool)
    background[2:8, 2:8] = False
    imperfect_background[background] = 0.2

    highlight_metric = evaluate_min_scene(
        reference, lost_highlight, (0.0, 0.0, 0.0)
    )
    background_metric = evaluate_min_scene(
        reference, imperfect_background, (0.0, 0.0, 0.0)
    )

    assert highlight_metric.global_mae < background_metric.global_mae
    assert highlight_metric.total_loss > background_metric.total_loss
