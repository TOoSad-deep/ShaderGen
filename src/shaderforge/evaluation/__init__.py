"""Shader 渲染结果的确定性基础评分."""

from shaderforge.evaluation.mae import (
    MIN_SCENE_METRIC_VERSION,
    MIN_SCENE_METRIC_WEIGHTS,
    MinSceneMetricBreakdown,
    decode_rgb,
    evaluate_min_scene,
    rgb_mae,
)
from shaderforge.evaluation.models import MetricWeights, ScoreBreakdownV1
from shaderforge.evaluation.oracle import (
    ImageSizeMismatchError,
    evaluate_render,
    max_protected_regression,
)
from shaderforge.evaluation.selection import (
    CandidateRecord,
    CurrentBestDecision,
    select_current_best,
)

__all__ = [
    "CandidateRecord",
    "CurrentBestDecision",
    "ImageSizeMismatchError",
    "MetricWeights",
    "MIN_SCENE_METRIC_VERSION",
    "MIN_SCENE_METRIC_WEIGHTS",
    "MinSceneMetricBreakdown",
    "ScoreBreakdownV1",
    "decode_rgb",
    "evaluate_render",
    "evaluate_min_scene",
    "max_protected_regression",
    "rgb_mae",
    "select_current_best",
]
