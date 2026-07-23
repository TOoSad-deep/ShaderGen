"""ShaderForge 对最小骨架开放的稳定入口."""

from shaderforge.contracts.webgl1 import (
    WEBGL1_STATIC_NO_TEXTURE_V1,
    RenderContract,
)
from shaderforge.perception import MinPerception, perceive_min_target
from shaderforge.rendering import (
    CompileResult,
    PlaywrightWebGL1Renderer,
    RendererMetadata,
    RendererUnavailableError,
    RenderResult,
    build_standalone_html,
)
from shaderforge.scene import (
    CIRCLE_AXES_TOLERANCE,
    MIN_SCENE_VERSION,
    AddFeaturePatch,
    Canvas,
    ColorField,
    Feature,
    LinearColorField,
    MinScene,
    Primitive,
    RadialColorField,
    RemoveFeaturePatch,
    ReplaceColorFieldPatch,
    ReplaceFeaturePatch,
    SceneObject,
    SolidColorField,
    apply_scene_patch,
)
from shaderforge.store import ArtifactRef, LocalArtifactStore, RunArtifactStore
from shaderforge.validation import (
    ValidationResult,
    ValidationViolation,
    validate_shader,
)

__all__ = [
    "WEBGL1_STATIC_NO_TEXTURE_V1",
    "ArtifactRef",
    "CompileResult",
    "LocalArtifactStore",
    "MinPerception",
    "MinScene",
    "AddFeaturePatch",
    "CIRCLE_AXES_TOLERANCE",
    "Canvas",
    "ColorField",
    "Feature",
    "LinearColorField",
    "MIN_SCENE_VERSION",
    "PlaywrightWebGL1Renderer",
    "Primitive",
    "RenderContract",
    "RenderResult",
    "RendererMetadata",
    "RendererUnavailableError",
    "RunArtifactStore",
    "RemoveFeaturePatch",
    "ReplaceColorFieldPatch",
    "ReplaceFeaturePatch",
    "RadialColorField",
    "SceneObject",
    "SolidColorField",
    "ValidationResult",
    "ValidationViolation",
    "build_standalone_html",
    "apply_scene_patch",
    "perceive_min_target",
    "validate_shader",
]
