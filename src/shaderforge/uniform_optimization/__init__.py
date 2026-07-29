"""Deterministic, bounded, manifest-only uniform optimization primitives."""

from shaderforge.uniform_optimization.flattening import (
    decimal_from_number,
    flatten_tunable_components,
    lattice_value,
    webgl_float32,
)
from shaderforge.uniform_optimization.hashing import (
    active_components_sha256,
    component_identity_sha256,
)
from shaderforge.uniform_optimization.models import (
    FlatTunableComponent,
    UniformOptimizationConfig,
    UniformOptimizationError,
    UniformOptimizationProvenanceV1,
    UniformOptimizationSummaryV2,
    UniformPatchV1,
    decimal_text,
)
from shaderforge.uniform_optimization.patching import (
    AppliedUniformPatch,
    UniformPatchProjection,
    apply_uniform_patch,
    apply_uniform_patch_values,
)
from shaderforge.uniform_optimization.search import (
    CoordinateMove,
    CoordinatePatternSession,
    next_coordinate_move,
    record_coordinate_failure,
    record_coordinate_outcome,
    start_coordinate_pattern_session,
)

__all__ = [
    "AppliedUniformPatch",
    "CoordinateMove",
    "CoordinatePatternSession",
    "FlatTunableComponent",
    "UniformOptimizationConfig",
    "UniformOptimizationError",
    "UniformOptimizationProvenanceV1",
    "UniformOptimizationSummaryV2",
    "UniformPatchProjection",
    "UniformPatchV1",
    "active_components_sha256",
    "apply_uniform_patch",
    "apply_uniform_patch_values",
    "component_identity_sha256",
    "decimal_from_number",
    "decimal_text",
    "flatten_tunable_components",
    "lattice_value",
    "next_coordinate_move",
    "record_coordinate_failure",
    "record_coordinate_outcome",
    "start_coordinate_pattern_session",
    "webgl_float32",
]
