"""scene_mvp 小预算确定性参数优化公共入口。."""

from shaderforge.optimization.dsl_params import (
    DslParameterSpec,
    dsl_parameter_specs,
    replace_dsl_parameter,
)
from shaderforge.optimization.min_optimize import (
    MAX_CANDIDATES_PER_BATCH,
    MAX_PATCH_CANDIDATE_DRAWS,
    CandidateProposal,
    OptimizationStage,
    ScoredScene,
    TunableParameter,
    accept_strict_mae_improvement,
    accepts_strict_total_loss,
    propose_min_scene_candidates,
    rebase_candidate_proposal,
)

__all__ = [
    "MAX_CANDIDATES_PER_BATCH",
    "MAX_PATCH_CANDIDATE_DRAWS",
    "CandidateProposal",
    "DslParameterSpec",
    "OptimizationStage",
    "ScoredScene",
    "TunableParameter",
    "accept_strict_mae_improvement",
    "dsl_parameter_specs",
    "accepts_strict_total_loss",
    "propose_min_scene_candidates",
    "rebase_candidate_proposal",
    "replace_dsl_parameter",
]
