"""Layered Spec 支持的 blend mode 契约与固定 GLSL 实现。."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

BlendMode = Literal[
    "source_over",
    "add",
    "screen",
    "multiply",
    "overlay",
    "soft_light",
    "lighten",
    "darken",
]
BLEND_MODES: tuple[BlendMode, ...] = (
    "source_over",
    "add",
    "screen",
    "multiply",
    "overlay",
    "soft_light",
    "lighten",
    "darken",
)
DEFAULT_BLEND_MODE: BlendMode = "source_over"

_BLEND_COMMON_SOURCE = """vec3 sg_straight_rgb(vec4 color) {
    return color.a > 0.0
        ? clamp(color.rgb / color.a, 0.0, 1.0)
        : vec3(0.0);
}

vec4 sg_compose_blended(vec4 backdrop, vec4 source, vec3 blended_rgb) {
    float out_alpha = source.a + backdrop.a * (1.0 - source.a);
    vec3 out_rgb =
        backdrop.rgb * (1.0 - source.a)
        + source.rgb * (1.0 - backdrop.a)
        + blended_rgb * (source.a * backdrop.a);
    return vec4(clamp(out_rgb, vec3(0.0), vec3(out_alpha)), out_alpha);
}"""

_BLEND_HELPER_SOURCES: dict[BlendMode, str] = {
    "add": """vec4 sg_compose_add(vec4 backdrop, vec4 source) {
    vec3 base = sg_straight_rgb(backdrop);
    vec3 blend = sg_straight_rgb(source);
    return sg_compose_blended(backdrop, source, min(base + blend, vec3(1.0)));
}""",
    "screen": """vec4 sg_compose_screen(vec4 backdrop, vec4 source) {
    vec3 base = sg_straight_rgb(backdrop);
    vec3 blend = sg_straight_rgb(source);
    return sg_compose_blended(
        backdrop, source, base + blend - base * blend
    );
}""",
    "multiply": """vec4 sg_compose_multiply(vec4 backdrop, vec4 source) {
    vec3 base = sg_straight_rgb(backdrop);
    vec3 blend = sg_straight_rgb(source);
    return sg_compose_blended(backdrop, source, base * blend);
}""",
    "overlay": """vec4 sg_compose_overlay(vec4 backdrop, vec4 source) {
    vec3 base = sg_straight_rgb(backdrop);
    vec3 blend = sg_straight_rgb(source);
    vec3 low = 2.0 * base * blend;
    vec3 high = 1.0 - 2.0 * (1.0 - base) * (1.0 - blend);
    vec3 mixed = vec3(
        base.r <= 0.5 ? low.r : high.r,
        base.g <= 0.5 ? low.g : high.g,
        base.b <= 0.5 ? low.b : high.b
    );
    return sg_compose_blended(backdrop, source, mixed);
}""",
    "soft_light": """float sg_soft_light_channel(float base, float blend) {
    float curve = base <= 0.25
        ? ((16.0 * base - 12.0) * base + 4.0) * base
        : sqrt(base);
    return blend <= 0.5
        ? base - (1.0 - 2.0 * blend) * base * (1.0 - base)
        : base + (2.0 * blend - 1.0) * (curve - base);
}

vec4 sg_compose_soft_light(vec4 backdrop, vec4 source) {
    vec3 base = sg_straight_rgb(backdrop);
    vec3 blend = sg_straight_rgb(source);
    vec3 mixed = vec3(
        sg_soft_light_channel(base.r, blend.r),
        sg_soft_light_channel(base.g, blend.g),
        sg_soft_light_channel(base.b, blend.b)
    );
    return sg_compose_blended(backdrop, source, mixed);
}""",
    "lighten": """vec4 sg_compose_lighten(vec4 backdrop, vec4 source) {
    vec3 base = sg_straight_rgb(backdrop);
    vec3 blend = sg_straight_rgb(source);
    return sg_compose_blended(backdrop, source, max(base, blend));
}""",
    "darken": """vec4 sg_compose_darken(vec4 backdrop, vec4 source) {
    vec3 base = sg_straight_rgb(backdrop);
    vec3 blend = sg_straight_rgb(source);
    return sg_compose_blended(backdrop, source, min(base, blend));
}""",
}


def emit_blend_helper_sources(
    blend_modes: Iterable[BlendMode],
) -> tuple[str, ...]:
    """按 canonical mode 顺序返回当前 Shader 实际需要的 GLSL helper。."""
    active_modes = set(blend_modes)
    ordered_modes = tuple(
        mode
        for mode in BLEND_MODES
        if mode != DEFAULT_BLEND_MODE and mode in active_modes
    )
    if not ordered_modes:
        return ()
    return (
        _BLEND_COMMON_SOURCE,
        *(_BLEND_HELPER_SOURCES[mode] for mode in ordered_modes),
    )


__all__ = [
    "BLEND_MODES",
    "DEFAULT_BLEND_MODE",
    "BlendMode",
    "emit_blend_helper_sources",
]
