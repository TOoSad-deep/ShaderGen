"""Node Lab 共享 Harness 内核的惰性公共入口."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nodelab.capabilities import CapabilityRegistry
    from nodelab.fixtures import FixtureDefinition, FixtureRegistry
    from nodelab.integration import (
        AsyncResource,
        CapabilityExecutor,
        DirectNodeExecutor,
        NodeExecutor,
        NodeExecutorBinding,
        NodeProvider,
    )
    from nodelab.models import (
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
    from nodelab.registry import NodeRegistry
    from nodelab.runner import NodeLabApplication
    from nodelab.store import NodeLabStore
    from nodelab.suites import SuiteRegistry

__all__ = [
    "ArtifactDescriptor",
    "AsyncResource",
    "CapabilityDescriptor",
    "CapabilityExecutor",
    "CapabilityRegistry",
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
    "SuiteRegistry",
]

_EXPORT_MODULES = {
    "FixtureDefinition": "nodelab.fixtures",
    "FixtureRegistry": "nodelab.fixtures",
    "CapabilityRegistry": "nodelab.capabilities",
    "AsyncResource": "nodelab.integration",
    "CapabilityExecutor": "nodelab.integration",
    "DirectNodeExecutor": "nodelab.integration",
    "NodeExecutor": "nodelab.integration",
    "NodeExecutorBinding": "nodelab.integration",
    "NodeProvider": "nodelab.integration",
    "ArtifactDescriptor": "nodelab.models",
    "CapabilityDescriptor": "nodelab.models",
    "CapabilityExecutionRequest": "nodelab.models",
    "CapabilityExecutionResponse": "nodelab.models",
    "LabRunCreateRequest": "nodelab.models",
    "LabRunRecord": "nodelab.models",
    "NodeDescriptor": "nodelab.models",
    "StepExecutionRequest": "nodelab.models",
    "StepExecutionResponse": "nodelab.models",
    "NodeRegistry": "nodelab.registry",
    "NodeLabApplication": "nodelab.runner",
    "NodeLabStore": "nodelab.store",
    "SuiteRegistry": "nodelab.suites",
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
