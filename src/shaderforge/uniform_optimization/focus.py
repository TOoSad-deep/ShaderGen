"""Strict, advisory scope contracts for local uniform optimization.

Focus is deliberately separate from patches and provenance: resolving a focus
only selects trusted manifest coordinates and never mutates either Spec.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from shaderforge.layered_spec import LayeredShaderSpecV1
from shaderforge.program_spec import ShaderProgramSpecV1
from shaderforge.program_spec.models import UNIFORM_NAME_PATTERN
from shaderforge.uniform_optimization.flattening import flatten_tunable_components
from shaderforge.uniform_optimization.models import (
    FlatTunableComponent,
    UniformOptimizationError,
)

FocusObjective = Literal["geometry", "color", "edge", "effect"]
FocusRegionPolicy = Literal[
    "layer_region", "worst_residual_intersection", "full_canvas"
]

_OBJECTIVES = frozenset({"geometry", "color", "edge", "effect"})
_REGION_POLICIES = frozenset(
    {"layer_region", "worst_residual_intersection", "full_canvas"}
)
_SCHEMA_VERSION = "uniform_optimization_focus_v1"


@dataclass(frozen=True, slots=True)
class UniformOptimizationFocusComponentV1:
    """A whitelist entry for scalar components of one uniform path."""

    path: str
    component_indices: tuple[int, ...]

    def __post_init__(self) -> None:
        """Validate and canonicalize one component whitelist entry."""
        if not isinstance(self.path, str) or not UNIFORM_NAME_PATTERN.fullmatch(
            self.path
        ):
            raise UniformOptimizationError("invalid_focus", "focus path is invalid")
        if not isinstance(self.component_indices, tuple) or not self.component_indices:
            raise UniformOptimizationError(
                "invalid_focus", "focus component_indices must be a non-empty tuple"
            )
        if any(
            isinstance(index, bool) or not isinstance(index, int) or index < 0
            for index in self.component_indices
        ):
            raise UniformOptimizationError(
                "invalid_focus", "focus component indices must be non-negative integers"
            )
        if len(set(self.component_indices)) != len(self.component_indices):
            raise UniformOptimizationError(
                "invalid_focus", "focus component indices must be unique"
            )
        object.__setattr__(
            self, "component_indices", tuple(sorted(self.component_indices))
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-safe whitelist entry."""
        return {"path": self.path, "component_indices": list(self.component_indices)}


@dataclass(frozen=True, slots=True)
class UniformOptimizationFocusV1:
    """Serializable advisory scope for one layer-local tuning pass.

    An empty ``active_components`` is explicit: it selects no coordinates and
    therefore lets a caller safely skip uniform optimization for this focus.
    """

    target_layer_id: str
    objective: FocusObjective
    active_components: tuple[UniformOptimizationFocusComponentV1, ...]
    region_policy: FocusRegionPolicy
    schema_version: str = _SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Validate fixed focus vocabulary and canonicalize path ordering."""
        if self.schema_version != _SCHEMA_VERSION:
            raise UniformOptimizationError("invalid_focus", "unsupported focus schema")
        if not isinstance(self.target_layer_id, str) or not self.target_layer_id:
            raise UniformOptimizationError(
                "invalid_focus", "focus target_layer_id is required"
            )
        if self.objective not in _OBJECTIVES:
            raise UniformOptimizationError(
                "invalid_focus", "focus objective is invalid"
            )
        if self.region_policy not in _REGION_POLICIES:
            raise UniformOptimizationError(
                "invalid_focus", "focus region_policy is invalid"
            )
        if not isinstance(self.active_components, tuple):
            raise UniformOptimizationError(
                "invalid_focus", "focus active_components must be a tuple"
            )
        if any(
            not isinstance(component, UniformOptimizationFocusComponentV1)
            for component in self.active_components
        ):
            raise UniformOptimizationError(
                "invalid_focus", "focus component is invalid"
            )
        paths = [component.path for component in self.active_components]
        if len(set(paths)) != len(paths):
            raise UniformOptimizationError(
                "invalid_focus", "focus paths must be unique"
            )
        object.__setattr__(
            self,
            "active_components",
            tuple(sorted(self.active_components, key=lambda component: component.path)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-safe focus payload."""
        return {
            "schema_version": self.schema_version,
            "target_layer_id": self.target_layer_id,
            "objective": self.objective,
            "active_components": [item.to_dict() for item in self.active_components],
            "region_policy": self.region_policy,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> UniformOptimizationFocusV1:
        """Parse one exact JSON payload without accepting extension fields."""
        return parse_uniform_optimization_focus_v1(value)


@dataclass(frozen=True, slots=True)
class UniformOptimizationFocusValidation:
    """Typed, non-mutating focus-resolution outcome for safe caller fallback."""

    focus: UniformOptimizationFocusV1
    components: tuple[FlatTunableComponent, ...]
    error_code: str | None = None

    def __post_init__(self) -> None:
        """Keep invalid results empty so they are safe to consume."""
        if self.error_code is None:
            return
        if not isinstance(self.error_code, str) or not self.error_code:
            raise ValueError("focus validation error_code must be non-empty")
        if self.components:
            raise ValueError("invalid focus validation cannot expose components")

    @property
    def is_valid(self) -> bool:
        """Whether the focus selected trusted components successfully."""
        return self.error_code is None


def parse_uniform_optimization_focus_v1(
    value: Mapping[str, Any],
) -> UniformOptimizationFocusV1:
    """Strictly parse a JSON-compatible focus payload."""
    if not isinstance(value, Mapping):
        raise UniformOptimizationError("invalid_focus", "focus must be an object")
    expected = {
        "schema_version",
        "target_layer_id",
        "objective",
        "active_components",
        "region_policy",
    }
    if set(value) != expected:
        raise UniformOptimizationError("invalid_focus", "focus fields are invalid")
    entries = value["active_components"]
    if not isinstance(entries, list):
        raise UniformOptimizationError(
            "invalid_focus", "active_components must be an array"
        )
    parsed: list[UniformOptimizationFocusComponentV1] = []
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != {
            "path",
            "component_indices",
        }:
            raise UniformOptimizationError(
                "invalid_focus", "focus component fields are invalid"
            )
        indices = entry["component_indices"]
        if not isinstance(indices, list):
            raise UniformOptimizationError(
                "invalid_focus", "focus component_indices must be an array"
            )
        parsed.append(
            UniformOptimizationFocusComponentV1(
                path=entry["path"], component_indices=tuple(indices)
            )
        )
    return UniformOptimizationFocusV1(
        schema_version=value["schema_version"],
        target_layer_id=value["target_layer_id"],
        objective=value["objective"],
        active_components=tuple(parsed),
        region_policy=value["region_policy"],
    )


def validate_uniform_optimization_focus(
    focus: UniformOptimizationFocusV1,
    layered: LayeredShaderSpecV1,
    program: ShaderProgramSpecV1,
) -> UniformOptimizationFocusValidation:
    """Resolve a focus against one verified Spec pair without changing either.

    Components are always returned in ``flatten_tunable_components`` canonical
    order, never in model-provided whitelist order.  Pair corruption and all
    invalid targets are represented as a typed invalid result for safe fallback.
    """
    if not isinstance(focus, UniformOptimizationFocusV1):
        raise TypeError("focus must be UniformOptimizationFocusV1")
    try:
        layer = next(
            (item for item in layered.layers if item.layer_id == focus.target_layer_id),
            None,
        )
        if layer is None:
            raise UniformOptimizationError(
                "unknown_focus_layer", "focus layer is unknown"
            )
        whitelist = {
            (entry.path, component_index)
            for entry in focus.active_components
            for component_index in entry.component_indices
        }
        manifest_paths = {item.path for item in layer.tunable_manifest}
        for entry in focus.active_components:
            if entry.path not in manifest_paths:
                raise UniformOptimizationError(
                    "non_manifest_focus", "focus path is not tunable in target layer"
                )
            declaration = next(
                item for item in layer.uniform_schema if item.name == entry.path
            )
            if any(
                index >= declaration.component_count
                for index in entry.component_indices
            ):
                raise UniformOptimizationError(
                    "focus_component_out_of_range",
                    "focus component index is out of range",
                )
        components = tuple(
            component
            for component in flatten_tunable_components(layered, program)
            if component.layer_id == focus.target_layer_id
            and (component.path, component.component_index) in whitelist
        )
        # A manifest coordinate can be filtered out only when its lattice has no
        # WebGL-observable move.  That is a valid empty result, as is an explicit
        # empty whitelist.
        return UniformOptimizationFocusValidation(focus=focus, components=components)
    except UniformOptimizationError as exc:
        return UniformOptimizationFocusValidation(
            focus=focus, components=(), error_code=exc.code
        )


def resolve_uniform_optimization_focus(
    focus: UniformOptimizationFocusV1,
    layered: LayeredShaderSpecV1,
    program: ShaderProgramSpecV1,
) -> tuple[FlatTunableComponent, ...]:
    """Resolve a focus or raise a stable domain error for fail-closed callers."""
    result = validate_uniform_optimization_focus(focus, layered, program)
    if not result.is_valid:
        raise UniformOptimizationError(
            result.error_code or "invalid_focus", "invalid focus"
        )
    return result.components
