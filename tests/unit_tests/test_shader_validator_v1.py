from __future__ import annotations

from shaderforge.validation import (
    repair_constant_reversed_smoothsteps,
    validate_shader,
)

VALID_SHADER = """precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_image;
uniform vec2 u_resolution;
uniform float u_time;

void main() {
    vec2 p = v_uv - vec2(0.5);
    float mask = 1.0 - smoothstep(0.29, 0.31, length(p));
    gl_FragColor = vec4(mix(vec3(1.0), vec3(1.0, 0.2, 0.4), mask), 1.0);
}
"""


def test_valid_webgl1_no_texture_shader_passes() -> None:
    result = validate_shader(VALID_SHADER)

    assert result.valid
    assert result.errors == ()
    assert result.contract_id == "webgl1_static_no_texture_v1"


def test_texture_sampling_is_rejected_but_comment_is_ignored() -> None:
    comment_only = VALID_SHADER.replace(
        "void main()", "// 禁止 texture2D(u_image, v_uv)\nvoid main()"
    )
    assert validate_shader(comment_only).valid

    sampled = VALID_SHADER.replace(
        "vec2 p = v_uv - vec2(0.5);",
        "vec4 sampled = texture2D(u_image, v_uv);\n    vec2 p = v_uv - vec2(0.5);",
    )
    result = validate_shader(sampled)

    assert not result.valid
    assert {item.code for item in result.errors} >= {"texture_sampling"}


def test_missing_contract_declarations_are_reported_together() -> None:
    result = validate_shader("precision mediump float;\nvoid main() {}")
    codes = {item.code for item in result.errors}

    assert not result.valid
    assert codes >= {
        "missing_v_uv",
        "missing_u_image",
        "missing_u_resolution",
        "missing_u_time",
        "missing_fragment_output",
    }


def test_webgl2_and_shadertoy_syntax_are_rejected() -> None:
    shader = VALID_SHADER.replace(
        "varying vec2 v_uv;", "in vec2 v_uv;\nout vec4 fragColor;"
    ).replace("void main()", "void mainImage()")
    result = validate_shader(shader)
    codes = {item.code for item in result.errors}

    assert codes >= {"webgl2_io", "custom_fragment_output", "shadertoy_entry"}


def test_derivative_builtins_are_rejected_without_extensions() -> None:
    shader = VALID_SHADER.replace(
        "float mask =",
        "float aa = fwidth(length(p));\n    float mask =",
    )

    result = validate_shader(shader)

    assert {item.code for item in result.errors} >= {"unsupported_derivative_builtin"}


def test_size_and_unbounded_loop_are_rejected() -> None:
    result = validate_shader(VALID_SHADER + "\nwhile (true) {}", max_shader_chars=100)
    codes = {item.code for item in result.errors}

    assert codes >= {"source_too_large", "unbounded_loop"}


def test_numeric_hazards_and_warnings_are_classified() -> None:
    shader = VALID_SHADER.replace(
        "vec2 p = v_uv - vec2(0.5);",
        "vec2 p = normalize(vec2(0.0));\n    float risky = u_resolution.x * u_resolution.x;",
    ).replace("smoothstep(0.29, 0.31", "smoothstep(0.31, 0.29")
    result = validate_shader(shader)
    error_codes = {item.code for item in result.errors}
    warning_codes = {item.code for item in result.warnings}

    assert error_codes >= {"normalize_zero", "reversed_smoothstep_edges"}
    assert warning_codes == {"mediump_large_square_risk"}
    assert result.to_dict()["valid"] is False


def test_constant_reversed_smoothsteps_are_repaired_with_inverse_intent() -> None:
    shader = VALID_SHADER.replace(
        "float mask = 1.0 - smoothstep(0.29, 0.31, length(p));",
        "float a = smoothstep(0.31, 0.29, length(p));\n"
        "    float mask = 1.0 - smoothstep(0.8, 0.2, length(p + vec2(0.1)));",
    )

    repair = repair_constant_reversed_smoothsteps(shader)

    assert repair is not None
    assert repair.replacement_count == 2
    assert repair.repaired_lines == (9, 10)
    assert "(1.0 - smoothstep(0.29, 0.31, length(p)))" in repair.source
    assert "1.0 - (1.0 - smoothstep(0.2, 0.8, length(p + vec2(0.1))))" in repair.source
    assert validate_shader(repair.source).valid


def test_equal_edges_and_comment_text_are_not_deterministically_repaired() -> None:
    shader = VALID_SHADER.replace(
        "smoothstep(0.29, 0.31, length(p))",
        "smoothstep(0.3, 0.3, length(p)) /* smoothstep(0.9, 0.1, x) */",
    )

    assert repair_constant_reversed_smoothsteps(shader) is None
    result = validate_shader(shader)
    assert not result.valid
    assert {item.code for item in result.errors} == {"reversed_smoothstep_edges"}
