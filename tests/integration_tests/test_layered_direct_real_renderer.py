"""LayeredShaderSpec 确定性编译后在真实 WebGL1 上的最小验收。"""

from __future__ import annotations

from hashlib import sha256

import pytest

from shaderforge.layered_spec import (
    build_layered_shader_spec,
    compile_layered_shader,
)
from shaderforge.program_spec import (
    build_author_identity,
    build_layer_author_identity,
    build_layer_plan,
)
from shaderforge.rendering import PlaywrightWebGL1Renderer
from shaderforge.validation import validate_program_spec_safety

CANVAS = 32


def _compiled_spec(*, uniform_count: int = 0):
    reference_sha256 = sha256(b"layered-renderer-reference").hexdigest()
    plan = build_layer_plan(
        {
            "schema_version": "layer_plan_v1",
            "layers": [
                {
                    "layer_id": "background",
                    "role": "background",
                    "z_index": 0,
                    "region": {
                        "x": 0.0,
                        "y": 0.0,
                        "width": 1.0,
                        "height": 1.0,
                    },
                    "dominant_colors": [[0.25, 0.5, 0.75, 1.0]],
                    "confidence": 1.0,
                    "notes": None,
                }
            ],
        },
        reference_sha256=reference_sha256,
        author_identity=build_layer_author_identity(
            model_ref="layered-real-renderer",
            prompt_version="layer-plan-smoke",
        ),
    )
    uniform_schema = {
        f"u_color_{index}": {
            "type": "vec4",
            "minimum": [0.0, 0.0, 0.0, 0.0],
            "maximum": [1.0, 1.0, 1.0, 1.0],
            "default": [0.25, 0.5, 0.75, 1.0],
        }
        for index in range(uniform_count)
    }
    uniform_values = {name: [0.25, 0.5, 0.75, 1.0] for name in uniform_schema}
    glsl_body = "return vec4(0.25, 0.5, 0.75, 1.0);"
    if uniform_schema:
        color_sum = " + ".join(uniform_schema)
        glsl_body = (
            f"vec4 color = ({color_sum}) / {float(uniform_count):.1f};\n"
            "return vec4(color.rgb, 1.0);"
        )
    layered = build_layered_shader_spec(
        {
            "schema_version": "layered_shader_spec_v1",
            "canvas": {"width": CANVAS, "height": CANVAS},
            "layers": [
                {
                    "layer_id": "background",
                    "role": "background",
                    "z_index": 0,
                    "glsl_body": glsl_body,
                    "uniform_schema": uniform_schema,
                    "uniform_values": uniform_values,
                    "tunable_manifest": [],
                }
            ],
        },
        plan,
        build_author_identity(
            reference_sha256=reference_sha256,
            instruction_sha256=sha256(b"smoke").hexdigest(),
            model_ref="layered-real-renderer",
            prompt_version="direct-layered-smoke",
            role="initial",
            plan_sha256=plan.plan_sha256,
        ),
    )
    return compile_layered_shader(layered)


def _compiled_screen_spec():
    reference_sha256 = sha256(b"layered-screen-reference").hexdigest()
    plan = build_layer_plan(
        {
            "schema_version": "layer_plan_v1",
            "layers": [
                {
                    "layer_id": "background",
                    "role": "background",
                    "z_index": 0,
                    "region": {
                        "x": 0.0,
                        "y": 0.0,
                        "width": 1.0,
                        "height": 1.0,
                    },
                    "dominant_colors": [[0.2, 0.4, 0.8, 1.0]],
                    "confidence": 1.0,
                    "notes": None,
                },
                {
                    "layer_id": "glow",
                    "role": "glow",
                    "z_index": 1,
                    "region": {
                        "x": 0.0,
                        "y": 0.0,
                        "width": 1.0,
                        "height": 1.0,
                    },
                    "dominant_colors": [[1.0, 0.5, 0.0, 0.5]],
                    "confidence": 1.0,
                    "notes": None,
                },
            ],
        },
        reference_sha256=reference_sha256,
        author_identity=build_layer_author_identity(
            model_ref="layered-real-renderer",
            prompt_version="layer-plan-screen",
        ),
    )
    layered = build_layered_shader_spec(
        {
            "schema_version": "layered_shader_spec_v1",
            "canvas": {"width": CANVAS, "height": CANVAS},
            "layers": [
                {
                    "layer_id": "background",
                    "role": "background",
                    "z_index": 0,
                    "blend_mode": "source_over",
                    "glsl_body": "return vec4(0.2, 0.4, 0.8, 1.0);",
                    "uniform_schema": {},
                    "uniform_values": {},
                    "tunable_manifest": [],
                },
                {
                    "layer_id": "glow",
                    "role": "glow",
                    "z_index": 1,
                    "blend_mode": "screen",
                    "glsl_body": "return vec4(0.5, 0.25, 0.0, 0.5);",
                    "uniform_schema": {},
                    "uniform_values": {},
                    "tunable_manifest": [],
                },
            ],
        },
        plan,
        build_author_identity(
            reference_sha256=reference_sha256,
            instruction_sha256=sha256(b"screen-smoke").hexdigest(),
            model_ref="layered-real-renderer",
            prompt_version="direct-layered-screen",
            role="initial",
            plan_sha256=plan.plan_sha256,
        ),
    )
    return compile_layered_shader(layered)


@pytest.mark.anyio
async def test_layered_spec_compiles_and_draws_on_real_webgl1() -> None:
    spec = _compiled_spec()
    safety = validate_program_spec_safety(spec)
    assert safety.valid, [item.code for item in safety.violations]

    async with PlaywrightWebGL1Renderer() as renderer:
        prepared = await renderer.prepare(
            spec.fragment_source,
            CANVAS,
            CANVAS,
            {item.name: item.type for item in spec.uniform_schema},
        )
        result = await prepared.render_uniforms(
            dict(spec.uniform_values),
            capture_png=True,
            receipt_spec_sha256=spec.spec_sha256,
        )
        mask_result = await prepared.render_uniforms(
            dict(spec.uniform_values),
            diagnostic_mode=2.0,
        )
        await prepared.close()

    assert result.success, result.draw_error
    assert result.rgb_bytes is not None
    assert result.rgb_bytes[:3] == bytes((64, 128, 191))
    assert len(result.rgb_bytes) == CANVAS * CANVAS * 3
    assert result.image_bytes is not None
    assert result.execution_receipt is not None
    assert mask_result.success, mask_result.draw_error
    assert mask_result.rgb_bytes is not None
    assert mask_result.rgb_bytes[:3] == bytes((0, 0, 255))
    assert mask_result.execution_receipt is None


@pytest.mark.anyio
async def test_screen_blend_draws_expected_pixels_on_real_webgl1() -> None:
    spec = _compiled_screen_spec()
    safety = validate_program_spec_safety(spec)
    assert safety.valid, [item.code for item in safety.violations]

    async with PlaywrightWebGL1Renderer() as renderer:
        prepared = await renderer.prepare(
            spec.fragment_source,
            CANVAS,
            CANVAS,
            {item.name: item.type for item in spec.uniform_schema},
        )
        result = await prepared.render_uniforms(
            dict(spec.uniform_values),
            capture_png=True,
            receipt_spec_sha256=spec.spec_sha256,
        )
        await prepared.close()

    assert result.success, result.draw_error
    assert result.rgb_bytes is not None
    assert result.rgb_bytes[:3] == pytest.approx(bytes((153, 140, 204)), abs=1)
    assert result.image_bytes is not None
    assert result.execution_receipt is not None


@pytest.mark.anyio
async def test_real_webgl1_decides_uniform_capacity_above_static_defaults() -> None:
    spec = _compiled_spec(uniform_count=18)
    safety = validate_program_spec_safety(spec)
    assert {item.code for item in safety.violations} >= {
        "too_many_uniforms",
        "too_many_uniform_components",
    }

    async with PlaywrightWebGL1Renderer() as renderer:
        prepared = await renderer.prepare(
            spec.fragment_source,
            CANVAS,
            CANVAS,
            {item.name: item.type for item in spec.uniform_schema},
        )
        result = await prepared.render_uniforms(
            dict(spec.uniform_values),
            capture_png=True,
            receipt_spec_sha256=spec.spec_sha256,
        )
        await prepared.close()

    assert result.success, result.draw_error
    assert result.rgb_bytes is not None
    assert result.rgb_bytes[:3] == bytes((64, 128, 191))
    assert result.execution_receipt is not None
