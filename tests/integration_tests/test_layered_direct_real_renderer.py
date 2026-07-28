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


def _compiled_spec():
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
    layered = build_layered_shader_spec(
        {
            "schema_version": "layered_shader_spec_v1",
            "canvas": {"width": CANVAS, "height": CANVAS},
            "layers": [
                {
                    "layer_id": "background",
                    "role": "background",
                    "z_index": 0,
                    "glsl_body": "return vec4(0.25, 0.5, 0.75, 1.0);",
                    "uniform_schema": {},
                    "uniform_values": {},
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
        await prepared.close()

    assert result.success, result.draw_error
    assert result.rgb_bytes is not None
    assert result.rgb_bytes[:3] == bytes((64, 128, 191))
    assert len(result.rgb_bytes) == CANVAS * CANVAS * 3
    assert result.image_bytes is not None
    assert result.execution_receipt is not None
