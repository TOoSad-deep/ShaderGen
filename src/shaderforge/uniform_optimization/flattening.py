"""Flatten manifest-addressable typed uniforms into deterministic scalar moves."""

from __future__ import annotations

import math
import struct
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from shaderforge.layered_spec import (
    LayeredShaderSpecV1,
    compile_layered_shader,
    recompute_layer_sha256,
    recompute_layered_spec_sha256,
)
from shaderforge.program_spec import (
    ShaderProgramSpecV1,
    recompute_binding_sha256,
    recompute_source_sha256,
    recompute_spec_sha256,
)
from shaderforge.uniform_optimization.models import (
    FlatTunableComponent,
    UniformOptimizationError,
)


def decimal_from_number(value: Any, *, name: str) -> Decimal:
    """Convert an existing finite JSON number through its canonical text form."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise UniformOptimizationError("invalid_number", f"{name} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise UniformOptimizationError("invalid_number", f"{name} is invalid") from exc
    if not result.is_finite():
        raise UniformOptimizationError("invalid_number", f"{name} must be finite")
    return result


def lattice_value(component: FlatTunableComponent, tick: int) -> Decimal:
    """Return the clamped candidate on the component's base-anchored lattice."""
    if isinstance(tick, bool) or not isinstance(tick, int):
        raise UniformOptimizationError("invalid_tick", "tick must be an integer")
    value = component.base_value + Decimal(tick) * component.step
    return min(component.maximum, max(component.minimum, value))


def webgl_float32(value: Decimal | int | float) -> float:
    """Return the finite IEEE-754 value that WebGL ``uniform*f`` receives.

    The renderer sends JavaScript numbers to ``gl.uniform*f``. WebGL converts
    those inputs to ``GLfloat`` (binary32), so search feasibility must use that
    transport precision rather than Python's binary64 representation.
    """
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise UniformOptimizationError(
            "unrepresentable_uniform", "uniform value cannot be represented"
        ) from exc
    if not math.isfinite(number):
        raise UniformOptimizationError(
            "unrepresentable_uniform", "uniform value must be finite"
        )
    try:
        result = struct.unpack("!f", struct.pack("!f", number))[0]
    except OverflowError as exc:
        raise UniformOptimizationError(
            "unrepresentable_uniform", "uniform value exceeds WebGL float range"
        ) from exc
    if not math.isfinite(result):
        raise UniformOptimizationError(
            "unrepresentable_uniform", "uniform value must be finite in WebGL"
        )
    return cast(float, result)


def _values(value: Any, *, count: int, name: str) -> tuple[Any, ...]:
    if count == 1:
        return (value,)
    if not isinstance(value, tuple) or len(value) != count:
        raise UniformOptimizationError("spec_pair_mismatch", f"{name} has wrong shape")
    return value


def _verify_pair(layered: LayeredShaderSpecV1, program: ShaderProgramSpecV1) -> None:
    if recompute_layered_spec_sha256(layered) != layered.layered_spec_sha256 or any(
        recompute_layer_sha256(layer) != layer.layer_sha256 for layer in layered.layers
    ):
        raise UniformOptimizationError(
            "layered_hash_mismatch", "Layered Spec is corrupted"
        )
    if (
        recompute_source_sha256(program) != program.source_sha256
        or recompute_binding_sha256(program) != program.binding_sha256
        or recompute_spec_sha256(program) != program.spec_sha256
    ):
        raise UniformOptimizationError(
            "program_hash_mismatch", "Program Spec is corrupted"
        )
    compiled = compile_layered_shader(layered)
    if (
        compiled.source_sha256 != program.source_sha256
        or compiled.uniform_schema != program.uniform_schema
        or dict(compiled.uniform_values) != dict(program.uniform_values)
        or compiled.tunable_manifest != program.tunable_manifest
        or compiled.canvas != program.canvas
        or compiled.renderer_contract_id != program.renderer_contract_id
    ):
        raise UniformOptimizationError(
            "spec_pair_mismatch", "Layered and Program Specs are not one source pair"
        )


def flatten_tunable_components(
    layered: LayeredShaderSpecV1,
    program: ShaderProgramSpecV1,
) -> tuple[FlatTunableComponent, ...]:
    """Return feasible scalar components in Layered canonical order.

    The function verifies the Layered/Program pair before trusting its manifest;
    malformed or non-manifest fields fail closed rather than becoming optimizer
    controls.
    """
    _verify_pair(layered, program)
    components: list[FlatTunableComponent] = []
    for layer in layered.layers:
        declarations = {item.name: item for item in layer.uniform_schema}
        for tunable in sorted(layer.tunable_manifest, key=lambda item: item.path):
            declaration = declarations.get(tunable.path)
            if declaration is None or declaration.type != tunable.type:
                raise UniformOptimizationError(
                    "invalid_manifest", "tunable does not match its layer declaration"
                )
            values = _values(
                layer.uniform_values.get(tunable.path),
                count=declaration.component_count,
                name=tunable.path,
            )
            for index in range(declaration.component_count):
                component = FlatTunableComponent(
                    layer_id=layer.layer_id,
                    path=tunable.path,
                    component_index=index,
                    minimum=decimal_from_number(
                        tunable.minimum[index], name=f"{tunable.path}.minimum"
                    ),
                    maximum=decimal_from_number(
                        tunable.maximum[index], name=f"{tunable.path}.maximum"
                    ),
                    step=decimal_from_number(tunable.step, name=f"{tunable.path}.step"),
                    base_value=decimal_from_number(
                        values[index], name=f"{tunable.path}[{index}]"
                    ),
                )
                base_transport_value = webgl_float32(component.base_value)
                if (
                    webgl_float32(lattice_value(component, 1))
                    != base_transport_value
                    or webgl_float32(lattice_value(component, -1))
                    != base_transport_value
                ):
                    components.append(component)
    return tuple(components)
