"""ShaderForge 最小骨架的惰性公共入口."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "MinPerception",
    "MinScene",
    "PlaywrightWebGL1Renderer",
    "RenderContract",
    "perceive_min_target",
]

_EXPORT_MODULES = {
    "RenderContract": "shaderforge.contracts",
    "MinPerception": "shaderforge.perception",
    "perceive_min_target": "shaderforge.perception",
    "MinScene": "shaderforge.scene",
    "PlaywrightWebGL1Renderer": "shaderforge.rendering",
}


def __getattr__(name: str) -> Any:
    """按所属 typed 子包惰性解析最小骨架导出."""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """返回包含惰性公共名的模块属性列表."""
    return sorted(set(globals()) | set(__all__))
