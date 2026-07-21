"""由最小 scene 确定性生成 WebGL1 与 Shadertoy GLSL。."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from shaderforge.scene import Feature, MinScene

MIN_TEMPLATE_VERSION = "png_to_shader_min_template_v1"


@dataclass(frozen=True)
class UniformSpec:
    """模板接受的 typed uniform 声明。."""

    type: Literal["float", "vec2", "vec3"]


@dataclass(frozen=True)
class MaterializedMinShader:
    """scene 物化出的固定模板、uniform 与导出版。."""

    webgl1_source: str
    shadertoy_source: str
    uniform_schema: dict[str, UniformSpec]
    uniform_values: dict[str, float | tuple[float, ...]]
    template_version: str = MIN_TEMPLATE_VERSION


def _feature(scene: MinScene, feature_type: str) -> Feature | None:
    return next(
        (item for item in scene.object.features if item.type == feature_type), None
    )


def _uniforms(scene: MinScene) -> dict[str, float | tuple[float, ...]]:
    primitive = scene.object.primitive
    field = scene.object.color_field
    rim = _feature(scene, "rim")
    shadow = _feature(scene, "shadow")
    return {
        "u_bg": scene.canvas.background,
        "u_center": primitive.center,
        "u_axes": primitive.axes,
        "u_inner": field.inner,
        "u_outer": field.outer,
        "u_gradient_origin": field.origin,
        "u_gradient_scale": field.scale,
        "u_rim_color": rim.color if rim else field.inner,
        "u_rim_intensity": rim.intensity if rim else 0.0,
        "u_shadow_center": shadow.center if shadow else (0.0, -2.0),
        "u_shadow_axes": shadow.axes if shadow else (0.1, 0.1),
        "u_shadow_color": shadow.color if shadow else scene.canvas.background,
        "u_shadow_intensity": shadow.intensity if shadow else 0.0,
    }


def _schema(values: dict[str, float | tuple[float, ...]]) -> dict[str, UniformSpec]:
    result: dict[str, UniformSpec] = {}
    for name, value in values.items():
        if isinstance(value, tuple):
            result[name] = UniformSpec("vec2" if len(value) == 2 else "vec3")
        else:
            result[name] = UniformSpec("float")
    return result


_WEBGL1_TEMPLATE = """precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_image;
uniform vec2 u_resolution;
uniform float u_time;
uniform vec3 u_bg;
uniform vec2 u_center;
uniform vec2 u_axes;
uniform vec3 u_inner;
uniform vec3 u_outer;
uniform vec2 u_gradient_origin;
uniform float u_gradient_scale;
uniform vec3 u_rim_color;
uniform float u_rim_intensity;
uniform vec2 u_shadow_center;
uniform vec2 u_shadow_axes;
uniform vec3 u_shadow_color;
uniform float u_shadow_intensity;

vec4 minScene(vec2 fragCoord) {
    float unit = min(u_resolution.x, u_resolution.y);
    vec2 p = (2.0 * fragCoord - u_resolution) / unit;
    vec2 safeAxes = max(u_axes, vec2(0.02));
    vec2 q = (p - u_center) / safeAxes;
    float objectDistance = length(q);
    float mask = 1.0 - smoothstep(0.985, 1.015, objectDistance);

    vec2 gradientPoint = q - u_gradient_origin;
    float gradient = clamp(length(gradientPoint) / max(u_gradient_scale, 0.05), 0.0, 1.0);
    vec3 body = mix(u_inner, u_outer, smoothstep(0.0, 1.0, gradient));
    float rim = exp(-pow((objectDistance - 0.91) * 18.181818, 2.0));
    body = mix(body, u_rim_color, clamp(rim * u_rim_intensity, 0.0, 0.85));

    vec2 shadowAxes = max(u_shadow_axes, vec2(0.02));
    float shadowDistance = length((p - u_shadow_center) / shadowAxes);
    float shadow = (1.0 - smoothstep(0.45, 1.25, shadowDistance)) * u_shadow_intensity;
    vec3 background = mix(u_bg, u_shadow_color, clamp(shadow, 0.0, 0.75));
    return vec4(mix(background, body, mask), 1.0);
}

void main() {
    gl_FragColor = minScene(gl_FragCoord.xy);
}
"""


def materialize_min_shader(scene: MinScene) -> MaterializedMinShader:
    """从同一 scene 生成运行真相源和 Shadertoy 适配版。."""
    values = _uniforms(scene)
    schema = _schema(values)
    shadertoy = _WEBGL1_TEMPLATE.replace("varying vec2 v_uv;\n", "")
    shadertoy = shadertoy.replace("uniform sampler2D u_image;\n", "")
    shadertoy = shadertoy.replace(
        "uniform vec2 u_resolution;", "#define u_resolution iResolution.xy"
    )
    shadertoy = shadertoy.replace("uniform float u_time;\n", "")
    shadertoy = shadertoy.replace(
        "void main() {\n    gl_FragColor = minScene(gl_FragCoord.xy);\n}",
        "void mainImage(out vec4 fragColor, in vec2 fragCoord) {\n"
        "    fragColor = minScene(fragCoord);\n}",
    )
    shadertoy = _bake_uniforms_in_source(shadertoy, schema, values)
    return MaterializedMinShader(
        webgl1_source=_WEBGL1_TEMPLATE,
        shadertoy_source=shadertoy,
        uniform_schema=schema,
        uniform_values=values,
    )


def _bake_uniforms_in_source(
    source: str,
    schema: dict[str, UniformSpec],
    values: dict[str, float | tuple[float, ...]],
) -> str:
    for name, spec in schema.items():
        value = values[name]
        if spec.type == "float":
            literal = f"{float(value):.8f}"
        else:
            items = ", ".join(f"{float(item):.8f}" for item in value)  # type: ignore[arg-type]
            literal = f"{spec.type}({items})"
        source = source.replace(f"uniform {spec.type} {name};", f"const {spec.type} {name} = {literal};")
    return source


def bake_min_uniforms(materialized: MaterializedMinShader) -> str:
    """把 typed uniform 烘焙为常量，供现有 V1 Renderer 快速贯通。."""
    return _bake_uniforms_in_source(
        materialized.webgl1_source,
        materialized.uniform_schema,
        materialized.uniform_values,
    )


__all__ = [
    "MIN_TEMPLATE_VERSION",
    "MaterializedMinShader",
    "UniformSpec",
    "bake_min_uniforms",
    "materialize_min_shader",
]
