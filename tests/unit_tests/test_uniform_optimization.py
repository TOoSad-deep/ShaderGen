from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from shaderforge.layered_spec import (
    build_layered_shader_spec,
    compile_layered_shader,
    recompute_layered_spec_sha256,
)
from shaderforge.program_spec import (
    build_author_identity,
    build_layer_author_identity,
    build_layer_plan,
    recompute_spec_sha256,
    sha256_hex_text,
)
from shaderforge.uniform_optimization import (
    FlatTunableComponent,
    UniformOptimizationConfig,
    UniformOptimizationError,
    UniformOptimizationProvenanceV1,
    UniformOptimizationSummaryV2,
    UniformPatchV1,
    active_components_sha256,
    apply_uniform_patch,
    apply_uniform_patch_values,
    component_identity_sha256,
    flatten_tunable_components,
    lattice_value,
    next_coordinate_move,
    record_coordinate_outcome,
    start_coordinate_pattern_session,
)


def test_uniform_summary_v2_projects_both_quality_dimensions() -> None:
    summary = UniformOptimizationSummaryV2(
        base_spec_sha256="a" * 64,
        selected_spec_sha256="b" * 64,
        config_fingerprint="c" * 64,
        active_component_count=4,
        evaluated_count=3,
        accepted_count=1,
        draw_count=3,
        draw_budget=4,
        initial_loss=0.07,
        initial_mae=0.08,
        final_loss=0.075,
        final_mae=0.06,
        loss_delta=-0.005,
        mae_delta=0.02,
        stop_reason="target_reached",
    )

    payload = summary.to_safe_dict()
    assert payload["schema_version"] == "uniform_optimization_summary_v2"
    assert payload["algorithm_version"] == "uniform_coordinate_v2"
    assert payload["initial_mae"] == pytest.approx(0.08)
    assert payload["final_mae"] == pytest.approx(0.06)
    assert payload["mae_delta"] == pytest.approx(0.02)
    assert payload["loss_delta"] == pytest.approx(-0.005)


def _layered_and_program(
    *,
    float_base: float = 0.5,
    float_minimum: float = 0.0,
    float_maximum: float = 1.0,
    float_step: float = 0.1,
):
    reference = sha256_hex_text("uniform-optimizer-reference")
    instruction = sha256_hex_text("uniform-optimizer-instruction")
    plan = build_layer_plan(
        {
            "schema_version": "layer_plan_v1",
            "layers": [
                {
                    "layer_id": "shape",
                    "role": "subject",
                    "z_index": 0,
                    "region": {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8},
                    "dominant_colors": [[0.2, 0.3, 0.4, 1.0]],
                    "confidence": 0.9,
                }
            ],
        },
        reference_sha256=reference,
        author_identity=build_layer_author_identity(
            model_ref="vision", prompt_version="plan_v1"
        ),
    )
    layered = build_layered_shader_spec(
        {
            "schema_version": "layered_shader_spec_v1",
            "canvas": {"width": 32, "height": 32},
            "layers": [
                {
                    "layer_id": "shape",
                    "role": "subject",
                    "z_index": 0,
                    "glsl_body": "return vec4(u_v3, u_float);",
                    "uniform_schema": {
                        "u_vec4": {
                            "type": "vec4",
                            "minimum": [0, 0, 0, 0],
                            "maximum": [1, 1, 1, 1],
                            "default": [0.1, 0.2, 0.3, 0.4],
                        },
                        "u_vec2": {
                            "type": "vec2",
                            "minimum": [0, 0],
                            "maximum": [1, 1],
                            "default": [0.2, 0.3],
                        },
                        "u_float": {
                            "type": "float",
                            "minimum": float_minimum,
                            "maximum": float_maximum,
                            "default": float_base,
                        },
                        "u_v3": {
                            "type": "vec3",
                            "minimum": [0, 0, 0],
                            "maximum": [1, 1, 1],
                            "default": [0.4, 0.5, 0.6],
                        },
                        "u_fixed": {
                            "type": "float",
                            "minimum": 0.5,
                            "maximum": 0.5,
                            "default": 0.5,
                        },
                    },
                    "uniform_values": {
                        "u_vec4": [0.1, 0.2, 0.3, 0.4],
                        "u_vec2": [0.2, 0.3],
                        "u_float": float_base,
                        "u_v3": [0.4, 0.5, 0.6],
                        "u_fixed": 0.5,
                    },
                    "tunable_manifest": [
                        {
                            "path": "u_vec4",
                            "type": "vec4",
                            "minimum": [0, 0, 0, 0],
                            "maximum": [1, 1, 1, 1],
                            "step": 0.1,
                        },
                        {
                            "path": "u_vec2",
                            "type": "vec2",
                            "minimum": [0, 0],
                            "maximum": [1, 1],
                            "step": 0.1,
                        },
                        {
                            "path": "u_float",
                            "type": "float",
                            "minimum": float_minimum,
                            "maximum": float_maximum,
                            "step": float_step,
                        },
                        {
                            "path": "u_v3",
                            "type": "vec3",
                            "minimum": [0, 0, 0],
                            "maximum": [1, 1, 1],
                            "step": 0.1,
                        },
                        {
                            "path": "u_fixed",
                            "type": "float",
                            "minimum": 0.5,
                            "maximum": 0.5,
                            "step": 0.1,
                        },
                    ],
                }
            ],
        },
        plan,
        build_author_identity(
            reference_sha256=reference,
            instruction_sha256=instruction,
            model_ref="shader",
            prompt_version="initial_v1",
            role="initial",
            plan_sha256=plan.plan_sha256,
        ),
    )
    return layered, compile_layered_shader(layered)


def test_flatten_supports_all_uniform_types_in_canonical_component_order() -> None:
    layered, program = _layered_and_program()

    components = flatten_tunable_components(layered, program)

    assert [item.canonical_path for item in components] == [
        "shape:u_float[0]",
        "shape:u_v3[0]",
        "shape:u_v3[1]",
        "shape:u_v3[2]",
        "shape:u_vec2[0]",
        "shape:u_vec2[1]",
        "shape:u_vec4[0]",
        "shape:u_vec4[1]",
        "shape:u_vec4[2]",
        "shape:u_vec4[3]",
    ]
    assert all(item.path != "u_fixed" for item in components)


def test_lattice_is_decimal_base_anchored_and_clamped() -> None:
    component = FlatTunableComponent(
        layer_id="shape",
        path="u_float",
        component_index=0,
        minimum=Decimal("0"),
        maximum=Decimal("0.3"),
        step=Decimal("0.1"),
        base_value=Decimal("0.15"),
    )

    assert lattice_value(component, 1) == Decimal("0.25")
    assert lattice_value(component, 2) == Decimal("0.3")
    assert lattice_value(component, -99) == Decimal("0")


def test_flatten_omits_lattice_moves_that_are_webgl_float32_noops() -> None:
    layered, program = _layered_and_program(
        float_base=1.0,
        float_maximum=2.0,
        float_step=1e-20,
    )

    components = flatten_tunable_components(layered, program)

    assert all(item.canonical_path != "shape:u_float[0]" for item in components)


def test_pattern_search_probes_minus_only_after_non_material_plus() -> None:
    component = FlatTunableComponent(
        layer_id="shape",
        path="u_float",
        component_index=0,
        minimum=Decimal("0"),
        maximum=Decimal("1"),
        step=Decimal("0.1"),
        base_value=Decimal("0.5"),
    )
    session = start_coordinate_pattern_session(
        base_program_spec_sha256="a" * 64,
        components=(component,),
        config=UniformOptimizationConfig(draw_budget=4, active_component_cap=1),
    )
    session, plus = next_coordinate_move(session)
    assert plus is not None and plus.direction == 1

    session = record_coordinate_outcome(
        session, plus, selected=False, material_improvement=False
    )
    session, minus = next_coordinate_move(session)
    assert minus is not None and minus.direction == -1

    session = record_coordinate_outcome(
        session, minus, selected=True, material_improvement=True
    )
    session, final = next_coordinate_move(session)
    assert final is None
    assert session.stop_reason == "local_optimum"
    assert session.accepted_count == 1


def test_minor_selected_plus_probes_opposite_base_side_not_old_base() -> None:
    component = FlatTunableComponent(
        layer_id="shape",
        path="u_float",
        component_index=0,
        minimum=Decimal("0"),
        maximum=Decimal("1"),
        step=Decimal("0.1"),
        base_value=Decimal("0.5"),
    )
    session = start_coordinate_pattern_session(
        base_program_spec_sha256="c" * 64,
        components=(component,),
        config=UniformOptimizationConfig(draw_budget=4, active_component_cap=1),
    )
    session, plus = next_coordinate_move(session)
    assert plus is not None
    session = record_coordinate_outcome(
        session, plus, selected=True, material_improvement=False
    )

    session, minus = next_coordinate_move(session)

    assert minus is not None
    assert minus.direction == -1
    assert minus.tick == -1
    assert minus.expected_value == Decimal("0.6")
    assert minus.replacement_value == Decimal("0.4")


def test_record_coordinate_outcome_rejects_a_forged_nonadjacent_move() -> None:
    component = FlatTunableComponent(
        layer_id="shape",
        path="u_float",
        component_index=0,
        minimum=Decimal("0"),
        maximum=Decimal("1"),
        step=Decimal("0.1"),
        base_value=Decimal("0.5"),
    )
    session = start_coordinate_pattern_session(
        base_program_spec_sha256="d" * 64,
        components=(component,),
        config=UniformOptimizationConfig(draw_budget=4, active_component_cap=1),
    )
    session, move = next_coordinate_move(session)
    assert move is not None

    with pytest.raises(UniformOptimizationError) as exc_info:
        record_coordinate_outcome(
            session,
            replace(
                move,
                tick=999,
                replacement_value=lattice_value(component, 999),
            ),
            selected=True,
            material_improvement=True,
        )

    assert exc_info.value.code == "invalid_outcome"


def test_active_cap_uses_stable_hash_permutation() -> None:
    components = tuple(
        FlatTunableComponent(
            layer_id="shape",
            path=f"u_{index}",
            component_index=0,
            minimum=Decimal("0"),
            maximum=Decimal("1"),
            step=Decimal("0.1"),
            base_value=Decimal("0.5"),
        )
        for index in range(5)
    )
    config = UniformOptimizationConfig(active_component_cap=2)
    first = start_coordinate_pattern_session(
        base_program_spec_sha256="b" * 64, components=components, config=config
    )
    second = start_coordinate_pattern_session(
        base_program_spec_sha256="b" * 64, components=components, config=config
    )

    assert first.components == second.components
    assert first.dimension_cap_reached is True
    assert len(first.components) == 2
    assert active_components_sha256(first.components) == second.active_components_sha256


def test_trusted_patch_projects_only_one_manifest_component() -> None:
    layered, program = _layered_and_program()
    component = next(
        item
        for item in flatten_tunable_components(layered, program)
        if item.canonical_path == "shape:u_float[0]"
    )
    config = UniformOptimizationConfig()
    provenance = UniformOptimizationProvenanceV1(
        parent_layered_spec_sha256=layered.layered_spec_sha256,
        parent_program_spec_sha256=program.spec_sha256,
        optimizer_config_fingerprint=config.fingerprint(),
        active_components_sha256=active_components_sha256((component,)),
        component_identity_sha256=component_identity_sha256(component),
        move_ordinal=1,
        tick=1,
        direction=1,
    )
    patch = UniformPatchV1(
        base_layered_spec_sha256=layered.layered_spec_sha256,
        base_program_spec_sha256=program.spec_sha256,
        target_layer_id="shape",
        path="u_float",
        component_index=0,
        lattice_base_value=component.base_value,
        expected_value=component.base_value,
        replacement_value=lattice_value(component, 1),
        tick=1,
        derivation=provenance,
    )

    projection = apply_uniform_patch_values(layered, program, patch)

    assert projection.layer_uniform_values["u_float"] == 0.6
    assert projection.program_uniform_values["u_float"] == 0.6
    assert (
        projection.program_uniform_values["u_vec2"] == program.uniform_values["u_vec2"]
    )

    with pytest.raises(UniformOptimizationError) as exc_info:
        apply_uniform_patch_values(
            layered,
            program,
            replace(patch, path="u_missing"),
        )
    assert exc_info.value.code == "non_manifest_patch"


def test_patch_rejects_a_webgl_float32_binding_noop() -> None:
    layered, program = _layered_and_program(
        float_base=1.0,
        float_maximum=2.0,
        float_step=1e-7,
    )
    component = next(
        item
        for item in flatten_tunable_components(layered, program)
        if item.canonical_path == "shape:u_float[0]"
    )
    lattice_base = Decimal("0.99999991")
    replacement = Decimal("1.00000001")
    config = UniformOptimizationConfig()
    provenance = UniformOptimizationProvenanceV1(
        parent_layered_spec_sha256=layered.layered_spec_sha256,
        parent_program_spec_sha256=program.spec_sha256,
        optimizer_config_fingerprint=config.fingerprint(),
        active_components_sha256=active_components_sha256((component,)),
        component_identity_sha256=component_identity_sha256(
            replace(component, base_value=lattice_base)
        ),
        move_ordinal=1,
        tick=1,
        direction=1,
    )
    patch = UniformPatchV1(
        base_layered_spec_sha256=layered.layered_spec_sha256,
        base_program_spec_sha256=program.spec_sha256,
        target_layer_id=component.layer_id,
        path=component.path,
        component_index=component.component_index,
        lattice_base_value=lattice_base,
        expected_value=component.base_value,
        replacement_value=replacement,
        tick=1,
        derivation=provenance,
    )

    with pytest.raises(UniformOptimizationError) as exc_info:
        apply_uniform_patch_values(layered, program, patch)

    assert exc_info.value.code == "no_op_uniform_patch"


def test_provenance_move_ordinal_starts_at_one() -> None:
    component = FlatTunableComponent(
        layer_id="shape",
        path="u_float",
        component_index=0,
        minimum=Decimal("0"),
        maximum=Decimal("1"),
        step=Decimal("0.1"),
        base_value=Decimal("0.5"),
    )
    with pytest.raises(UniformOptimizationError) as exc_info:
        UniformOptimizationProvenanceV1(
            parent_layered_spec_sha256="a" * 64,
            parent_program_spec_sha256="b" * 64,
            optimizer_config_fingerprint="c" * 64,
            active_components_sha256=active_components_sha256((component,)),
            component_identity_sha256=component_identity_sha256(component),
            move_ordinal=0,
            tick=1,
            direction=1,
        )

    assert exc_info.value.code == "invalid_provenance"


def test_trusted_patch_rebuilds_hashes_and_preserves_model_author() -> None:
    layered, program = _layered_and_program()
    component = next(
        item
        for item in flatten_tunable_components(layered, program)
        if item.canonical_path == "shape:u_float[0]"
    )
    config = UniformOptimizationConfig()
    provenance = UniformOptimizationProvenanceV1(
        parent_layered_spec_sha256=layered.layered_spec_sha256,
        parent_program_spec_sha256=program.spec_sha256,
        optimizer_config_fingerprint=config.fingerprint(),
        active_components_sha256=active_components_sha256((component,)),
        component_identity_sha256=component_identity_sha256(component),
        move_ordinal=1,
        tick=1,
        direction=1,
    )
    patch = UniformPatchV1(
        base_layered_spec_sha256=layered.layered_spec_sha256,
        base_program_spec_sha256=program.spec_sha256,
        target_layer_id=component.layer_id,
        path=component.path,
        component_index=component.component_index,
        lattice_base_value=component.base_value,
        expected_value=component.base_value,
        replacement_value=lattice_value(component, 1),
        tick=1,
        derivation=provenance,
    )

    applied = apply_uniform_patch(layered, program, patch)

    assert applied.layered_spec.author_identity == layered.author_identity
    assert applied.program_spec.author_identity == program.author_identity
    assert applied.layered_spec.derivation_provenance == provenance
    assert applied.program_spec.derivation_provenance == provenance
    assert applied.program_spec.source_sha256 == program.source_sha256
    assert applied.program_spec.binding_sha256 != program.binding_sha256
    assert applied.program_spec.spec_sha256 != program.spec_sha256
    assert applied.program_spec.validation_attestation is None
    assert (
        recompute_layered_spec_sha256(applied.layered_spec)
        == applied.layered_spec.layered_spec_sha256
    )
    assert (
        recompute_spec_sha256(applied.program_spec) == applied.program_spec.spec_sha256
    )
