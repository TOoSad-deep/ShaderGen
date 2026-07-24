"""WebGL1 确定性渲染接口."""

from shaderforge.rendering.graph_program_registry import (
    GraphProgramBudgetError,
    GraphProgramKey,
    GraphProgramRegistry,
    GraphProgramRegistryClosedError,
    GraphProgramRegistryError,
    PreparedProgramProtocol,
    ProgramRendererProtocol,
)
from shaderforge.rendering.models import (
    CompileResult,
    PreparedRenderResult,
    RendererMetadata,
    RendererUnavailableError,
    RenderResult,
    ShaderPreparationError,
)
from shaderforge.rendering.webgl1_renderer import (
    PREPARED_RENDERER_PATH,
    PlaywrightWebGL1Renderer,
    PreparedWebGL1Renderer,
    build_standalone_html,
)

__all__ = [
    "CompileResult",
    "GraphProgramBudgetError",
    "GraphProgramKey",
    "GraphProgramRegistry",
    "GraphProgramRegistryClosedError",
    "GraphProgramRegistryError",
    "PREPARED_RENDERER_PATH",
    "PreparedProgramProtocol",
    "PreparedRenderResult",
    "PreparedWebGL1Renderer",
    "PlaywrightWebGL1Renderer",
    "ProgramRendererProtocol",
    "RendererMetadata",
    "RendererUnavailableError",
    "RenderResult",
    "ShaderPreparationError",
    "build_standalone_html",
]
