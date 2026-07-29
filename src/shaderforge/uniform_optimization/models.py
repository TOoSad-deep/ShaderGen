"""Trusted value objects for bounded uniform-only optimization."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from shaderforge.program_spec import canonical_json, sha256_hex_text

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,127}$")


class UniformOptimizationError(ValueError):
    """Report one stable failure in the trusted optimizer domain."""

    def __init__(self, code: str, message: str) -> None:
        """Retain a stable code while avoiding unsafe domain-error messages."""
        if not _SAFE_IDENTIFIER.fullmatch(code):
            raise ValueError("uniform optimizer error code must be safe")
        self.code = code
        super().__init__(message)


def _sha256(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise UniformOptimizationError("invalid_hash", f"{name} must be a SHA-256.")
    return value


def _decimal(value: Decimal, *, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise UniformOptimizationError("invalid_decimal", f"{name} must be finite.")
    return value


def decimal_text(value: Decimal) -> str:
    """Return a canonical non-exponent decimal string."""
    value = _decimal(value, name="decimal")
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


@dataclass(frozen=True, slots=True)
class FlatTunableComponent:
    """One scalar coordinate derived from one manifest-addressable uniform."""

    layer_id: str
    path: str
    component_index: int
    minimum: Decimal
    maximum: Decimal
    step: Decimal
    base_value: Decimal

    def __post_init__(self) -> None:
        """Reject malformed scalar domains before they reach a search session."""
        if not self.layer_id or not self.path or self.component_index < 0:
            raise UniformOptimizationError(
                "invalid_component", "invalid component identity"
            )
        minimum = _decimal(self.minimum, name="minimum")
        maximum = _decimal(self.maximum, name="maximum")
        step = _decimal(self.step, name="step")
        base = _decimal(self.base_value, name="base_value")
        if minimum > maximum or step <= 0 or not minimum <= base <= maximum:
            raise UniformOptimizationError(
                "invalid_component", "invalid component domain"
            )

    @property
    def canonical_path(self) -> str:
        """Return the unambiguous scalar address used by provenance and traces."""
        return f"{self.layer_id}:{self.path}[{self.component_index}]"

    def to_dict(self) -> dict[str, str | int]:
        """Return canonical JSON-safe component metadata without a private value trace."""
        return {
            "layer_id": self.layer_id,
            "path": self.path,
            "component_index": self.component_index,
            "minimum": decimal_text(self.minimum),
            "maximum": decimal_text(self.maximum),
            "step": decimal_text(self.step),
            "base_value": decimal_text(self.base_value),
        }


@dataclass(frozen=True, slots=True)
class UniformOptimizationConfig:
    """Bounded deterministic-search configuration for one attempt-local session."""

    draw_budget: int = 4
    active_component_cap: int = 8
    max_passes: int = 1
    algorithm_id: str = "bounded_coordinate_pattern_search"
    algorithm_version: str = "uniform_coordinate_v2"

    def __post_init__(self) -> None:
        """Reject unbounded or anonymous optimizer configurations."""
        for name in ("draw_budget", "active_component_cap", "max_passes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise UniformOptimizationError(
                    "invalid_config", f"{name} must be non-negative"
                )
        if not self.algorithm_id or not self.algorithm_version:
            raise UniformOptimizationError(
                "invalid_config", "optimizer identity is required"
            )
        if self.max_passes > 1:
            raise UniformOptimizationError(
                "invalid_config",
                "the bounded MVP supports at most one coordinate pass",
            )

    def to_dict(self) -> dict[str, str | int]:
        """Return the complete configuration that participates in provenance."""
        return {
            "draw_budget": self.draw_budget,
            "active_component_cap": self.active_component_cap,
            "max_passes": self.max_passes,
            "algorithm_id": self.algorithm_id,
            "algorithm_version": self.algorithm_version,
        }

    def fingerprint(self) -> str:
        """Return a stable fingerprint for optimizer provenance and summaries."""
        return sha256_hex_text(canonical_json(self.to_dict()))


@dataclass(frozen=True, slots=True)
class UniformOptimizationProvenanceV1:
    """Trusted derivation metadata for one uniform-only candidate.

    The trusted Layered/Program derivation path binds this value into both
    canonical Spec hashes, preserving the parent and optimizer move identity.
    """

    parent_layered_spec_sha256: str
    parent_program_spec_sha256: str
    optimizer_config_fingerprint: str
    active_components_sha256: str
    component_identity_sha256: str
    move_ordinal: int
    tick: int
    direction: Literal[-1, 1]
    algorithm_id: str = "bounded_coordinate_pattern_search"
    algorithm_version: str = "uniform_coordinate_v2"
    schema_version: str = "uniform_optimization_provenance_v1"

    def __post_init__(self) -> None:
        """Require complete parent and move bindings for every derivation."""
        for name in (
            "parent_layered_spec_sha256",
            "parent_program_spec_sha256",
            "optimizer_config_fingerprint",
            "active_components_sha256",
            "component_identity_sha256",
        ):
            _sha256(getattr(self, name), name=name)
        if (
            isinstance(self.move_ordinal, bool)
            or not isinstance(self.move_ordinal, int)
            or self.move_ordinal < 1
            or self.direction not in {-1, 1}
            or self.tick == 0
            or (self.tick > 0) != (self.direction == 1)
        ):
            raise UniformOptimizationError(
                "invalid_provenance", "invalid optimizer move"
            )

    def to_dict(self) -> dict[str, str | int]:
        """Return a canonical JSON-safe trusted provenance record."""
        return {
            "schema_version": self.schema_version,
            "parent_layered_spec_sha256": self.parent_layered_spec_sha256,
            "parent_program_spec_sha256": self.parent_program_spec_sha256,
            "algorithm_id": self.algorithm_id,
            "algorithm_version": self.algorithm_version,
            "optimizer_config_fingerprint": self.optimizer_config_fingerprint,
            "active_components_sha256": self.active_components_sha256,
            "component_identity_sha256": self.component_identity_sha256,
            "move_ordinal": self.move_ordinal,
            "tick": self.tick,
            "direction": self.direction,
        }


@dataclass(frozen=True, slots=True)
class UniformPatchV1:
    """Trusted patch that can change exactly one manifest component."""

    base_layered_spec_sha256: str
    base_program_spec_sha256: str
    target_layer_id: str
    path: str
    component_index: int
    lattice_base_value: Decimal
    expected_value: Decimal
    replacement_value: Decimal
    tick: int
    derivation: UniformOptimizationProvenanceV1
    schema_version: str = "uniform_patch_v1"

    def __post_init__(self) -> None:
        """Require a non-noop trusted move before it is applied."""
        _sha256(self.base_layered_spec_sha256, name="base_layered_spec_sha256")
        _sha256(self.base_program_spec_sha256, name="base_program_spec_sha256")
        if not self.target_layer_id or not self.path or self.component_index < 0:
            raise UniformOptimizationError("invalid_patch", "invalid patch target")
        for name in ("lattice_base_value", "expected_value", "replacement_value"):
            _decimal(getattr(self, name), name=name)
        if self.expected_value == self.replacement_value:
            raise UniformOptimizationError("invalid_patch", "patch must make one move")
        if self.tick != self.derivation.tick:
            raise UniformOptimizationError(
                "invalid_patch",
                "patch tick must match trusted derivation",
            )


@dataclass(frozen=True, slots=True)
class UniformOptimizationSummaryV2:
    """Safe source-session aggregate; detailed vectors remain private."""

    base_spec_sha256: str
    selected_spec_sha256: str
    config_fingerprint: str
    active_component_count: int
    evaluated_count: int
    accepted_count: int
    draw_count: int
    draw_budget: int
    initial_loss: float
    initial_mae: float
    final_loss: float
    final_mae: float
    loss_delta: float
    mae_delta: float
    stop_reason: str
    private_trace_sha256: str | None = None
    algorithm_id: str = "bounded_coordinate_pattern_search"
    algorithm_version: str = "uniform_coordinate_v2"
    schema_version: str = "uniform_optimization_summary_v2"

    def __post_init__(self) -> None:
        """Reject non-finite or internally inconsistent safe summaries."""
        if self.schema_version != "uniform_optimization_summary_v2":
            raise UniformOptimizationError(
                "invalid_summary", "unsupported optimizer summary schema"
            )
        _sha256(self.base_spec_sha256, name="base_spec_sha256")
        _sha256(self.selected_spec_sha256, name="selected_spec_sha256")
        _sha256(self.config_fingerprint, name="config_fingerprint")
        if self.private_trace_sha256 is not None:
            _sha256(self.private_trace_sha256, name="private_trace_sha256")
        if (
            any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in (
                    self.active_component_count,
                    self.evaluated_count,
                    self.accepted_count,
                    self.draw_count,
                    self.draw_budget,
                )
            )
            or self.accepted_count > self.evaluated_count
        ):
            raise UniformOptimizationError(
                "invalid_summary", "invalid optimizer counts"
            )
        if (
            not all(
                math.isfinite(value)
                for value in (
                    self.initial_loss,
                    self.initial_mae,
                    self.final_loss,
                    self.final_mae,
                    self.loss_delta,
                    self.mae_delta,
                )
            )
            or not self.stop_reason
        ):
            raise UniformOptimizationError(
                "invalid_summary", "invalid optimizer summary"
            )

    def to_dict(self) -> dict[str, str | int | float | None]:
        """Return the public-safe aggregate without raw paths, values, or trace."""
        return {
            "schema_version": self.schema_version,
            "algorithm_id": self.algorithm_id,
            "algorithm_version": self.algorithm_version,
            "config_fingerprint": self.config_fingerprint,
            "active_component_count": self.active_component_count,
            "evaluated_count": self.evaluated_count,
            "accepted_count": self.accepted_count,
            "draw_count": self.draw_count,
            "draw_budget": self.draw_budget,
            "initial_loss": self.initial_loss,
            "initial_mae": self.initial_mae,
            "final_loss": self.final_loss,
            "final_mae": self.final_mae,
            "loss_delta": self.loss_delta,
            "mae_delta": self.mae_delta,
            "stop_reason": self.stop_reason,
            "base_spec_sha256": self.base_spec_sha256,
            "selected_spec_sha256": self.selected_spec_sha256,
            "private_trace_sha256": self.private_trace_sha256,
        }

    def to_safe_dict(self) -> dict[str, str | int | float | None]:
        """Alias the only serialization shape, which is already public-safe."""
        return self.to_dict()
