"""把最终 MinScene 接入非权威 ShaderGraph shadow 纵向切片."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

from shaderforge.dsl import (
    UnsupportedMinSceneFeatureError,
    adapt_min_scene_to_shader_graph,
    compile_dsl_shader,
    shape_primitive_count,
)
from shaderforge.public import MinScene
from shaderforge.rendering import (
    GraphProgramKey,
    GraphProgramRegistry,
    PlaywrightWebGL1Renderer,
    PreparedRenderResult,
)

SHADER_GRAPH_RENDERER_PATH = "compiled_graph_program_cache_v1"


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
