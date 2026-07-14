from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from shaderforge.analysis import measure_target
from shaderforge.evaluation import evaluate_render
from shaderforge.rendering import PlaywrightWebGL1Renderer
from shaderforge.validation import repair_constant_reversed_smoothsteps, validate_shader

ROOT = Path(__file__).resolve().parents[2]
GOLDEN_SHADER = ROOT / "benchmarks/png_to_shader_v1/golden/pink_gel.frag"
REFERENCE_IMAGE = ROOT / "benchmarks/png_to_shader_v1/images/pink_gel.png"


@pytest.mark.anyio
async def test_m1_webgl1_golden_smoke_is_stable_and_never_returns_stale_png():
    source = GOLDEN_SHADER.read_text(encoding="utf-8")
    reference = REFERENCE_IMAGE.read_bytes()
    syntax_invalid = source.replace("vec3 color = vec3(0.9961);", "vec3 color = ;")

    async with PlaywrightWebGL1Renderer() as renderer:
        first = await renderer.render(source, 192, 192)
        second = await renderer.render(source, 192, 192)
        compile_failure = await renderer.render(syntax_invalid, 192, 192)
        after_failure = await renderer.render(source, 192, 192)

    assert first.success
    assert first.compile.success
    assert first.image_bytes is not None
    assert first.image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert first.console_errors == ()
    assert first.metadata is not None
    assert "WebGL" in first.metadata.gl_version

    assert second.image_bytes == first.image_bytes
    assert second.image_sha256 == first.image_sha256
    assert not compile_failure.success
    assert compile_failure.image_bytes is None
    assert compile_failure.compile.fragment_log
    assert after_failure.success
    assert after_failure.image_bytes == first.image_bytes

    with Image.open(BytesIO(first.image_bytes)) as image:
        assert image.size == (192, 192)
        assert image.mode in {"RGB", "RGBA"}
        center = image.convert("RGB").getpixel((96, 96))
        corner = image.convert("RGB").getpixel((0, 0))
    assert center[0] > center[1] + 25
    assert min(corner) > 240

    measurements = measure_target(first.image_bytes)
    score = evaluate_render(reference, first.image_bytes)
    assert measurements.foreground_bbox_uv is not None
    assert measurements.foreground_confidence > 0.5
    assert 0.0 <= score.total_loss < 0.5


@pytest.mark.anyio
async def test_reversed_smoothstep_regression_is_repaired_before_real_webgl() -> None:
    """复现 M5 canary 的倒序常量边界，并通过真实 Chromium WebGL 门禁."""
    source = """precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_image;
uniform vec2 u_resolution;
uniform float u_time;

void main() {
  float distance_from_center = length(v_uv - 0.5);
  float inverse_mask = smoothstep(0.5, 0.3, distance_from_center);
  gl_FragColor = vec4(vec3(inverse_mask), 1.0);
}
"""

    assert not validate_shader(source).valid
    repair = repair_constant_reversed_smoothsteps(source)
    assert repair is not None
    assert repair.replacement_count == 1
    assert validate_shader(repair.source).valid

    async with PlaywrightWebGL1Renderer() as renderer:
        rendered = await renderer.render(repair.source, 64, 64)

    assert rendered.success
    assert rendered.compile.success
    assert rendered.image_bytes is not None
