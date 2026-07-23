"""由扩展 scene 确定性生成固定签名 WebGL1 与 Shadertoy GLSL。."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from shaderforge.scene import MAX_MIN_FEATURES, MinScene

MIN_TEMPLATE_VERSION = "png_to_shader_min_template_v3"
WEBGL1_MIN_FRAGMENT_UNIFORM_VECTORS = 16
MIN_TEMPLATE_FRAGMENT_UNIFORM_VECTORS = 15
_RENDERER_ACTIVE_UNIFORM_VECTORS = 1  # u_resolution

_PRIMITIVE_KIND = {"circle": 1.0, "ellipse": 2.0}
_COLOR_FIELD_KIND = {"solid": 1.0, "radial": 2.0, "linear": 3.0}
_FEATURE_KIND = {
    "rim": 1.0,
    "shadow": 2.0,
    "polar_arc": 3.0,
    "edge_line": 4.0,
    "gaussian_lobe": 5.0,
    "glow": 6.0,
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


def _field_uniforms(
    scene: MinScene,
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    float,
    float,
    float,
    float,
]:
    field = scene.object.color_field
    if field.model == "solid":
        return field.color, field.color, 0.0, 0.0, 1.0, 0.0
    if field.model == "radial":
        return (
            field.inner,
            field.outer,
            field.origin[0],
            field.origin[1],
            field.scale,
            0.0,
        )
    return (
        field.start,
        field.end,
        field.direction[0],
        field.direction[1],
        field.scale,
        field.offset,
    )


def _uniforms(scene: MinScene) -> dict[str, float | tuple[float, ...]]:
    primitive = scene.object.primitive
    color_a, color_b, param_x, param_y, scale, offset = _field_uniforms(scene)
    values: dict[str, float | tuple[float, ...]] = {
        "u_scene_bg_scale": (*scene.canvas.background, scale),
        "u_scene_primitive": (*primitive.center, *primitive.axes),
        "u_scene_color_a_param_x": (*color_a, param_x),
        "u_scene_color_b_param_y": (*color_b, param_y),
        "u_scene_meta": (
            _PRIMITIVE_KIND[primitive.type],
            _COLOR_FIELD_KIND[scene.object.color_field.model],
            offset,
            0.0,
        ),
    }
    kinds = [0.0] * MAX_MIN_FEATURES
    for index in range(MAX_MIN_FEATURES):
        feature = (
            scene.object.features[index] if index < len(scene.object.features) else None
        )
        prefix = f"u_feature_{index}"
        values[f"{prefix}_shape"] = (
            *(feature.center if feature else (0.0, 0.0)),
            *(feature.axes if feature else (1.0, 1.0)),
        )
        values[f"{prefix}_color_power"] = (
            *(feature.color if feature else (0.0, 0.0, 0.0)),
            feature.intensity if feature else 0.0,
        )
        kinds[index] = _FEATURE_KIND[feature.type] if feature else 0.0
    values["u_feature_kinds"] = tuple(kinds)
    return values


def _schema(values: dict[str, float | tuple[float, ...]]) -> dict[str, UniformSpec]:
    result: dict[str, UniformSpec] = {}
    for name, value in values.items():
        if isinstance(value, tuple):
            lengths = {2: "vec2", 3: "vec3", 4: "vec4"}
            vector_type = lengths.get(len(value))
            if vector_type is None:
                raise ValueError(f"uniform {name} 使用了不支持的向量长度。")
            result[name] = UniformSpec(
                cast(Literal["vec2", "vec3", "vec4"], vector_type)
            )
        else:
            result[name] = UniformSpec("float")
    return result


_FEATURE_UNIFORM_DECLARATIONS = "\n".join(
    f"""uniform vec4 u_feature_{index}_shape;
uniform vec4 u_feature_{index}_color_power;"""
    for index in range(MAX_MIN_FEATURES)
)

_FEATURE_BACKGROUND_CALLS = "\n".join(
    f"""    background = applyFeatureBackground(background, p, mask,
        u_feature_kinds[{index}], u_feature_{index}_shape,
        u_feature_{index}_color_power);"""
    for index in range(MAX_MIN_FEATURES)
)

_FEATURE_BODY_CALLS = "\n".join(
    f"""    body = applyFeatureBody(body, p, objectDistance, {stage:.1f},
        u_feature_kinds[{index}], u_feature_{index}_shape,
        u_feature_{index}_color_power);"""
    for stage in (1, 2, 3)
    for index in range(MAX_MIN_FEATURES)
)

_WEBGL1_TEMPLATE_BLUEPRINT = """precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_image;
uniform vec2 u_resolution;
uniform float u_time;
uniform vec4 u_scene_bg_scale;
uniform vec4 u_scene_primitive;
uniform vec4 u_scene_color_a_param_x;
uniform vec4 u_scene_color_b_param_y;
uniform vec4 u_scene_meta;
uniform vec4 u_feature_kinds;
__FEATURE_UNIFORMS__

float featureKind(float kind, float expected) {
    return 1.0 - step(0.25, abs(kind - expected));
}

vec3 applyFeatureBackground(
    vec3 background,
    vec2 p,
    float objectMask,
    float kind,
    vec4 shape,
    vec4 colorPower
) {
    vec2 safeAxes = max(abs(shape.zw), vec2(0.02));
    float distanceToFeature = length((p - shape.xy) / safeAxes);
    float footprint = exp(-pow(distanceToFeature * 0.85, 2.0));
    float shadowWeight = featureKind(kind, 2.0) * footprint * colorPower.a;
    float glowWeight = featureKind(kind, 6.0) * footprint * colorPower.a;
    float outside = 1.0 - objectMask;
    vec3 shadowed = mix(background, colorPower.rgb, clamp(shadowWeight * outside, 0.0, 0.8));
    return shadowed + colorPower.rgb * clamp(glowWeight * outside, 0.0, 1.25);
}

vec3 applyFeatureBody(
    vec3 body,
    vec2 p,
    float objectDistance,
    float stage,
    float kind,
    vec4 shape,
    vec4 colorPower
) {
    vec2 safeAxes = max(abs(shape.zw), vec2(0.02));
    vec2 featurePoint = (p - shape.xy) / safeAxes;
    float distanceToFeature = length(featurePoint);
    float footprint = exp(-pow(distanceToFeature * 0.85, 2.0));
    float edgeBand = exp(-pow((objectDistance - 0.91) * 18.181818, 2.0));
    float rimWeight = featureKind(kind, 1.0) * footprint * edgeBand;
    float arcBand = exp(-pow((distanceToFeature - 1.0) * 9.0, 2.0));
    float arcGate = smoothstep(-0.15, 0.25, featurePoint.y);
    float arcWeight = featureKind(kind, 3.0) * arcBand * arcGate * edgeBand;
    float lineBand = exp(-pow(featurePoint.y * 4.0, 2.0));
    float lineExtent = 1.0 - smoothstep(0.75, 1.0, abs(featurePoint.x));
    float lineWeight = featureKind(kind, 4.0) * lineBand * lineExtent;
    float lobeWeight = featureKind(kind, 5.0) * footprint;
    float stagedLobe = featureKind(stage, 1.0) * lobeWeight;
    float stagedRim = featureKind(stage, 2.0) * rimWeight;
    float stagedDetail = featureKind(stage, 3.0) * (arcWeight + lineWeight);
    float weight = (stagedLobe + stagedRim + stagedDetail) * colorPower.a;
    return mix(body, colorPower.rgb, clamp(weight, 0.0, 0.95));
}

vec4 minScene(vec2 fragCoord) {
    float unit = min(u_resolution.x, u_resolution.y);
    vec2 p = (2.0 * fragCoord - u_resolution) / unit;
    vec3 backgroundColor = u_scene_bg_scale.rgb;
    float fieldScale = max(u_scene_bg_scale.a, 0.05);
    vec2 objectCenter = u_scene_primitive.xy;
    vec2 objectAxes = max(abs(u_scene_primitive.zw), vec2(0.02));
    float circleRadius = 0.5 * (objectAxes.x + objectAxes.y);
    objectAxes = mix(objectAxes, vec2(circleRadius), featureKind(u_scene_meta.x, 1.0));
    vec2 q = (p - objectCenter) / objectAxes;
    float objectDistance = length(q);
    float mask = 1.0 - smoothstep(0.985, 1.015, objectDistance);

    vec3 colorA = u_scene_color_a_param_x.rgb;
    vec3 colorB = u_scene_color_b_param_y.rgb;
    vec2 fieldParameter = vec2(
        u_scene_color_a_param_x.a,
        u_scene_color_b_param_y.a
    );
    float radial = clamp(length(q - fieldParameter) / fieldScale, 0.0, 1.0);
    vec2 linearDirection = normalize(fieldParameter + vec2(1.0e-6, 0.0));
    float linear = clamp(dot(q, linearDirection) / fieldScale + u_scene_meta.z, 0.0, 1.0);
    float radialKind = featureKind(u_scene_meta.y, 2.0);
    float linearKind = featureKind(u_scene_meta.y, 3.0);
    float fieldMix = radialKind * smoothstep(0.0, 1.0, radial)
        + linearKind * smoothstep(0.0, 1.0, linear);
    vec3 body = mix(colorA, colorB, clamp(fieldMix, 0.0, 1.0));
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
    if active_uniform_vectors != MIN_TEMPLATE_FRAGMENT_UNIFORM_VECTORS:
        raise RuntimeError(
            "扩展模板 fragment uniform vector 资源计算漂移："
            f"{active_uniform_vectors}!={MIN_TEMPLATE_FRAGMENT_UNIFORM_VECTORS}。"
        )
    if active_uniform_vectors > WEBGL1_MIN_FRAGMENT_UNIFORM_VECTORS:
        raise RuntimeError(
            "扩展模板超过 WebGL1 最低 fragment uniform vector 容量："
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
    """把 typed uniform 烘焙为常量，供导出与现有验证器使用。."""
    return _bake_uniforms_in_source(
        materialized.webgl1_source,
        materialized.uniform_schema,
        materialized.uniform_values,
    )


__all__ = [
    "MAX_MIN_FEATURES",
    "MIN_TEMPLATE_FRAGMENT_UNIFORM_VECTORS",
    "MIN_TEMPLATE_VERSION",
    "MaterializedMinShader",
    "UniformSpec",
    "WEBGL1_MIN_FRAGMENT_UNIFORM_VECTORS",
    "bake_min_uniforms",
    "materialize_min_shader",
]
