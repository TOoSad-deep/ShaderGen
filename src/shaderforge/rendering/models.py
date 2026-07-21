"""Playwright WebGL1 渲染结果模型."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any

from shaderforge.validation.models import ValidationResult


class RendererUnavailableError(RuntimeError):
    """表示浏览器渲染 worker 在一次重放后仍不可用."""


@dataclass(frozen=True)
class CompileResult:
    """静态校验、编译、链接和 draw 阶段的诊断."""

    success: bool
    vertex_log: str
    fragment_log: str
    link_log: str
    draw_error: str | None
    static_validation: ValidationResult

    def to_dict(self) -> dict[str, Any]:
        """返回可持久化的诊断字典."""
        return {
            "success": self.success,
            "vertex_log": self.vertex_log,
            "fragment_log": self.fragment_log,
            "link_log": self.link_log,
            "draw_error": self.draw_error,
            "static_validation": self.static_validation.to_dict(),
        }


@dataclass(frozen=True)
class RendererMetadata:
    """影响像素复现的浏览器与 WebGL 环境信息."""

    renderer_version: str
    browser_version: str
    gl_version: str
    glsl_version: str
    gl_vendor: str
    gl_renderer: str
    webgl_context_kind: str
    canvas_alpha: bool
    canvas_antialias: bool
    canvas_depth: bool
    canvas_stencil: bool
    premultiplied_alpha: bool
    preserve_drawing_buffer: bool
    canvas_clear_color_rgba: tuple[float, float, float, float]

    def to_dict(self) -> dict[str, Any]:
        """返回普通字典."""
        return asdict(self)


@dataclass(frozen=True)
class RenderResult:
    """单次 WebGL1 渲染的完整、无陈旧帧结果."""

    success: bool
    image_bytes: bytes | None
    width: int
    height: int
    compile: CompileResult
    console_errors: tuple[str, ...]
    metadata: RendererMetadata | None
    duration_ms: float

    @property
    def image_sha256(self) -> str | None:
        """返回 PNG 内容哈希，失败时为 None."""
        if self.image_bytes is None:
            return None
        return sha256(self.image_bytes).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """返回不内嵌大块 PNG 的 Artifact 元数据."""
        return {
            "success": self.success,
            "width": self.width,
            "height": self.height,
            "compile": self.compile.to_dict(),
            "console_errors": list(self.console_errors),
            "metadata": self.metadata.to_dict() if self.metadata else None,
            "duration_ms": self.duration_ms,
            "image_sha256": self.image_sha256,
            "image_size_bytes": len(self.image_bytes) if self.image_bytes else 0,
        }
