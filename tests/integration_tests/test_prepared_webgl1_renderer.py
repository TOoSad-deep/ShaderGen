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
from shaderforge.scene import (
    Feature,
    LinearColorField,
    RadialColorField,
    SolidColorField,
)


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
async def test_prepared_renderer_matches_legacy_render_and_never_reuses_stale_frame() -> (
    None
):
    materialized = materialize_min_shader(
        perceive_min_target(_pink_orb_png()).fallback_scene
    )
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
        bg_scale = materialized.uniform_values["u_scene_bg_scale"]
        assert isinstance(bg_scale, tuple)
        changed_values["u_scene_bg_scale"] = (0.0, 0.0, 0.0, bg_scale[3])
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
    materialized = materialize_min_shader(
        perceive_min_target(_pink_orb_png()).fallback_scene
    )
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
            bg_scale = materialized.uniform_values["u_scene_bg_scale"]
            assert isinstance(bg_scale, tuple)
            color = (1.0, 1.0, 1.0) if index % 2 == 0 else (0.0, 0.0, 0.0)
            values["u_scene_bg_scale"] = (*color, bg_scale[3])
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


@pytest.mark.anyio
async def test_rim_polar_arc_and_edge_line_render_distinct_pixels() -> None:
    base = perceive_min_target(_pink_orb_png()).fallback_scene
    common = {
        "center": (0.0, 0.0),
        "axes": (0.65, 0.35),
        "color": (1.0, 1.0, 1.0),
        "intensity": 1.0,
    }
    scenes = [
        base.model_copy(
            update={
                "object": base.object.model_copy(
                    update={"features": (Feature(id=kind, type=kind, **common),)}
                )
            }
        )
        for kind in ("rim", "polar_arc", "edge_line")
    ]
    materialized = [materialize_min_shader(scene) for scene in scenes]

    async with PlaywrightWebGL1Renderer() as renderer:
        prepared = await renderer.prepare(
            materialized[0].webgl1_source,
            192,
            192,
            materialized[0].uniform_schema,
        )
        results = [
            await prepared.render_uniforms(item.uniform_values, capture_png=False)
            for item in materialized
        ]

    pixels = [result.rgb_bytes for result in results]
    assert all(result.success for result in results)
    assert all(value is not None for value in pixels)
    assert len(set(pixels)) == 3


@pytest.mark.anyio
async def test_solid_radial_and_linear_color_fields_render_distinct_pixels() -> None:
    base = perceive_min_target(_pink_orb_png()).fallback_scene
    fields = (
        SolidColorField(model="solid", color=(0.9, 0.2, 0.4)),
        RadialColorField(
            model="radial",
            inner=(1.0, 0.9, 0.9),
            outer=(0.7, 0.0, 0.2),
            origin=(-0.4, 0.4),
            scale=1.1,
        ),
        LinearColorField(
            model="linear",
            start=(1.0, 0.1, 0.3),
            end=(1.0, 0.95, 0.98),
            direction=(0.0, -1.0),
            offset=0.5,
            scale=1.4,
        ),
    )
    materialized = [
        materialize_min_shader(
            base.model_copy(
                update={
                    "object": base.object.model_copy(
                        update={"color_field": field, "features": ()}
                    )
                }
            )
        )
        for field in fields
    ]
    signatures = {
        (item.webgl1_source, tuple(item.uniform_schema.items()))
        for item in materialized
    }

    async with PlaywrightWebGL1Renderer() as renderer:
        prepared = await renderer.prepare(
            materialized[0].webgl1_source,
            192,
            192,
            materialized[0].uniform_schema,
        )
        results = [
            await prepared.render_uniforms(item.uniform_values, capture_png=False)
            for item in materialized
        ]

    assert len(signatures) == 1
    assert all(result.success and result.rgb_bytes is not None for result in results)
    assert len({result.rgb_bytes for result in results}) == 3


@pytest.mark.anyio
async def test_all_six_feature_kinds_and_four_slots_have_live_pixel_semantics() -> None:
    base = perceive_min_target(_pink_orb_png()).fallback_scene
    common = {
        "center": (0.25, 0.1),
        "axes": (0.45, 0.25),
        "color": (0.2, 0.9, 1.0),
        "intensity": 0.9,
    }
    kinds = ("rim", "shadow", "polar_arc", "edge_line", "gaussian_lobe", "glow")
    kind_materialized = [
        materialize_min_shader(
            base.model_copy(
                update={
                    "object": base.object.model_copy(
                        update={"features": (Feature(id=kind, type=kind, **common),)}
                    )
                }
            )
        )
        for kind in kinds
    ]
    four_features = tuple(
        Feature(
            id=f"lobe_{index}",
            type="gaussian_lobe",
            center=(-0.55 + index * 0.35, 0.25 - index * 0.15),
            axes=(0.18, 0.16),
            color=(0.1 + index * 0.2, 0.8, 1.0 - index * 0.2),
            intensity=0.35 + index * 0.15,
        )
        for index in range(4)
    )
    slot_scenes = [
        base.model_copy(
            update={
                "object": base.object.model_copy(
                    update={"features": four_features[:count]}
                )
            }
        )
        for count in range(5)
    ]
    slot_materialized = [materialize_min_shader(scene) for scene in slot_scenes]

    async with PlaywrightWebGL1Renderer() as renderer:
        prepared = await renderer.prepare(
            kind_materialized[0].webgl1_source,
            192,
            192,
            kind_materialized[0].uniform_schema,
        )
        kind_results = [
            await prepared.render_uniforms(item.uniform_values, capture_png=False)
            for item in kind_materialized
        ]
        slot_results = [
            await prepared.render_uniforms(item.uniform_values, capture_png=False)
            for item in slot_materialized
        ]

    assert all(
        result.success and result.rgb_bytes is not None for result in kind_results
    )
    assert len({result.rgb_bytes for result in kind_results}) == len(kinds)
    assert all(
        result.success and result.rgb_bytes is not None for result in slot_results
    )
    assert len({result.rgb_bytes for result in slot_results}) == len(slot_results)
