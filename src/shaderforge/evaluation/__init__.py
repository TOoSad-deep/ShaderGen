"""scene_mvp 渲染结果的确定性评分."""

from shaderforge.evaluation.mae import (
    MIN_SCENE_METRIC_VERSION,
    MIN_SCENE_METRIC_WEIGHTS,
    MinSceneMetricBreakdown,
    decode_rgb,
    dominant_metric_component,
    evaluate_min_scene,
    rgb_mae,
    summarize_spatial_residual,
)

__all__ = [
    "MIN_SCENE_METRIC_VERSION",
    "MIN_SCENE_METRIC_WEIGHTS",
    "MinSceneMetricBreakdown",
    "decode_rgb",
    "dominant_metric_component",
    "evaluate_min_scene",
    "rgb_mae",
    "summarize_spatial_residual",
]
