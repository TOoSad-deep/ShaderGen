"""旧 MinScene 可证明子集到 ShaderDocument 的确定性迁移映射.

`polar_arc`、`edge_line`、`gaussian_lobe` 没有 V1 可证明等价表达，遇到即
fail closed；旧 shadow footprint 用独立低 Alpha 椭圆 Layer 近似，旧 radial
的 object-local 坐标用短轴近似为 Canvas radial，均不宣称像素 parity。
"""

from __future__ import annotations

import math

from shaderforge.dsl.document import (
    MIN_POSITIVE_VALUE,
    CircleShape,
    DslCanvas,
    EllipseShape,
    GlowEffect,
    Layer,
    LinearFill,
    RadialFill,
    RimEffect,
    ShaderDocument,
    SolidFill,
    Transform,
)
from shaderforge.scene import Feature, MinScene

UNSUPPORTED_MIN_SCENE_FEATURES = frozenset({"polar_arc", "edge_line", "gaussian_lobe"})


class UnsupportedMinSceneFeatureError(ValueError):
    """表示旧 MinScene feature 没有 V1 可证明的等价映射."""

    def __init__(self, features: tuple[str, ...]) -> None:
        """保存不支持的稳定 feature 类型集合."""
        self.features = features
        super().__init__("MinScene 包含 ShaderGraph V1 未映射的 feature。")


def _clamp_unit(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _rgba(
    color: tuple[float, float, float], alpha: float = 1.0
) -> tuple[float, float, float, float]:
    return (
        _clamp_unit(color[0]),
        _clamp_unit(color[1]),
        _clamp_unit(color[2]),
        _clamp_unit(alpha),
    )


def _positive(value: float) -> float:
    """把 legacy 任意正值收敛到 ShaderGraph 的最小正尺寸."""
    return max(float(value), MIN_POSITIVE_VALUE)


def _adapt_shape(scene: MinScene) -> CircleShape | EllipseShape:
    primitive = scene.object.primitive
    transform = Transform(translate=primitive.center)
    if primitive.type == "circle":
        radius = _positive(0.5 * (primitive.axes[0] + primitive.axes[1]))
        return CircleShape(
            id="legacy_body_shape",
            kind="circle",
            transform=transform,
            radius=radius,
        )
    return EllipseShape(
        id="legacy_body_shape",
        kind="ellipse",
        transform=transform,
        radii=(_positive(primitive.axes[0]), _positive(primitive.axes[1])),
    )


def _adapt_fill(scene: MinScene) -> SolidFill | LinearFill | RadialFill:
    field = scene.object.color_field
    primitive = scene.object.primitive
    center = primitive.center
    axes = primitive.axes
    if field.model == "solid":
        return SolidFill(kind="solid", color=_rgba(field.color))
    if field.model == "radial":
        # 旧 radial 使用 object-local 椭圆坐标；V1 Paint 固定为 Canvas 坐标。
        # 首个 shadow 切片用短轴换算半径，明确只用于链路验证，不宣称像素 parity。
        origin = (
            center[0] + field.origin[0] * axes[0],
            center[1] + field.origin[1] * axes[1],
        )
        return RadialFill(
            kind="radial",
            center=origin,
            radius=_positive(field.scale * min(axes)),
            inner_color=_rgba(field.inner),
            outer_color=_rgba(field.outer),
        )

    direction_length = math.hypot(*field.direction)
    direction = (
        field.direction[0] / direction_length,
        field.direction[1] / direction_length,
    )
    world_gradient = (
        direction[0] / axes[0] / field.scale,
        direction[1] / axes[1] / field.scale,
    )
    squared_length = (
        world_gradient[0] * world_gradient[0] + world_gradient[1] * world_gradient[1]
    )
    start = (
        center[0] - field.offset * world_gradient[0] / squared_length,
        center[1] - field.offset * world_gradient[1] / squared_length,
    )
    end = (
        start[0] + world_gradient[0] / squared_length,
        start[1] + world_gradient[1] / squared_length,
    )
    span = math.dist(start, end)
    if span < MIN_POSITIVE_VALUE:
        scale = MIN_POSITIVE_VALUE / span
        end = (
            start[0] + (end[0] - start[0]) * scale,
            start[1] + (end[1] - start[1]) * scale,
        )
    return LinearFill.model_validate(
        {
            "kind": "linear",
            "from": start,
            "to": end,
            "start_color": _rgba(field.start),
            "end_color": _rgba(field.end),
        }
    )


def _adapt_effect(feature: Feature) -> RimEffect | GlowEffect:
    alpha = _clamp_unit(feature.intensity)
    color = _rgba(feature.color, alpha)
    if feature.type == "rim":
        width = _positive(min(feature.axes) * 0.25)
        return RimEffect(
            kind="rim",
            width=width,
            softness=width * 0.5,
            color=color,
        )
    if feature.type == "glow":
        radius = _positive(max(feature.axes))
        return GlowEffect(
            kind="glow",
            radius=radius,
            softness=radius * 0.5,
            color=color,
        )
    raise UnsupportedMinSceneFeatureError((feature.type,))


def _adapt_shadow_layer(feature: Feature, index: int) -> Layer:
    """把旧独立椭圆 shadow footprint 映射为主体后方的独立 Layer.

    MinScene shadow 不是主体 SDF 的 offset/blur，而是带独立 center/axes 的
    Gaussian footprint；直接映射为 ShaderGraph ShadowEffect 会错误复制整个主体。
    V1 用一个低 Alpha 椭圆和窄 glow 近似其 footprint，并显式保持在主体层后方。
    """
    alpha = _clamp_unit(feature.intensity)
    radii = (
        max(feature.axes[0], MIN_POSITIVE_VALUE),
        max(feature.axes[1], MIN_POSITIVE_VALUE),
    )
    softness = max(min(radii) * 0.5, MIN_POSITIVE_VALUE)
    return Layer(
        id=f"legacy_shadow_{index}",
        shape=EllipseShape(
            id=f"legacy_shadow_shape_{index}",
            kind="ellipse",
            transform=Transform(translate=feature.center),
            radii=radii,
        ),
        fill=SolidFill(
            kind="solid",
            color=_rgba(feature.color, alpha * 0.65),
        ),
        effects=(
            GlowEffect(
                kind="glow",
                radius=softness,
                softness=softness,
                color=_rgba(feature.color, alpha * 0.35),
            ),
        ),
    )


def adapt_min_scene_to_shader_graph(scene: MinScene) -> ShaderDocument:
    """把当前 MinScene 可证明子集转换为主体层及可选 shadow footprint 层."""
    unsupported = tuple(
        sorted(
            {
                feature.type
                for feature in scene.object.features
                if feature.type in UNSUPPORTED_MIN_SCENE_FEATURES
            }
        )
    )
    if unsupported:
        raise UnsupportedMinSceneFeatureError(unsupported)
    shadow_layers = tuple(
        _adapt_shadow_layer(feature, index)
        for index, feature in enumerate(scene.object.features)
        if feature.type == "shadow"
    )
    effects = tuple(
        _adapt_effect(feature)
        for feature in scene.object.features
        if feature.type != "shadow"
    )
    return ShaderDocument(
        canvas=DslCanvas(
            width=scene.canvas.width,
            height=scene.canvas.height,
            background=_rgba(scene.canvas.background),
        ),
        layers=(
            *shadow_layers,
            Layer(
                id="legacy_body",
                shape=_adapt_shape(scene),
                fill=_adapt_fill(scene),
                effects=effects,
            ),
        ),
    )


__all__ = [
    "UNSUPPORTED_MIN_SCENE_FEATURES",
    "UnsupportedMinSceneFeatureError",
    "adapt_min_scene_to_shader_graph",
]
