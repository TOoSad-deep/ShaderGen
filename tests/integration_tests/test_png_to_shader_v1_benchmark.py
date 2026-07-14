from __future__ import annotations

from pathlib import Path

import pytest

from shaderforge.analysis import measure_target
from shaderforge.benchmark import build_ai_off_shader, load_benchmark_suite
from shaderforge.evaluation import evaluate_render
from shaderforge.rendering import PlaywrightWebGL1Renderer
from shaderforge.validation import validate_shader

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "benchmarks/png_to_shader_v1/manifest.yaml"


@pytest.mark.anyio
async def test_ai_off_smoke_crosses_validator_real_webgl_and_oracle() -> None:
    suite = load_benchmark_suite(MANIFEST)
    case = next(item for item in suite.cases if item.case_id == "solid_circle")
    reference = case.image_path.read_bytes()
    measurements = measure_target(reference)
    glsl = build_ai_off_shader(measurements)
    validation = validate_shader(glsl)
    renderer = PlaywrightWebGL1Renderer()
    try:
        render = await renderer.render(
            glsl,
            measurements.analysis_width,
            measurements.analysis_height,
        )
    finally:
        await renderer.close()

    assert validation.valid is True
    assert render.success is True
    assert render.image_bytes is not None
    score = evaluate_render(reference, render.image_bytes, measurements=measurements)
    assert 0.0 <= score.total_loss <= 1.0
