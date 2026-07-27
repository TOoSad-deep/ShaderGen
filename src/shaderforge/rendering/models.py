"""Playwright WebGL1 渲染结果模型."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any

from shaderforge.program_spec.models import ExecutionReceipt
from shaderforge.validation.models import ValidationResult


class RendererUnavailableError(RuntimeError):
    """表示浏览器渲染 worker 在一次重放后仍不可用."""


class ShaderPreparationError(RuntimeError):
    """表示 prepared Shader 未通过编译或链接门禁."""

    def __init__(self, compile_result: CompileResult) -> None:
        """保留可诊断的编译结果."""
        self.compile_result = compile_result
        super().__init__(compile_result.draw_error or "prepared_shader_compile_failed")


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

    def to_dict(self) -> dict[str, str]:
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


@dataclass(frozen=True)
class PreparedRenderResult:
    """prepared program 的单次 uniform 绘制结果.

    ``execution_receipt`` 只在成功 draw 后由真实 renderer 路径经可信
    issuer 签发；fake/协议注入实现必须在测试内用显式 test-only issuer
    自行签发，缺失时下游 attestation 一律 fail-closed。
    """

    success: bool
    rgb_bytes: bytes | None
    image_bytes: bytes | None
    width: int
    height: int
    console_errors: tuple[str, ...]
    duration_ms: float
    draw_error: str | None = None
    execution_receipt: ExecutionReceipt | None = None

    def to_dict(self) -> dict[str, Any]:
        """返回不内嵌 RGB/PNG 大字段的诊断字典."""
        return {
            "success": self.success,
            "width": self.width,
            "height": self.height,
            "console_errors": list(self.console_errors),
            "duration_ms": self.duration_ms,
            "draw_error": self.draw_error,
            "rgb_size_bytes": len(self.rgb_bytes) if self.rgb_bytes else 0,
            "image_size_bytes": len(self.image_bytes) if self.image_bytes else 0,
            "execution_receipt": (
                self.execution_receipt.to_dict()
                if self.execution_receipt is not None
                else None
            ),
        }
