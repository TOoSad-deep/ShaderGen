"""ShaderForge 确定性领域核心的惰性公共入口."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shaderforge.analysis import (
        InvalidTargetImageError,
        RegionOfInterest,
        TargetMeasurements,
        measure_target,
        normalize_target_png,
    )
    from shaderforge.contracts import (
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
    from shaderforge.evaluation import (
        CandidateRecord,
        CurrentBestDecision,
        ImageSizeMismatchError,
        MetricWeights,
        ScoreBreakdownV1,
        evaluate_render,
        max_protected_regression,
        select_current_best,
    )
    from shaderforge.generation import (
        MEASUREMENT_AFFINE_SEED_VERSION,
        MeasurementAffineSeed,
        MeasurementSeedProvenance,
        build_measurement_affine_seed,
    )
    from shaderforge.rendering import (
        CompileResult,
        PlaywrightWebGL1Renderer,
        RendererMetadata,
        RendererUnavailableError,
        RenderResult,
        build_standalone_html,
    )
    from shaderforge.store import ArtifactRef, LocalArtifactStore, RunArtifactStore
    from shaderforge.validation import (
        ValidationResult,
        ValidationViolation,
        validate_shader,
    )

__all__ = [
    "DEFAULT_ACCEPTANCE_POLICY",
    "PROBLEM_DOMAINS",
    "QUALITY_PRESETS",
    "STOP_REASONS",
    "WEBGL1_STATIC_NO_TEXTURE_V1",
    "AcceptancePolicy",
    "ArtifactRef",
    "BudgetPolicy",
    "CandidateRecord",
    "CompileResult",
    "CurrentBestDecision",
    "ImageSizeMismatchError",
    "InvalidTargetImageError",
    "LocalArtifactStore",
    "MEASUREMENT_AFFINE_SEED_VERSION",
    "MetricWeights",
    "MeasurementAffineSeed",
    "MeasurementSeedProvenance",
    "PlaywrightWebGL1Renderer",
    "ProblemDomain",
    "QualityPreset",
    "RegionOfInterest",
    "RenderContract",
    "RenderResult",
    "RendererMetadata",
    "RendererUnavailableError",
    "RunArtifactStore",
    "ScoreBreakdownV1",
    "StopReason",
    "TargetMeasurements",
    "ValidationResult",
    "ValidationViolation",
    "budget_for_preset",
    "build_standalone_html",
    "build_measurement_affine_seed",
    "evaluate_render",
    "max_protected_regression",
    "measure_target",
    "normalize_target_png",
    "select_current_best",
    "validate_shader",
]

_EXPORT_MODULES = {
    "DEFAULT_ACCEPTANCE_POLICY": "shaderforge.contracts",
    "PROBLEM_DOMAINS": "shaderforge.contracts",
    "QUALITY_PRESETS": "shaderforge.contracts",
    "STOP_REASONS": "shaderforge.contracts",
    "WEBGL1_STATIC_NO_TEXTURE_V1": "shaderforge.contracts",
    "AcceptancePolicy": "shaderforge.contracts",
    "BudgetPolicy": "shaderforge.contracts",
    "ProblemDomain": "shaderforge.contracts",
    "QualityPreset": "shaderforge.contracts",
    "RenderContract": "shaderforge.contracts",
    "StopReason": "shaderforge.contracts",
    "budget_for_preset": "shaderforge.contracts",
    "InvalidTargetImageError": "shaderforge.analysis",
    "RegionOfInterest": "shaderforge.analysis",
    "TargetMeasurements": "shaderforge.analysis",
    "measure_target": "shaderforge.analysis",
    "normalize_target_png": "shaderforge.analysis",
    "CandidateRecord": "shaderforge.evaluation",
    "CurrentBestDecision": "shaderforge.evaluation",
    "ImageSizeMismatchError": "shaderforge.evaluation",
    "MetricWeights": "shaderforge.evaluation",
    "ScoreBreakdownV1": "shaderforge.evaluation",
    "evaluate_render": "shaderforge.evaluation",
    "max_protected_regression": "shaderforge.evaluation",
    "select_current_best": "shaderforge.evaluation",
    "MEASUREMENT_AFFINE_SEED_VERSION": "shaderforge.generation",
    "MeasurementAffineSeed": "shaderforge.generation",
    "MeasurementSeedProvenance": "shaderforge.generation",
    "build_measurement_affine_seed": "shaderforge.generation",
    "CompileResult": "shaderforge.rendering",
    "PlaywrightWebGL1Renderer": "shaderforge.rendering",
    "RendererMetadata": "shaderforge.rendering",
    "RendererUnavailableError": "shaderforge.rendering",
    "RenderResult": "shaderforge.rendering",
    "build_standalone_html": "shaderforge.rendering",
    "ArtifactRef": "shaderforge.store",
    "LocalArtifactStore": "shaderforge.store",
    "RunArtifactStore": "shaderforge.store",
    "ValidationResult": "shaderforge.validation",
    "ValidationViolation": "shaderforge.validation",
    "validate_shader": "shaderforge.validation",
}


def __getattr__(name: str) -> Any:
    """按所属 typed 子包惰性解析兼容导出."""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """返回包含惰性公共名的模块属性列表."""
    return sorted(set(globals()) | set(__all__))
