"""Shader 渲染结果的确定性基础评分."""

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
    "ImageSizeMismatchError",
    "CandidateRecord",
    "CurrentBestDecision",
    "MetricWeights",
    "ScoreBreakdownV1",
    "evaluate_render",
    "max_protected_regression",
    "select_current_best",
]
