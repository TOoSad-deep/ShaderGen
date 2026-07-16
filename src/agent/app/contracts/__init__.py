"""Agent 跨模块中立契约的惰性兼容入口."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.app.contracts.png_to_shader_v1 import (
        AuthorMode,
        CandidateProvenance,
        CandidateRecordInput,
        ModelCallAudit,
        RenderEvidenceBinding,
        ShaderAuthorResult,
        VisualAnalysis,
        VisualReview,
    )

__all__ = [
    "AuthorMode",
    "CandidateProvenance",
    "CandidateRecordInput",
    "ModelCallAudit",
    "RenderEvidenceBinding",
    "ShaderAuthorResult",
    "VisualAnalysis",
    "VisualReview",
]

_PNG_TO_SHADER_V1_CONTRACTS = "agent.app.contracts.png_to_shader_v1"


def __getattr__(name: str) -> Any:
    """按需解析 V1 兼容导出，避免污染其他契约域的导入路径."""
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(_PNG_TO_SHADER_V1_CONTRACTS), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """返回包含惰性公共名的模块属性列表."""
    return sorted(set(globals()) | set(__all__))
