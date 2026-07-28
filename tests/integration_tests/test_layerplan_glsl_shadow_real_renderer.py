"""shadow Author 契约形状在真实 PlaywrightWebGL1Renderer 上的 prepare+draw 验收.

证明 Agent 薄 adapter 接受的 canonical 声明形状（含仅声明不可采样的
``uniform sampler2D u_image;``）能通过真实 Chromium WebGL1 的
validate_shader + compile/link/draw，而不是只在 fake renderer 下成立。
"""

from __future__ import annotations

import json
from hashlib import sha256
from io import BytesIO

import pytest
from PIL import Image

from agent.app.contracts.layerplan_glsl_shadow import (
    assemble_program_spec,
    parse_program_spec_semantics,
)
from shaderforge.program_spec import (
    build_author_identity,
    is_executable,
    issue_attestation,
    process_receipt_verifier,
)
from shaderforge.rendering import PlaywrightWebGL1Renderer
from shaderforge.validation import validate_program_spec_safety

CANVAS = 64

_FRAGMENT_SOURCE = (
    "precision mediump float;\n"
    "varying vec2 v_uv;\n"
    "uniform sampler2D u_image;\n"
    "uniform vec2 u_resolution;\n"
    "uniform float u_time;\n"
    "uniform float u_gain;\n"
    "void main(){gl_FragColor=vec4(vec3(u_gain),1.0);}\n"
)

_COORDINATE_CONTRACT_SOURCE = (
    "precision mediump float;\n"
    "varying vec2 v_uv;\n"
    "uniform sampler2D u_image;\n"
    "uniform vec2 u_resolution;\n"
    "uniform float u_time;\n"
    "void main(){\n"
    "  vec3 color = v_uv.y > 0.5 ? vec3(1.0,0.0,0.0) : vec3(0.0,0.0,1.0);\n"
    "  gl_FragColor=vec4(color,1.0);\n"
    "}\n"
)


def _spec() -> object:
    payload = {
        "schema_version": "shader_program_spec_v1",
        "fragment_source": _FRAGMENT_SOURCE,
        "uniform_schema": {
            "u_gain": {
                "type": "float",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.5,
            }
        },
        "uniform_values": {"u_gain": 0.5},
        "tunable_manifest": [
            {
                "path": "u_gain",
                "type": "float",
                "minimum": 0.0,
                "maximum": 1.0,
                "step": 0.01,
            }
        ],
        "canvas": {"width": CANVAS, "height": CANVAS},
        "renderer_contract_id": "webgl1_static_no_texture_v1",
    }
    semantics = parse_program_spec_semantics(
        json.dumps(payload), expected_width=CANVAS, expected_height=CANVAS
    )
    return assemble_program_spec(
        semantics,
        author_identity=build_author_identity(
            reference_sha256=sha256(b"fake-reference").hexdigest(),
            instruction_sha256=sha256(b"smoke").hexdigest(),
            model_ref="real-renderer-smoke",
            prompt_version="real-renderer-smoke",
            role="initial",
        ),
    )


@pytest.mark.anyio
async def test_shadow_contract_shape_prepares_and_draws_on_real_renderer() -> None:
    spec = _spec()
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
    assert len(result.rgb_bytes) == CANVAS * CANVAS * 3
    assert result.image_bytes is not None
    # u_gain=0.5 的纯色灰图：所有像素一致且接近 128。
    assert len(set(result.rgb_bytes)) == 1
    assert abs(result.rgb_bytes[0] - 128) <= 1

    # 真实 renderer 路径就地签发 receipt：进程内可验证、绑定像素与 Spec。
    receipt = result.execution_receipt
    assert receipt is not None
    issuer = process_receipt_verifier()
    assert issuer.verify(receipt)
    assert receipt.rgb_sha256 == sha256(result.rgb_bytes).hexdigest()
    assert receipt.png_sha256 == sha256(result.image_bytes).hexdigest()
    assert receipt.spec_sha256 == spec.spec_sha256
    assert receipt.source_sha256 == spec.source_sha256
    assert receipt.runtime_metadata.get("gl_version")

    attested = spec.with_attestation(
        issue_attestation(spec, receipt=receipt, static_ok=True)
    )
    assert is_executable(attested)


@pytest.mark.anyio
async def test_v_uv_matches_canonical_lower_left_region_coordinates() -> None:
    """返回的左上行像素必须来自较大的 v_uv.y，避免图像行序反转."""
    async with PlaywrightWebGL1Renderer() as renderer:
        prepared = await renderer.prepare(
            _COORDINATE_CONTRACT_SOURCE,
            CANVAS,
            CANVAS,
            {},
        )
        result = await prepared.render_uniforms({}, capture_png=True)
        await prepared.close()

    assert result.success, result.draw_error
    assert result.rgb_bytes is not None
    assert result.image_bytes is not None
    row_size = CANVAS * 3
    assert result.rgb_bytes[:3] == bytes((255, 0, 0))
    assert result.rgb_bytes[-row_size : -row_size + 3] == bytes((0, 0, 255))
    png = Image.open(BytesIO(result.image_bytes)).convert("RGB")
    assert png.getpixel((0, 0)) == (255, 0, 0)
    assert png.getpixel((0, CANVAS - 1)) == (0, 0, 255)
