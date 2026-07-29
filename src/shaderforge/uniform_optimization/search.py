"""Pure bounded coordinate-pattern session state."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256
from typing import Literal

from shaderforge.uniform_optimization.flattening import lattice_value, webgl_float32
from shaderforge.uniform_optimization.hashing import active_components_sha256
from shaderforge.uniform_optimization.models import (
    FlatTunableComponent,
    UniformOptimizationConfig,
    UniformOptimizationError,
)

Direction = Literal[-1, 1]
Phase = Literal["plus", "minus"]


@dataclass(frozen=True, slots=True)
class CoordinateMove:
    """One proposed lattice move; scoring and acceptance remain external."""

    component: FlatTunableComponent
    tick: int
    direction: Direction
    ordinal: int
    expected_value: Decimal
    replacement_value: Decimal


@dataclass(frozen=True, slots=True)
class CoordinatePatternSession:
    """JSON-safe state for a bounded sequential coordinate search."""

    base_program_spec_sha256: str
    components: tuple[FlatTunableComponent, ...]
    config: UniformOptimizationConfig
    ticks: tuple[int, ...]
    cursor: int = 0
    phase: Phase = "plus"
    pass_index: int = 0
    material_improved_in_pass: bool = False
    evaluated_count: int = 0
    failed_probe_count: int = 0
    accepted_count: int = 0
    dimension_cap_reached: bool = False
    stop_reason: str | None = None

    def __post_init__(self) -> None:
        """Reject mismatched component state before a search can resume."""
        if len(self.base_program_spec_sha256) != 64:
            raise UniformOptimizationError("invalid_session", "invalid base spec hash")
        if len(self.components) != len(self.ticks):
            raise UniformOptimizationError(
                "invalid_session", "component ticks mismatch"
            )
        if (
            self.cursor < 0
            or self.pass_index < 0
            or self.evaluated_count < 0
            or self.failed_probe_count < 0
            or self.accepted_count < 0
            or self.accepted_count > self.evaluated_count
        ):
            raise UniformOptimizationError(
                "invalid_session", "invalid session counters"
            )

    @property
    def probe_count(self) -> int:
        """Return all consumed local probes without inflating real evaluations."""
        return self.evaluated_count + self.failed_probe_count

    @property
    def active_components_sha256(self) -> str:
        """Return the stable active-set identity bound into provenance."""
        return active_components_sha256(self.components)


def _permutation_key(
    base_spec_sha256: str, component: FlatTunableComponent, version: str
) -> str:
    return sha256(
        f"{base_spec_sha256}:{version}:{component.canonical_path}".encode()
    ).hexdigest()


def start_coordinate_pattern_session(
    *,
    base_program_spec_sha256: str,
    components: tuple[FlatTunableComponent, ...],
    config: UniformOptimizationConfig,
) -> CoordinatePatternSession:
    """Select a deterministic active set and initialize one bounded session."""
    ordered = tuple(
        sorted(
            components,
            key=lambda item: _permutation_key(
                base_program_spec_sha256, item, config.algorithm_version
            ),
        )
    )
    capped = ordered[: config.active_component_cap]
    stop_reason = None
    if not capped:
        stop_reason = "no_tunables"
    elif config.draw_budget == 0 or config.max_passes == 0:
        stop_reason = "uniform_tuning_budget_exhausted"
    return CoordinatePatternSession(
        base_program_spec_sha256=base_program_spec_sha256,
        components=capped,
        config=config,
        ticks=(0,) * len(capped),
        dimension_cap_reached=len(ordered) > len(capped),
        stop_reason=stop_reason,
    )


def _finish_pass(session: CoordinatePatternSession) -> CoordinatePatternSession:
    completed_passes = session.pass_index + 1
    if (
        session.material_improved_in_pass
        and completed_passes < session.config.max_passes
    ):
        return replace(
            session,
            cursor=0,
            phase="plus",
            pass_index=completed_passes,
            material_improved_in_pass=False,
        )
    if session.failed_probe_count > 0 and not session.material_improved_in_pass:
        reason = "candidate_failures_exhausted"
    else:
        reason = (
            "dimension_cap_reached_local_optimum"
            if session.dimension_cap_reached
            else "local_optimum"
        )
    return replace(session, stop_reason=reason)


def _finish_probe_budget(
    session: CoordinatePatternSession,
) -> CoordinatePatternSession:
    """Stop a session once its bounded local probe slots are consumed."""
    if (
        session.stop_reason is not None
        or session.probe_count < session.config.draw_budget
    ):
        return session
    reason = (
        "candidate_failures_exhausted"
        if session.failed_probe_count > 0
        and not session.material_improved_in_pass
        else "uniform_tuning_budget_exhausted"
    )
    return replace(session, stop_reason=reason)


def _advance(
    session: CoordinatePatternSession, *, direction: Direction, material: bool
) -> CoordinatePatternSession:
    if direction == 1 and not material:
        return replace(session, phase="minus")
    advanced = replace(
        session,
        cursor=session.cursor + 1,
        phase="plus",
        material_improved_in_pass=session.material_improved_in_pass or material,
    )
    return (
        _finish_pass(advanced)
        if advanced.cursor >= len(advanced.components)
        else advanced
    )


def next_coordinate_move(
    session: CoordinatePatternSession,
) -> tuple[CoordinatePatternSession, CoordinateMove | None]:
    """Return the next feasible move and a session normalized for skipped bounds."""
    current = session
    while current.stop_reason is None:
        if current.probe_count >= current.config.draw_budget:
            return _finish_probe_budget(current), None
        if current.cursor >= len(current.components):
            current = _finish_pass(current)
            continue
        component = current.components[current.cursor]
        direction: Direction = 1 if current.phase == "plus" else -1
        expected = lattice_value(component, current.ticks[current.cursor])
        # The paired negative probe is always the opposite side of the
        # session's original lattice anchor. A strict-but-minor +1 candidate
        # may already be selected by the Graph; probing tick=-1 must then
        # cross that incumbent instead of merely proposing the old tick=0.
        tick = current.ticks[current.cursor] + 1 if direction == 1 else -1
        replacement = lattice_value(component, tick)
        if webgl_float32(replacement) == webgl_float32(expected):
            current = _advance(current, direction=direction, material=False)
            continue
        return current, CoordinateMove(
            component=component,
            tick=tick,
            direction=direction,
            ordinal=current.probe_count + 1,
            expected_value=expected,
            replacement_value=replacement,
        )
    return current, None


def record_coordinate_outcome(
    session: CoordinatePatternSession,
    move: CoordinateMove,
    *,
    selected: bool,
    material_improvement: bool,
) -> CoordinatePatternSession:
    """Advance a session after one externally evaluated candidate outcome."""
    if session.stop_reason is not None or session.cursor >= len(session.components):
        raise UniformOptimizationError(
            "invalid_outcome", "session is not awaiting a move"
        )
    normalized, expected_move = next_coordinate_move(session)
    if normalized != session or expected_move is None or move != expected_move:
        raise UniformOptimizationError(
            "invalid_outcome", "move does not belong to session"
        )
    if material_improvement and not selected:
        raise UniformOptimizationError(
            "invalid_outcome", "material improvement must be selected"
        )
    ticks = list(session.ticks)
    if selected:
        ticks[session.cursor] = move.tick
    updated = replace(
        session,
        ticks=tuple(ticks),
        evaluated_count=session.evaluated_count + 1,
        accepted_count=session.accepted_count + int(selected),
    )
    return _finish_probe_budget(
        _advance(updated, direction=move.direction, material=material_improvement)
    )


def record_coordinate_failure(
    session: CoordinatePatternSession,
    move: CoordinateMove,
) -> CoordinatePatternSession:
    """Consume one failed local probe and advance without a real evaluation."""
    if session.stop_reason is not None or session.cursor >= len(session.components):
        raise UniformOptimizationError(
            "invalid_outcome", "session is not awaiting a move"
        )
    normalized, expected_move = next_coordinate_move(session)
    if normalized != session or expected_move is None or move != expected_move:
        raise UniformOptimizationError(
            "invalid_outcome", "move does not belong to session"
        )
    updated = replace(
        session,
        failed_probe_count=session.failed_probe_count + 1,
    )
    return _finish_probe_budget(
        _advance(updated, direction=move.direction, material=False)
    )
