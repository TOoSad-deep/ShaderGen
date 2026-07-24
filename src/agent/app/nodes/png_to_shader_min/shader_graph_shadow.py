"""把最终 MinScene 接入非权威 ShaderGraph shadow 纵向切片."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

from shaderforge.dsl import (
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
    compile_dsl_shader,
    shape_primitive_count,
)
from shaderforge.public import Feature, MinScene
from shaderforge.rendering import (
    GraphProgramKey,
    GraphProgramRegistry,
    PlaywrightWebGL1Renderer,
    PreparedRenderResult,
)

SHADER_GRAPH_RENDERER_PATH = "compiled_graph_program_cache_v1"
_UNSUPPORTED_FEATURES = frozenset({"polar_arc", "edge_line", "gaussian_lobe"})


class _DrawablePrepared(Protocol):
    async def render_uniforms(
        self,
        values: dict[str, tuple[float, float, float, float]],
        *,
        capture_png: bool = False,
    ) -> PreparedRenderResult:
        """绘制一帧完整 uniform 值."""


class _ClosableRenderer(Protocol):
    async def close(self) -> None:
        """释放 Renderer 资源."""


GraphRendererFactory = Callable[[], PlaywrightWebGL1Renderer]


@dataclass(frozen=True)
class ShaderGraphShadowResult:
    """不参与 current_best 的 ShaderGraph 编译和实渲染结果."""

    summary: dict[str, Any]
    fragment_source: str | None = None
    image_bytes: bytes | None = None


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


def _adapt_shape(scene: MinScene) -> CircleShape | EllipseShape:
    primitive = scene.object.primitive
    transform = Transform(translate=primitive.center)
    if primitive.type == "circle":
        radius = 0.5 * (primitive.axes[0] + primitive.axes[1])
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
        radii=primitive.axes,
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
            radius=max(field.scale * min(axes), 1.0e-6),
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
        width = max(min(feature.axes) * 0.25, 1.0e-6)
        return RimEffect(
            kind="rim",
            width=width,
            softness=width * 0.5,
            color=color,
        )
    if feature.type == "glow":
        radius = max(max(feature.axes), 1.0e-6)
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
                if feature.type in _UNSUPPORTED_FEATURES
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


class ShaderGraphShadowRunner:
    """编译并真实渲染一次 ShaderGraph，但不参与产品候选选择."""

    def __init__(
        self,
        renderer_factory: GraphRendererFactory = PlaywrightWebGL1Renderer,
    ) -> None:
        """保存惰性 Renderer 工厂，构造阶段不启动浏览器."""
        self._renderer_factory = renderer_factory

    async def run(self, scene: MinScene) -> ShaderGraphShadowResult:
        """执行 fail-open shadow；失败只返回安全摘要."""
        try:
            document = adapt_min_scene_to_shader_graph(scene)
        except UnsupportedMinSceneFeatureError as exc:
            return ShaderGraphShadowResult(
                {
                    "status": "unsupported",
                    "layer_count": 0,
                    "primitive_count": 0,
                    "compile_count": 0,
                    "cache_hit_count": 0,
                    "cache_size": 0,
                    "unsupported_features": list(exc.features),
                    "error_code": "unsupported_min_scene_feature",
                }
            )

        document_json = document.model_dump(mode="json", by_alias=True)
        compiled = None
        registry = None
        renderer: _ClosableRenderer | None = None
        image_bytes: bytes | None = None
        error_code: str | None = None
        render_duration_ms: float | None = None
        registry_summary: dict[str, int] | None = None
        try:
            compiled = compile_dsl_shader(document)
            renderer = self._renderer_factory()
            registry = GraphProgramRegistry(renderer, max_programs=2, max_compiles=4)
            key = GraphProgramKey(
                compiler_version=compiled.compiler_version,
                topology_sha256=compiled.topology_sha256,
                active_parameter_manifest_sha256=(compiled.parameter_manifest_sha256),
                baked_parameter_sha256=compiled.glsl_sha256,
                width=scene.canvas.width,
                height=scene.canvas.height,
            )
            prepared = cast(
                _DrawablePrepared,
                await registry.get_or_prepare(
                    key,
                    compiled.fragment_source,
                    compiled.uniform_schema,
                ),
            )
            rendered = await prepared.render_uniforms(
                compiled.uniform_values,
                capture_png=True,
            )
            render_duration_ms = float(rendered.duration_ms)
            if not rendered.success or rendered.image_bytes is None:
                error_code = "shadow_render_failed"
            else:
                image_bytes = rendered.image_bytes
        except Exception:
            error_code = (
                "shadow_compile_failed"
                if compiled is None
                else "shadow_renderer_failed"
            )
        finally:
            close_failed = False
            if registry is not None:
                registry_summary = registry.summary()
                try:
                    await registry.close_all()
                except Exception:
                    close_failed = True
            if renderer is not None:
                try:
                    await renderer.close()
                except Exception:
                    close_failed = True
            if close_failed:
                error_code = error_code or "shadow_resource_close_failed"

        if registry_summary is None:
            registry_summary = {
                "compile_count": 0,
                "cache_hit_count": 0,
                "cache_size": 0,
            }
        resource_summary = (
            compiled.resource_summary.to_dict() if compiled is not None else {}
        )
        summary: dict[str, Any] = {
            "status": "rendered" if error_code is None else "failed",
            "renderer_path": SHADER_GRAPH_RENDERER_PATH,
            "dsl_schema_version": (
                compiled.dsl_schema_version if compiled is not None else None
            ),
            "compiler_version": (
                compiled.compiler_version if compiled is not None else None
            ),
            "document_sha256": (
                compiled.document_sha256 if compiled is not None else None
            ),
            "topology_sha256": (
                compiled.topology_sha256 if compiled is not None else None
            ),
            "layer_count": len(document.layers),
            "primitive_count": sum(
                shape_primitive_count(layer.shape) for layer in document.layers
            ),
            "compile_count": registry_summary["compile_count"],
            "cache_hit_count": registry_summary["cache_hit_count"],
            "cache_size": registry_summary["cache_size"],
            "render_duration_ms": render_duration_ms,
            "unsupported_features": [],
            "error_code": error_code,
            "resource_summary": resource_summary,
            "shader_graph": document_json,
        }
        return ShaderGraphShadowResult(
            summary=summary,
            fragment_source=(
                compiled.fragment_source if compiled is not None else None
            ),
            image_bytes=image_bytes,
        )


__all__ = [
    "SHADER_GRAPH_RENDERER_PATH",
    "ShaderGraphShadowResult",
    "ShaderGraphShadowRunner",
    "UnsupportedMinSceneFeatureError",
    "adapt_min_scene_to_shader_graph",
]
