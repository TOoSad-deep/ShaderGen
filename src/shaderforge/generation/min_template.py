"""由最小 scene 确定性生成 WebGL1 与 Shadertoy GLSL。."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from shaderforge.scene import MAX_MIN_FEATURES, MinScene

MIN_TEMPLATE_VERSION = "png_to_shader_min_template_v2"
WEBGL1_MIN_FRAGMENT_UNIFORM_VECTORS = 16
_RENDERER_ACTIVE_UNIFORM_VECTORS = 1  # u_resolution；u_image/u_time 只作兼容声明。

_FEATURE_KIND = {
    "rim": 1.0,
    "shadow": 2.0,
    "polar_arc": 3.0,
    "edge_line": 4.0,
}


@dataclass(frozen=True)
class UniformSpec:
    """模板接受的 typed uniform 声明。."""

    type: Literal["float", "vec2", "vec3", "vec4"]


@dataclass(frozen=True)
class MaterializedMinShader:
    """scene 物化出的固定模板、uniform 与导出版。."""

    webgl1_source: str
    shadertoy_source: str
    uniform_schema: dict[str, UniformSpec]
    uniform_values: dict[str, float | tuple[float, ...]]
    template_version: str = MIN_TEMPLATE_VERSION


def _uniforms(scene: MinScene) -> dict[str, float | tuple[float, ...]]:
    primitive = scene.object.primitive
    field = scene.object.color_field
    values: dict[str, float | tuple[float, ...]] = {
        "u_scene_bg_scale": (*scene.canvas.background, field.scale),
        "u_scene_primitive": (*primitive.center, *primitive.axes),
        "u_scene_inner_origin_x": (*field.inner, field.origin[0]),
        "u_scene_outer_origin_y": (*field.outer, field.origin[1]),
    }
    for index in range(MAX_MIN_FEATURES):
        feature = (
            scene.object.features[index]
            if index < len(scene.object.features)
            else None
        )
        prefix = f"u_feature_{index}"
        values[f"{prefix}_meta"] = (
            _FEATURE_KIND[feature.type] if feature else 0.0,
            feature.intensity if feature else 0.0,
            *(feature.center if feature else (0.0, 0.0)),
        )
        values[f"{prefix}_shape"] = (
            *(feature.axes if feature else (1.0, 1.0)),
            0.0,
            0.0,
        )
        values[f"{prefix}_color"] = (
            *(feature.color if feature else (0.0, 0.0, 0.0)),
            0.0,
        )
    return values


def _schema(values: dict[str, float | tuple[float, ...]]) -> dict[str, UniformSpec]:
    result: dict[str, UniformSpec] = {}
    for name, value in values.items():
        if isinstance(value, tuple):
            if len(value) == 2:
                result[name] = UniformSpec("vec2")
            elif len(value) == 3:
                result[name] = UniformSpec("vec3")
            elif len(value) == 4:
                result[name] = UniformSpec("vec4")
            else:
                raise ValueError(f"uniform {name} 使用了不支持的向量长度。")
        else:
            result[name] = UniformSpec("float")
    return result


_FEATURE_UNIFORM_DECLARATIONS = "\n".join(
    f"""uniform vec4 u_feature_{index}_meta;
uniform vec4 u_feature_{index}_shape;
uniform vec4 u_feature_{index}_color;"""
    for index in range(MAX_MIN_FEATURES)
)

_FEATURE_BACKGROUND_CALLS = "\n".join(
    f"""    background = applyFeatureBackground(background, p,
        u_feature_{index}_meta.x, u_feature_{index}_meta.zw,
        u_feature_{index}_shape.xy, u_feature_{index}_color.rgb,
        u_feature_{index}_meta.y);"""
    for index in range(MAX_MIN_FEATURES)
)

_FEATURE_BODY_CALLS = "\n".join(
    f"""    body = applyFeatureBody(body, p, objectDistance,
        u_feature_{index}_meta.x, u_feature_{index}_meta.zw,
        u_feature_{index}_shape.xy, u_feature_{index}_color.rgb,
        u_feature_{index}_meta.y);"""
    for index in range(MAX_MIN_FEATURES)
)

_WEBGL1_TEMPLATE_BLUEPRINT = """precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_image;
uniform vec2 u_resolution;
uniform float u_time;
uniform vec4 u_scene_bg_scale;
uniform vec4 u_scene_primitive;
uniform vec4 u_scene_inner_origin_x;
uniform vec4 u_scene_outer_origin_y;
__FEATURE_UNIFORMS__

float featureKind(float kind, float expected) {
    return 1.0 - step(0.25, abs(kind - expected));
}

vec3 applyFeatureBackground(
    vec3 background,
    vec2 p,
    float kind,
    vec2 center,
    vec2 axes,
    vec3 color,
    float intensity
) {
    vec2 safeAxes = max(abs(axes), vec2(0.02));
    float distanceToFeature = length((p - center) / safeAxes);
    float footprint = exp(-pow(distanceToFeature * 0.85, 2.0));
    float weight = featureKind(kind, 2.0) * footprint * intensity;
    return mix(background, color, clamp(weight, 0.0, 0.8));
}

vec3 applyFeatureBody(
    vec3 body,
    vec2 p,
    float objectDistance,
    float kind,
    vec2 center,
    vec2 axes,
    vec3 color,
    float intensity
) {
    vec2 safeAxes = max(abs(axes), vec2(0.02));
    vec2 featurePoint = (p - center) / safeAxes;
    float distanceToFeature = length(featurePoint);
    float footprint = exp(-pow(distanceToFeature * 0.85, 2.0));
    float edgeBand = exp(-pow((objectDistance - 0.91) * 18.181818, 2.0));
    float rimWeight = featureKind(kind, 1.0) * footprint * edgeBand;
    float arcBand = exp(-pow((distanceToFeature - 1.0) * 9.0, 2.0));
    float arcGate = smoothstep(-0.15, 0.25, featurePoint.y);
    float arcWeight = featureKind(kind, 3.0) * arcBand * arcGate;
    float lineBand = exp(-pow(featurePoint.y * 4.0, 2.0));
    float lineExtent = 1.0 - smoothstep(0.75, 1.0, abs(featurePoint.x));
    float lineWeight = featureKind(kind, 4.0) * lineBand * lineExtent;
    float weight = (rimWeight + arcWeight + lineWeight) * intensity;
    return mix(body, color, clamp(weight, 0.0, 0.9));
}

vec4 minScene(vec2 fragCoord) {
    float unit = min(u_resolution.x, u_resolution.y);
    vec2 p = (2.0 * fragCoord - u_resolution) / unit;
    vec3 backgroundColor = u_scene_bg_scale.rgb;
    float gradientScale = u_scene_bg_scale.a;
    vec2 objectCenter = u_scene_primitive.xy;
    vec2 objectAxes = u_scene_primitive.zw;
    vec3 innerColor = u_scene_inner_origin_x.rgb;
    vec3 outerColor = u_scene_outer_origin_y.rgb;
    vec2 gradientOrigin = vec2(
        u_scene_inner_origin_x.a,
        u_scene_outer_origin_y.a
    );
    vec2 safeAxes = max(objectAxes, vec2(0.02));
    vec2 q = (p - objectCenter) / safeAxes;
    float objectDistance = length(q);
    float mask = 1.0 - smoothstep(0.985, 1.015, objectDistance);

    vec2 gradientPoint = q - gradientOrigin;
    float gradient = clamp(length(gradientPoint) / max(gradientScale, 0.05), 0.0, 1.0);
    vec3 body = mix(innerColor, outerColor, smoothstep(0.0, 1.0, gradient));
    vec3 background = backgroundColor;
__FEATURE_BACKGROUND_CALLS__
__FEATURE_BODY_CALLS__
    return vec4(mix(background, body, mask), 1.0);
}

void main() {
    gl_FragColor = minScene(gl_FragCoord.xy);
}
"""

_WEBGL1_TEMPLATE = (
    _WEBGL1_TEMPLATE_BLUEPRINT.replace(
        "__FEATURE_UNIFORMS__", _FEATURE_UNIFORM_DECLARATIONS
    )
    .replace("__FEATURE_BACKGROUND_CALLS__", _FEATURE_BACKGROUND_CALLS)
    .replace("__FEATURE_BODY_CALLS__", _FEATURE_BODY_CALLS)
)


def materialize_min_shader(scene: MinScene) -> MaterializedMinShader:
    """从同一 scene 生成运行真相源和 Shadertoy 适配版。."""
    values = _uniforms(scene)
    schema = _schema(values)
    active_uniform_vectors = _RENDERER_ACTIVE_UNIFORM_VECTORS + len(schema)
    if active_uniform_vectors > WEBGL1_MIN_FRAGMENT_UNIFORM_VECTORS:
        raise RuntimeError(
            "最小模板超过 WebGL1 最低 fragment uniform vector 容量："
            f"{active_uniform_vectors}>{WEBGL1_MIN_FRAGMENT_UNIFORM_VECTORS}。"
        )
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
            literal = f"{cast(float, value):.8f}"
        else:
            vector = cast(tuple[float, ...], value)
            items = ", ".join(f"{item:.8f}" for item in vector)
            literal = f"{spec.type}({items})"
        source = source.replace(
            f"uniform {spec.type} {name};",
            f"const {spec.type} {name} = {literal};",
        )
    return source


def bake_min_uniforms(materialized: MaterializedMinShader) -> str:
    """把 typed uniform 烘焙为常量，供现有 V1 Renderer 快速贯通。."""
    return _bake_uniforms_in_source(
        materialized.webgl1_source,
        materialized.uniform_schema,
        materialized.uniform_values,
    )


__all__ = [
    "MAX_MIN_FEATURES",
    "MIN_TEMPLATE_VERSION",
    "MaterializedMinShader",
    "UniformSpec",
    "WEBGL1_MIN_FRAGMENT_UNIFORM_VECTORS",
    "bake_min_uniforms",
    "materialize_min_shader",
]
