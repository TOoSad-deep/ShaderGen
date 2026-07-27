"""通过进程启动配置装配 Node Lab Application."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Protocol

from nodelab.http.settings import NodeLabServiceSettings
from nodelab.runner import NodeLabApplication


class ApplicationFactory(Protocol):
    """项目插件需要实现的最小组合根契约."""

    def __call__(self, settings: NodeLabServiceSettings) -> NodeLabApplication:
        """使用冻结服务配置创建 Application."""


def load_application(settings: NodeLabServiceSettings) -> NodeLabApplication:
    """创建空安全 Application，或加载运维配置的可信 factory."""
    if settings.application_factory is None:
        return NodeLabApplication.at_root(
            settings.root,
            pipeline_id=settings.pipeline_id,
            benchmark_workspace_root=Path.cwd(),
        )
    module_name, attribute_path = settings.application_factory.split(":", 1)
    try:
        value: object = import_module(module_name)
        for part in attribute_path.split("."):
            value = getattr(value, part)
    except (AttributeError, ImportError) as exc:
        raise RuntimeError("无法加载 NODELAB_APPLICATION_FACTORY。") from exc
    if not callable(value):
        raise RuntimeError("NODELAB_APPLICATION_FACTORY 目标不可调用。")
    application = value(settings)
    if not isinstance(application, NodeLabApplication):
        raise RuntimeError("Application factory 必须返回 NodeLabApplication。")
    return application


__all__ = ["ApplicationFactory", "load_application"]
