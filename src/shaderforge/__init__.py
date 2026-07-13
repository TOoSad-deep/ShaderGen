"""ShaderForge 确定性领域核心."""

from shaderforge.public import (
    DEFAULT_ACCEPTANCE_POLICY,
    PROBLEM_DOMAINS,
    QUALITY_PRESETS,
    STOP_REASONS,
    WEBGL1_STATIC_NO_TEXTURE_V1,
    AcceptancePolicy,
    BudgetPolicy,
    ProblemDomain,
    QualityPreset,
    RenderContract,
    StopReason,
    budget_for_preset,
)

__all__ = [
    "DEFAULT_ACCEPTANCE_POLICY",
    "PROBLEM_DOMAINS",
    "QUALITY_PRESETS",
    "STOP_REASONS",
    "WEBGL1_STATIC_NO_TEXTURE_V1",
    "AcceptancePolicy",
    "BudgetPolicy",
    "ProblemDomain",
    "QualityPreset",
    "RenderContract",
    "StopReason",
    "budget_for_preset",
]
