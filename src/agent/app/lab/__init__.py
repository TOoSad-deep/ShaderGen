"""Node Lab 共享 Harness 内核的惰性公共入口."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.app.lab.fixtures import FixtureDefinition, FixtureRegistry
    from agent.app.lab.integration import (
        DirectNodeExecutor,
        NodeExecutor,
        NodeExecutorBinding,
        NodeProvider,
    )
    from agent.app.lab.models import (
        ArtifactDescriptor,
        CapabilityDescriptor,
        CapabilityExecutionRequest,
        CapabilityExecutionResponse,
        LabRunCreateRequest,
        LabRunRecord,
        NodeDescriptor,
        StepExecutionRequest,
        StepExecutionResponse,
    )
    from agent.app.lab.registry import NodeRegistry
    from agent.app.lab.runner import NodeLabApplication
    from agent.app.lab.store import NodeLabStore

__all__ = [
    "ArtifactDescriptor",
    "CapabilityDescriptor",
    "CapabilityExecutionRequest",
    "CapabilityExecutionResponse",
    "FixtureDefinition",
    "FixtureRegistry",
    "LabRunCreateRequest",
    "LabRunRecord",
    "NodeDescriptor",
    "NodeExecutor",
    "NodeExecutorBinding",
    "NodeLabApplication",
    "NodeProvider",
    "NodeLabStore",
    "NodeRegistry",
    "DirectNodeExecutor",
    "StepExecutionRequest",
    "StepExecutionResponse",
]

_EXPORT_MODULES = {
    "FixtureDefinition": "agent.app.lab.fixtures",
    "FixtureRegistry": "agent.app.lab.fixtures",
    "DirectNodeExecutor": "agent.app.lab.integration",
    "NodeExecutor": "agent.app.lab.integration",
    "NodeExecutorBinding": "agent.app.lab.integration",
    "NodeProvider": "agent.app.lab.integration",
    "ArtifactDescriptor": "agent.app.lab.models",
    "CapabilityDescriptor": "agent.app.lab.models",
    "CapabilityExecutionRequest": "agent.app.lab.models",
    "CapabilityExecutionResponse": "agent.app.lab.models",
    "LabRunCreateRequest": "agent.app.lab.models",
    "LabRunRecord": "agent.app.lab.models",
    "NodeDescriptor": "agent.app.lab.models",
    "StepExecutionRequest": "agent.app.lab.models",
    "StepExecutionResponse": "agent.app.lab.models",
    "NodeRegistry": "agent.app.lab.registry",
    "NodeLabApplication": "agent.app.lab.runner",
    "NodeLabStore": "agent.app.lab.store",
}


def __getattr__(name: str) -> Any:
    """按所属 Harness 模块惰性解析公共导出."""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """返回包含惰性公共名的模块属性列表."""
    return sorted(set(globals()) | set(__all__))
