from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace

import pytest

from agent.app.contracts.layered_direct_glsl import (
    LayeredDirectAuthorParseError,
    layered_shader_spec_json_schema,
    parse_layered_shader_spec_semantics,
)
from shaderforge.layered_spec import (
    LayeredSpecError,
    apply_layer_patch,
    build_layer_patch,
    build_layered_shader_spec,
    compile_layered_shader,
    recompute_layer_sha256,
    recompute_layered_spec_sha256,
)
from shaderforge.program_spec import (
    build_author_identity,
    build_layer_author_identity,
    build_layer_plan,
    sha256_hex_text,
)
from shaderforge.validation import validate_program_spec_safety

REFERENCE_SHA256 = sha256_hex_text("layered-reference")
INSTRUCTION_SHA256 = sha256_hex_text("layered-instruction")


def _plan():
    return build_layer_plan(
        {
            "schema_version": "layer_plan_v1",
            "layers": [
                {
                    "layer_id": "background",
                    "role": "background",
                    "z_index": 0,
                    "region": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
                    "dominant_colors": [[1.0, 1.0, 1.0, 1.0]],
                    "confidence": 1.0,
                    "notes": None,
                },
                {
                    "layer_id": "subject-main",
                    "role": "subject",
                    "z_index": 1,
                    "region": {
                        "x": 0.2,
                        "y": 0.2,
                        "width": 0.6,
                        "height": 0.6,
                    },
                    "dominant_colors": [[0.2, 0.3, 0.8, 1.0]],
                    "confidence": 0.9,
                    "notes": "main disc",
                },
            ],
        },
        reference_sha256=REFERENCE_SHA256,
        author_identity=build_layer_author_identity(
            model_ref="vision-model",
            prompt_version="layer_plan_v1",
        ),
    )


def _initial_identity(plan):
    return build_author_identity(
        reference_sha256=REFERENCE_SHA256,
        instruction_sha256=INSTRUCTION_SHA256,
        model_ref="shader-model",
        prompt_version="direct_layered_initial_v1",
        role="initial",
        sampling_params={"temperature": 0.2},
        plan_sha256=plan.plan_sha256,
    )


def _model_output() -> dict:
    return {
        "schema_version": "layered_shader_spec_v1",
        "canvas": {"width": 128, "height": 128},
        "layers": [
            {
                "layer_id": "background",
                "role": "background",
                "z_index": 0,
                "glsl_body": "return vec4(0.92, 0.94, 1.0, 1.0);",
                "uniform_schema": {},
                "uniform_values": {},
                "tunable_manifest": [],
            },
            {
                "layer_id": "subject-main",
                "role": "subject",
                "z_index": 1,
                "glsl_body": (
                    "float mask = 1.0 - smoothstep(u_subject_radius, "
                    "u_subject_radius + 0.01, length(uv - vec2(0.5)));\n"
                    "return vec4(u_subject_color * mask, mask);"
                ),
                "uniform_schema": {
                    "u_subject_radius": {
                        "type": "float",
                        "minimum": 0.1,
                        "maximum": 0.45,
                        "default": 0.3,
                    },
                    "u_subject_color": {
                        "type": "vec3",
                        "minimum": [0.0, 0.0, 0.0],
                        "maximum": [1.0, 1.0, 1.0],
                        "default": [0.2, 0.3, 0.8],
                    },
                },
                "uniform_values": {
                    "u_subject_radius": 0.3,
                    "u_subject_color": [0.2, 0.3, 0.8],
                },
                "tunable_manifest": [
                    {
                        "path": "u_subject_radius",
                        "type": "float",
                        "minimum": 0.15,
                        "maximum": 0.4,
                        "step": 0.01,
                    }
                ],
            },
        ],
    }


def _spec():
    plan = _plan()
    return build_layered_shader_spec(_model_output(), plan, _initial_identity(plan))


def _patch_output(spec, *, body: str | None = None) -> dict:
    replacement = deepcopy(_model_output()["layers"][1])
    replacement["glsl_body"] = body or (
        "float mask = 1.0 - smoothstep(u_subject_radius, "
        "u_subject_radius + 0.02, length(uv - vec2(0.5)));\n"
        "return vec4(u_subject_color * mask, mask);"
    )
    return {
        "schema_version": "layer_patch_v1",
        "base_layered_spec_sha256": spec.layered_spec_sha256,
        "target_layer_id": "subject-main",
        "expected_layer_sha256": spec.layers[1].layer_sha256,
        "replacement": replacement,
    }


def _refine_identity(spec):
    compiled = compile_layered_shader(spec)
    return build_author_identity(
        reference_sha256=REFERENCE_SHA256,
        instruction_sha256=INSTRUCTION_SHA256,
        model_ref="shader-model",
        prompt_version="direct_layered_refine_v1",
        role="refine",
        parent_spec_sha256=compiled.spec_sha256,
        sampling_params={"temperature": 0.2},
        plan_sha256=spec.plan_sha256,
    )


def test_layered_spec_binds_plan_identity_and_has_stable_hashes() -> None:
    plan = _plan()
    first = build_layered_shader_spec(_model_output(), plan, _initial_identity(plan))
    shuffled = deepcopy(_model_output())
    shuffled["layers"][1]["uniform_schema"] = dict(
        reversed(list(shuffled["layers"][1]["uniform_schema"].items()))
    )
    shuffled["layers"][1]["uniform_values"] = dict(
        reversed(list(shuffled["layers"][1]["uniform_values"].items()))
    )
    second = build_layered_shader_spec(shuffled, plan, _initial_identity(plan))

    assert first.plan_sha256 == plan.plan_sha256
    assert first.layered_spec_sha256 == second.layered_spec_sha256
    assert [layer.layer_sha256 for layer in first.layers] == [
        layer.layer_sha256 for layer in second.layers
    ]
    assert recompute_layered_spec_sha256(first) == first.layered_spec_sha256
    assert all(
        recompute_layer_sha256(layer) == layer.layer_sha256 for layer in first.layers
    )


def test_initial_schema_binds_canvas_and_planned_layer_identity() -> None:
    plan = _plan()
    schema = layered_shader_spec_json_schema(
        layer_plan=plan,
        canvas_width=128,
        canvas_height=96,
    )
    properties = schema["properties"]
    assert isinstance(properties, dict)
    canvas = properties["canvas"]
    layers = properties["layers"]
    assert isinstance(canvas, dict)
    assert isinstance(layers, dict)
    assert canvas["properties"] == {
        "width": {"const": 128},
        "height": {"const": 96},
    }
    assert layers["minItems"] == 2
    assert layers["maxItems"] == 2
    assert layers["items"] is False
    prefix = layers["prefixItems"]
    assert isinstance(prefix, list)
    assert [
        {name: layer["properties"][name] for name in ("layer_id", "role", "z_index")}
        for layer in prefix
    ] == [
        {
            "layer_id": {"const": "background"},
            "role": {"const": "background"},
            "z_index": {"const": 0},
        },
        {
            "layer_id": {"const": "subject-main"},
            "role": {"const": "subject"},
            "z_index": {"const": 1},
        },
    ]


def test_initial_parser_preserves_safe_domain_error_category() -> None:
    output = _model_output()
    output["layers"] = list(reversed(output["layers"]))

    with pytest.raises(LayeredDirectAuthorParseError) as exc_info:
        parse_layered_shader_spec_semantics(
            json.dumps(output),
            layer_plan=_plan(),
        )

    assert exc_info.value.code == ("invalid_layered_shader_spec_layer_plan_mismatch")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("plan_sha256", "0" * 64),
        ("author_identity", {}),
        ("layered_spec_sha256", "0" * 64),
    ],
)
def test_layered_spec_rejects_model_reported_trusted_fields(
    field: str, value: object
) -> None:
    output = _model_output()
    output[field] = value
    plan = _plan()
    with pytest.raises(LayeredSpecError):
        build_layered_shader_spec(output, plan, _initial_identity(plan))


def test_layered_spec_rejects_plan_order_or_identity_mismatch() -> None:
    output = _model_output()
    output["layers"] = list(reversed(output["layers"]))
    plan = _plan()
    with pytest.raises(LayeredSpecError) as exc_info:
        build_layered_shader_spec(output, plan, _initial_identity(plan))
    assert exc_info.value.code == "layer_plan_mismatch"

    wrong_identity = replace(
        _initial_identity(plan), plan_sha256=sha256_hex_text("wrong-plan")
    )
    with pytest.raises(LayeredSpecError) as exc_info:
        build_layered_shader_spec(_model_output(), plan, wrong_identity)
    assert exc_info.value.code == "author_plan_mismatch"


def test_layered_spec_rejects_cross_layer_uniform_collision_and_body_escape() -> None:
    output = _model_output()
    output["layers"][0]["uniform_schema"] = deepcopy(
        output["layers"][1]["uniform_schema"]
    )
    output["layers"][0]["uniform_values"] = deepcopy(
        output["layers"][1]["uniform_values"]
    )
    output["layers"][0]["tunable_manifest"] = []
    plan = _plan()
    with pytest.raises(LayeredSpecError) as exc_info:
        build_layered_shader_spec(output, plan, _initial_identity(plan))
    assert exc_info.value.code == "duplicate_global_uniform"

    for body in (
        "} void main() { gl_FragColor = vec4(1.0);",
        "#define ESCAPE }",
        "float helper(vec2 p) { return p.x; }\nreturn vec4(helper(uv));",
    ):
        escaped = _model_output()
        escaped["layers"][0]["glsl_body"] = body
        with pytest.raises(LayeredSpecError):
            build_layered_shader_spec(escaped, plan, _initial_identity(plan))


def test_compiler_is_deterministic_and_passes_program_spec_safety() -> None:
    spec = _spec()
    first = compile_layered_shader(spec)
    second = compile_layered_shader(spec)

    assert first.fragment_source == second.fragment_source
    assert first.spec_sha256 == second.spec_sha256
    assert "vec4 sg_layer_0_background(vec2 uv)" in first.fragment_source
    assert "vec4 sg_layer_1_subject_main(vec2 uv)" in first.fragment_source
    assert "accum = layer + accum * (1.0 - layer.a);" in first.fragment_source
    assert "gl_FragColor = vec4(opaque_rgb, 1.0);" in first.fragment_source
    safety = validate_program_spec_safety(first)
    assert safety.valid, safety.violations


def test_compiler_repairs_constant_reversed_smoothstep_without_changing_layer() -> None:
    plan = _plan()
    output = _model_output()
    output["layers"][0]["glsl_body"] = (
        "float mask = smoothstep(0.2, 0.1, uv.x); return vec4(vec3(mask), 1.0);"
    )
    layered = build_layered_shader_spec(output, plan, _initial_identity(plan))

    compiled = compile_layered_shader(layered)

    assert "smoothstep(0.2, 0.1" not in compiled.fragment_source
    assert "1.0 - smoothstep(0.1, 0.2" in compiled.fragment_source
    assert validate_program_spec_safety(compiled).valid


def test_patch_replaces_exactly_one_layer_and_preserves_other_objects() -> None:
    base = _spec()
    patch = build_layer_patch(_patch_output(base))
    updated = apply_layer_patch(base, patch, _refine_identity(base))

    assert updated.layers[0] is base.layers[0]
    assert updated.layers[0].layer_sha256 == base.layers[0].layer_sha256
    assert updated.layers[1].layer_sha256 != base.layers[1].layer_sha256
    assert updated.layered_spec_sha256 != base.layered_spec_sha256
    assert base.layers[1].glsl_body != updated.layers[1].glsl_body
    assert validate_program_spec_safety(compile_layered_shader(updated)).valid


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (
            lambda raw: raw.update(base_layered_spec_sha256="0" * 64),
            "base_hash_mismatch",
        ),
        (
            lambda raw: raw.update(expected_layer_sha256="0" * 64),
            "expected_layer_hash_mismatch",
        ),
        (
            lambda raw: raw["replacement"].update(layer_id="different"),
            "replacement_identity_mismatch",
        ),
    ],
)
def test_patch_rejects_hash_or_layer_identity_mismatch(
    mutation, expected_code: str
) -> None:
    base = _spec()
    raw = _patch_output(base)
    mutation(raw)
    patch = build_layer_patch(raw)
    with pytest.raises(LayeredSpecError) as exc_info:
        apply_layer_patch(base, patch, _refine_identity(base))
    assert exc_info.value.code == expected_code
