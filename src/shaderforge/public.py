"""ShaderForge 对其他应用层开放的稳定入口."""

from shaderforge.contracts.png_to_shader_v1 import (
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
