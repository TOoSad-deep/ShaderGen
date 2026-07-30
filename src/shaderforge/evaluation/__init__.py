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
from shaderforge.evaluation.region import (
    FOCUSED_REGION_METRICS_VERSION,
    FocusedRegionMetricsV1,
    NormalizedUvBBox,
    evaluate_focused_region,
    normalize_uv_bbox,
)
from shaderforge.evaluation.role_alpha import (
    ROLE_ALPHA_MASK_PASSES,
    ROLE_ALPHA_MASK_VERSION,
    RoleAlphaMaskV1,
    decode_role_alpha_masks,
    encode_grayscale_png,
)

__all__ = [
    "MIN_SCENE_METRIC_VERSION",
    "MIN_SCENE_METRIC_WEIGHTS",
    "FOCUSED_REGION_METRICS_VERSION",
    "ROLE_ALPHA_MASK_PASSES",
    "ROLE_ALPHA_MASK_VERSION",
    "MinSceneMetricBreakdown",
    "FocusedRegionMetricsV1",
    "NormalizedUvBBox",
    "RoleAlphaMaskV1",
    "decode_rgb",
    "dominant_metric_component",
    "evaluate_min_scene",
    "decode_role_alpha_masks",
    "encode_grayscale_png",
    "evaluate_focused_region",
    "normalize_uv_bbox",
    "rgb_mae",
    "summarize_spatial_residual",
]
