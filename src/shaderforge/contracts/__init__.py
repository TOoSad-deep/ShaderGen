"""ShaderForge 稳定领域契约."""

from shaderforge.contracts.base import (
    FiniteFloat,
    FrozenModel,
    JsonValue,
    NonEmptyString,
    Sha256Hex,
)
from shaderforge.contracts.canonical import (
    CANONICAL_JSON_VERSION,
    canonical_json_bytes,
    canonical_sha256,
    canonicalize,
)
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
from shaderforge.contracts.taxonomy import (
    REQUIRED_LAYER_ORDER,
    REQUIRED_LAYER_TAXONOMY_VERSION,
    RequiredLayerTaxon,
)

__all__ = [
    "CANONICAL_JSON_VERSION",
    "DEFAULT_ACCEPTANCE_POLICY",
    "PROBLEM_DOMAINS",
    "QUALITY_PRESETS",
    "REQUIRED_LAYER_ORDER",
    "REQUIRED_LAYER_TAXONOMY_VERSION",
    "STOP_REASONS",
    "WEBGL1_STATIC_NO_TEXTURE_V1",
    "AcceptancePolicy",
    "BudgetPolicy",
    "FiniteFloat",
    "FrozenModel",
    "JsonValue",
    "NonEmptyString",
    "ProblemDomain",
    "QualityPreset",
    "RenderContract",
    "RequiredLayerTaxon",
    "Sha256Hex",
    "StopReason",
    "budget_for_preset",
    "canonical_json_bytes",
    "canonical_sha256",
    "canonicalize",
]
