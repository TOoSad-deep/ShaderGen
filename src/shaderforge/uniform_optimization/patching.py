"""Validate and project one trusted uniform patch without mutating Specs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from shaderforge.layered_spec import (
    LayeredShaderSpecV1,
    compile_layered_shader,
    compute_layer_sha256,
    compute_layered_spec_sha256,
)
from shaderforge.program_spec import ShaderProgramSpecV1
from shaderforge.uniform_optimization.flattening import (
    flatten_tunable_components,
    lattice_value,
    webgl_float32,
)
from shaderforge.uniform_optimization.hashing import component_identity_sha256
from shaderforge.uniform_optimization.models import (
    FlatTunableComponent,
    UniformOptimizationError,
    UniformPatchV1,
)


@dataclass(frozen=True, slots=True)
class UniformPatchProjection:
    """Verified replacement values for later trusted Spec-derivation wiring."""

    component: FlatTunableComponent
    layer_uniform_values: dict[str, Any]
    program_uniform_values: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AppliedUniformPatch:
    """A fully rehashed Layered/Program pair derived from one trusted patch."""

    layered_spec: LayeredShaderSpecV1
    program_spec: ShaderProgramSpecV1
    component: FlatTunableComponent


def _replace_component(value: Any, *, index: int, replacement: Decimal) -> Any:
    # Validate against WebGL's actual GLfloat transport before retaining the
    # canonical Python number that participates in Spec hashes.
    webgl_float32(replacement)
    number = float(replacement)
    if isinstance(value, tuple):
        result = list(value)
        result[index] = number
        return tuple(result)
    if index != 0:
        raise UniformOptimizationError(
            "invalid_patch", "scalar component index must be zero"
        )
    return number


def apply_uniform_patch_values(
    layered: LayeredShaderSpecV1,
    program: ShaderProgramSpecV1,
    patch: UniformPatchV1,
) -> UniformPatchProjection:
    """Fail-closed validate a patch and return the only permitted value changes.

    Existing Specs have no trusted ``derivation_provenance`` field yet. This
    function intentionally stops at a projected value update rather than using
    ``replace`` to produce a false Spec with stale hashes/attestation. The
    later cross-contract integration must consume this projection to rebuild
    Layered and Program Specs and bind ``patch.derivation`` into their hashes.
    """
    if (
        patch.base_layered_spec_sha256 != layered.layered_spec_sha256
        or patch.base_program_spec_sha256 != program.spec_sha256
    ):
        raise UniformOptimizationError(
            "base_hash_mismatch", "patch base does not match Specs"
        )
    component = next(
        (
            item
            for item in flatten_tunable_components(layered, program)
            if (
                item.layer_id == patch.target_layer_id
                and item.path == patch.path
                and item.component_index == patch.component_index
            )
        ),
        None,
    )
    if component is None:
        raise UniformOptimizationError(
            "non_manifest_patch", "patch target is not feasible manifest control"
        )
    if component.base_value != patch.expected_value:
        raise UniformOptimizationError(
            "expected_value_mismatch", "patch expected value is stale"
        )
    if (
        patch.derivation.parent_layered_spec_sha256 != layered.layered_spec_sha256
        or patch.derivation.parent_program_spec_sha256 != program.spec_sha256
    ):
        raise UniformOptimizationError(
            "provenance_parent_mismatch", "provenance parent mismatch"
        )
    if patch.derivation.component_identity_sha256 != component_identity_sha256(
        replace(component, base_value=patch.lattice_base_value)
    ):
        raise UniformOptimizationError(
            "provenance_component_mismatch",
            "provenance does not bind the patched component",
        )
    lattice_component = replace(component, base_value=patch.lattice_base_value)
    if lattice_value(lattice_component, patch.tick) != patch.replacement_value:
        raise UniformOptimizationError(
            "invalid_lattice_move", "replacement is not on the lattice"
        )
    if webgl_float32(component.base_value) == webgl_float32(
        patch.replacement_value
    ):
        raise UniformOptimizationError(
            "no_op_uniform_patch",
            "patch does not change the WebGL uniform binding",
        )
    layer = next(item for item in layered.layers if item.layer_id == component.layer_id)
    layer_values = dict(layer.uniform_values)
    program_values = dict(program.uniform_values)
    layer_values[component.path] = _replace_component(
        layer_values[component.path],
        index=component.component_index,
        replacement=patch.replacement_value,
    )
    program_values[component.path] = _replace_component(
        program_values[component.path],
        index=component.component_index,
        replacement=patch.replacement_value,
    )
    return UniformPatchProjection(component, layer_values, program_values)


def apply_uniform_patch(
    layered: LayeredShaderSpecV1,
    program: ShaderProgramSpecV1,
    patch: UniformPatchV1,
) -> AppliedUniformPatch:
    """Apply one trusted uniform move and rebuild every affected content hash."""
    projection = apply_uniform_patch_values(layered, program, patch)
    layers = list(layered.layers)
    layer_index = next(
        index
        for index, layer in enumerate(layers)
        if layer.layer_id == patch.target_layer_id
    )
    previous_layer = layers[layer_index]
    layer_hash = compute_layer_sha256(
        layer_id=previous_layer.layer_id,
        role=previous_layer.role,
        z_index=previous_layer.z_index,
        glsl_body=previous_layer.glsl_body,
        uniform_schema=previous_layer.uniform_schema,
        uniform_values=projection.layer_uniform_values,
        tunable_manifest=previous_layer.tunable_manifest,
    )
    layers[layer_index] = replace(
        previous_layer,
        uniform_values=projection.layer_uniform_values,
        layer_sha256=layer_hash,
    )
    result_layers = tuple(layers)
    layered_hash = compute_layered_spec_sha256(
        schema_version=layered.schema_version,
        plan_sha256=layered.plan_sha256,
        canvas=layered.canvas,
        layers=result_layers,
        author_identity=layered.author_identity,
        derivation_provenance=patch.derivation,
    )
    derived_layered = replace(
        layered,
        layers=result_layers,
        layered_spec_sha256=layered_hash,
        derivation_provenance=patch.derivation,
    )
    derived_program = compile_layered_shader(derived_layered)
    if (
        derived_program.source_sha256 != program.source_sha256
        or derived_program.binding_sha256 == program.binding_sha256
        or dict(derived_program.uniform_values) != projection.program_uniform_values
        or derived_program.author_identity != program.author_identity
        or derived_program.derivation_provenance != patch.derivation
        or derived_program.validation_attestation is not None
    ):
        raise UniformOptimizationError(
            "derivation_invariant_failed",
            "trusted uniform derivation changed a protected field",
        )
    return AppliedUniformPatch(
        layered_spec=derived_layered,
        program_spec=derived_program,
        component=projection.component,
    )
