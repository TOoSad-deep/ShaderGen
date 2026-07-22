"""Node Lab 独立 HTTP 服务包的惰性公共入口."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["NodeLabHttpService", "NodeLabServiceSettings", "create_app"]

_EXPORT_MODULES = {
    "NodeLabHttpService": "nodelab_service.service",
    "NodeLabServiceSettings": "nodelab_service.settings",
    "create_app": "nodelab_service.main",
}


def __getattr__(name: str) -> Any:
    """按所属模块解析公共名，避免导入 Schema 时创建服务."""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """返回包含惰性公共名的模块属性列表."""
    return sorted(set(globals()) | set(__all__))
