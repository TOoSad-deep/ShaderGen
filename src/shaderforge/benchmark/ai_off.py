"""不调用模型的确定性 Shader baseline."""

from __future__ import annotations

from shaderforge.analysis import TargetMeasurements

AI_OFF_BASELINE_VERSION = "measurement_ellipse_v1"


def _rgb(color: tuple[int, int, int]) -> tuple[float, float, float]:
    return tuple(channel / 255.0 for channel in color)  # type: ignore[return-value]


def _invert(color: tuple[float, float, float]) -> tuple[float, float, float]:
    return 1.0 - color[0], 1.0 - color[1], 1.0 - color[2]


def build_ai_off_shader(measurements: TargetMeasurements) -> str:
    """从确定性测量构造可编译的椭圆基线，用于 Renderer/Oracle smoke."""
    bbox = measurements.foreground_bbox_uv or (0.20, 0.20, 0.80, 0.80)
    center_x = (bbox[0] + bbox[2]) * 0.5
    center_y = (bbox[1] + bbox[3]) * 0.5
    half_width = max(0.02, (bbox[2] - bbox[0]) * 0.5)
    half_height = max(0.02, (bbox[3] - bbox[1]) * 0.5)
    background = _rgb(measurements.border_color_rgb)
    if measurements.palette:
        foreground = _rgb(measurements.palette[0].rgb)
    else:
        foreground = _invert(background)
    return f"""precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_image;
uniform vec2 u_resolution;
uniform float u_time;

// ai_off_baseline: {AI_OFF_BASELINE_VERSION}
void main() {{
    vec2 center = vec2({center_x:.8f}, {center_y:.8f});
    vec2 half_size = vec2({half_width:.8f}, {half_height:.8f});
    vec2 q = (v_uv - center) / half_size;
    float d = length(q) - 1.0;
    float pixel = 2.0 / max(min(u_resolution.x, u_resolution.y), 1.0);
    float aa = pixel / max(min(half_size.x, half_size.y), 0.01);
    float mask = 1.0 - smoothstep(-aa, aa, d);
    vec3 background = vec3({background[0]:.8f}, {background[1]:.8f}, {background[2]:.8f});
    vec3 foreground = vec3({foreground[0]:.8f}, {foreground[1]:.8f}, {foreground[2]:.8f});
    gl_FragColor = vec4(mix(background, foreground, mask), 1.0);
}}
"""
