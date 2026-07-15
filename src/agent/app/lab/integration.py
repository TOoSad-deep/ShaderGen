"""Node 与 Node Lab 之间的通用 provider 协议."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from agent.app.lab.models import (
    ArtifactDescriptor,
    ExecutionMode,
    LabRunRecord,
    NodeDescriptor,
    NodeExecutionResult,
    StepExecutionRequest,
    ensure_json_object,
)


class NodeExecutor(Protocol):
    """NodeProvider 提供的单节点执行契约."""

    def resolve_inputs(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
    ) -> dict[str, object]:
        """返回模式默认输入，显式 request.inputs 最后覆盖."""

    async def execute(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
        state: Mapping[str, object],
    ) -> NodeExecutionResult:
        """执行节点并返回 partial State，不原地修改输入."""


@runtime_checkable
class PreflightNodeExecutor(Protocol):
    """需在创建步骤证据前执行门禁的可选协议."""

    def preflight(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
    ) -> None:
        """在读取父快照或分配 step id 前 fail closed."""


class NodeExecutionHost(Protocol):
    """Provider 可使用的最小 LabRun/Artifact 能力."""

    def get_run(self, lab_run_id: str) -> LabRunRecord:
        """读取 LabRun 的 pipeline/project 绑定."""

    def upload_artifact(
        self,
        *,
        lab_run_id: str,
        kind: str,
        content_type: str,
        data: bytes,
    ) -> ArtifactDescriptor:
        """写入不透明 Artifact."""

    def read_artifact(
        self,
        lab_run_id: str,
        artifact_id: str,
    ) -> tuple[ArtifactDescriptor, bytes]:
        """读取同一 LabRun 的不透明 Artifact."""


@dataclass(frozen=True)
class NodeExecutorBinding:
    """Provider 对一个 ``(node_id, execution_mode)`` 的精确绑定."""

    node_id: str
    execution_mode: ExecutionMode
    executor: NodeExecutor


class NodeProvider(Protocol):
    """生产 Node 向通用 Harness 暴露的唯一 API."""

    @property
    def pipeline_id(self) -> str:
        """返回该 provider 的稳定 pipeline id."""

    def describe_nodes(self) -> tuple[NodeDescriptor, ...]:
        """返回 provider 管理的全部节点契约."""

    def bind(self, host: NodeExecutionHost) -> Iterable[NodeExecutorBinding]:
        """根据 Lab host 和生产依赖生成精确 Executor 绑定."""

    def source_paths(self) -> Iterable[str]:
        """返回 benchmark 应冻结的生产源文件路径."""


RouteDecider = Callable[[Mapping[str, object]], Mapping[str, Any]]


@runtime_checkable
class RouteCapabilityProvider(Protocol):
    """可选暴露纯路由 capability 的 Provider 扩展."""

    def route_deciders(self) -> Mapping[str, RouteDecider]:
        """返回 capability_id 到生产纯路由函数的映射."""


NodeCallable = Callable[
    [Mapping[str, object]],
    Mapping[str, Any] | Awaitable[Mapping[str, Any]],
]


class DirectNodeExecutor:
    """对 JSON-safe 生产 Node 的零适配直调 Executor."""

    def __init__(
        self,
        node: NodeCallable,
        *,
        execution_source: str = "production_node",
    ) -> None:
        """绑定一个只消费 State、返回 partial State 的 Node callable."""
        self._node = node
        self._execution_source = execution_source

    def resolve_inputs(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
    ) -> dict[str, object]:
        """直调 Node 不注入隐藏输入."""
        del descriptor, request
        return {}

    async def execute(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
        state: Mapping[str, object],
    ) -> NodeExecutionResult:
        """执行 callable，并对输出做统一 JSON-safe 投影."""
        del request
        result = self._node(state)
        if inspect.isawaitable(result):
            result = await result
        return NodeExecutionResult(
            output_patch=ensure_json_object(result, path="$.output_patch"),
            provenance={
                "execution_source": self._execution_source,
                "node_id": descriptor.node_id,
            },
            usage={"model_call_count": 0, "browser_launch_count": 0},
        )
