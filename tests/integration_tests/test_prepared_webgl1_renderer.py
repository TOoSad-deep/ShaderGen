"""prepared WebGL1 program 的真实 Chromium 验收与显式性能探针."""

from __future__ import annotations

import math
import os
import time
from hashlib import sha256
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from shaderforge.generation import bake_min_uniforms, materialize_min_shader
from shaderforge.perception import perceive_min_target
from shaderforge.rendering import PlaywrightWebGL1Renderer, RendererUnavailableError


def _pink_orb_png(width: int = 192, height: int = 192) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((14, 14, width - 14, height - 14), fill=(245, 80, 130))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _decoded_rgb(image_bytes: bytes) -> bytes:
    with Image.open(BytesIO(image_bytes)) as image:
        return image.convert("RGB").tobytes()


@pytest.mark.anyio
async def test_prepared_renderer_matches_legacy_render_and_never_reuses_stale_frame() -> None:
    materialized = materialize_min_shader(perceive_min_target(_pink_orb_png()).fallback_scene)
    width = height = 96

    async with PlaywrightWebGL1Renderer() as renderer:
        legacy = await renderer.render(bake_min_uniforms(materialized), width, height)
        prepared = await renderer.prepare(
            materialized.webgl1_source,
            width,
            height,
            materialized.uniform_schema,
        )
        first = await prepared.render_uniforms(
            materialized.uniform_values,
            capture_png=True,
        )
        changed_values = dict(materialized.uniform_values)
        changed_values["u_bg"] = (0.0, 0.0, 0.0)
        changed = await prepared.render_uniforms(changed_values, capture_png=False)
        restored = await prepared.render_uniforms(
            materialized.uniform_values,
            capture_png=False,
        )
        legacy_after_prepare = await renderer.render(
            bake_min_uniforms(materialized), width, height
        )
        after_legacy = await prepared.render_uniforms(
            materialized.uniform_values,
            capture_png=False,
        )

        assert legacy.success and legacy.image_bytes is not None
        assert first.success and first.rgb_bytes is not None
        assert first.image_bytes is not None
        assert _decoded_rgb(legacy.image_bytes) == first.rgb_bytes
        assert _decoded_rgb(first.image_bytes) == first.rgb_bytes
        assert changed.success and changed.rgb_bytes is not None
        assert changed.image_bytes is None
        assert changed.rgb_bytes != first.rgb_bytes
        assert restored.rgb_bytes == first.rgb_bytes
        assert legacy_after_prepare.image_bytes is not None
        assert _decoded_rgb(legacy_after_prepare.image_bytes) == first.rgb_bytes
        assert after_legacy.rgb_bytes == first.rgb_bytes
        assert prepared.render_count == 4

        await prepared.close()
        await prepared.close()
        with pytest.raises(RendererUnavailableError, match="已关闭"):
            await prepared.render_uniforms(materialized.uniform_values)


@pytest.mark.anyio
@pytest.mark.skipif(
    os.getenv("SHADERGEN_RUN_RENDERER_PERFORMANCE_PROBE") != "true",
    reason="100 draw 性能探针只在显式开关下运行。",
)
async def test_prepared_renderer_100_draw_performance_probe() -> None:
    """显式探针：192x192 粉球模板 100 draw，不进入普通 make check."""
    materialized = materialize_min_shader(perceive_min_target(_pink_orb_png()).fallback_scene)
    async with PlaywrightWebGL1Renderer() as renderer:
        prepared = await renderer.prepare(
            materialized.webgl1_source,
            192,
            192,
            materialized.uniform_schema,
        )
        durations: list[float] = []
        frame_hashes: list[str] = []
        started = time.perf_counter()
        for index in range(100):
            values = dict(materialized.uniform_values)
            values["u_bg"] = (1.0, 1.0, 1.0) if index % 2 == 0 else (0.0, 0.0, 0.0)
            result = await prepared.render_uniforms(values, capture_png=False)
            assert result.success and result.rgb_bytes is not None
            assert result.image_bytes is None
            durations.append(result.duration_ms)
            frame_hashes.append(sha256(result.rgb_bytes).hexdigest())
        total_seconds = time.perf_counter() - started

    ordered = sorted(durations)
    p95_ms = ordered[math.ceil(len(ordered) * 0.95) - 1]
    assert total_seconds <= 45.0
    assert p95_ms <= 450.0
    assert len(set(frame_hashes[::2])) == 1
    assert len(set(frame_hashes[1::2])) == 1
    assert frame_hashes[0] != frame_hashes[1]
    assert result.rgb_bytes is not None
    assert len(result.rgb_bytes) == 192 * 192 * 3
