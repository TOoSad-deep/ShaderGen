"""scene_mvp 小预算确定性参数优化公共入口。."""

from shaderforge.optimization.min_optimize import (
    MAX_CANDIDATES_PER_BATCH,
    CandidateProposal,
    OptimizationStage,
    ScoredScene,
    TunableParameter,
    accept_strict_mae_improvement,
    propose_min_scene_candidates,
)

__all__ = [
    "MAX_CANDIDATES_PER_BATCH",
    "CandidateProposal",
    "OptimizationStage",
    "ScoredScene",
    "TunableParameter",
    "accept_strict_mae_improvement",
    "propose_min_scene_candidates",
]
