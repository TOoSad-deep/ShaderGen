"""Node 与 Node Lab 之间的通用 provider 协议."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from nodelab.models import (
    ArtifactDescriptor,
    CapabilityDescriptor,
    CapabilityExecutionRequest,
    ExecutionMode,
    LabRunRecord,
    NodeDescriptor,
    NodeExecutionResult,
    StepExecutionRequest,
    ensure_json_object,
)


class AsyncResource(Protocol):
    """Benchmark 可跨 attempt 复用并异步关闭的领域资源."""

    async def close(self) -> None:
        """关闭资源."""


class CapabilityExecutor(Protocol):
    """Pipeline Provider 注入的独立 capability 执行契约."""

    async def execute_capability(
        self,
        request: CapabilityExecutionRequest,
        descriptor: CapabilityDescriptor,
        runtime: object | None = None,
    ) -> NodeExecutionResult:
        """执行 capability；runtime 由所属 Pipeline 定义."""


BenchmarkResourceFactory = Callable[[], AsyncResource]
CapabilityRuntimeFactory = Callable[[AsyncResource, bool, int], object]


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
class BenchmarkSourceProvider(Protocol):
    """显式暴露影响执行语义、应进入 benchmark fingerprint 的源码."""

    def source_paths(self) -> Iterable[str | Path]:
        """返回实现源码路径."""


def discover_implementation_source_paths(*values: object | None) -> tuple[str, ...]:
    """发现函数、类、实例及 SourceProvider 声明的本地源码文件."""
    paths: set[str] = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, (str, Path)):
            path = Path(value)
            if path.is_file():
                paths.add(str(path.resolve()))
            continue
        candidates = (value,) if inspect.isroutine(value) or inspect.isclass(value) else (
            value,
            type(value),
        )
        for candidate in candidates:
            try:
                source_file = inspect.getsourcefile(candidate)  # type: ignore[arg-type]
            except TypeError:
                source_file = None
            if source_file is not None and Path(source_file).is_file():
                paths.add(str(Path(source_file).resolve()))
        if isinstance(value, BenchmarkSourceProvider):
            paths.update(discover_implementation_source_paths(*value.source_paths()))
    return tuple(sorted(paths))


class StateReducer(Protocol):
    """把 Node partial update 应用到父 State 的可注入语义."""

    def apply(
        self,
        before: Mapping[str, object],
        update: Mapping[str, object],
    ) -> dict[str, object]:
        """返回新的完整 State，不修改输入对象."""


class ShallowStateReducer:
    """默认的顶层覆盖语义，适用于普通 dict State Node."""

    def apply(
        self,
        before: Mapping[str, object],
        update: Mapping[str, object],
    ) -> dict[str, object]:
        """以 partial update 覆盖同名顶层字段."""
        return {**before, **update}


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


CapabilityExecutorFactory = Callable[[NodeExecutionHost], CapabilityExecutor]


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


NodeReturn = Mapping[str, Any] | NodeExecutionResult | object
NodeCallable = Callable[
    [Mapping[str, object]],
    NodeReturn | Awaitable[NodeReturn],
]

NodeContextFactory = Callable[
    [NodeDescriptor, StepExecutionRequest, Mapping[str, object]],
    object,
]
ContextNodeCallable = Callable[
    [Mapping[str, object], object],
    NodeReturn | Awaitable[NodeReturn],
]
RunnableConfigFactory = Callable[[StepExecutionRequest], Mapping[str, Any] | None]


def _normalize_node_result(
    value: NodeReturn,
    *,
    descriptor: NodeDescriptor,
    execution_source: str,
) -> NodeExecutionResult:
    """归一化 Mapping、NodeExecutionResult 和 LangGraph Command-like 返回值."""
    if isinstance(value, NodeExecutionResult):
        return value
    next_action: str | None = None
    if isinstance(value, Mapping):
        update_value: object = value
    else:
        missing = object()
        update_value = getattr(value, "update", missing)
        if update_value is missing:
            raise ValueError(
                "Node 必须返回 Mapping、NodeExecutionResult 或 Command-like 对象。"
            )
        if update_value is None:
            update_value = {}
        goto = getattr(value, "goto", None)
        if isinstance(goto, str):
            next_action = goto
    if not isinstance(update_value, Mapping):
        raise ValueError("Node 必须返回 Mapping、NodeExecutionResult 或 Command-like 对象。")
    return NodeExecutionResult(
        output_patch=ensure_json_object(dict(update_value), path="$.output_patch"),
        provenance={
            "execution_source": execution_source,
            "node_id": descriptor.node_id,
        },
        usage={"model_call_count": 0, "browser_launch_count": 0},
        next_action=next_action,
    )


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

    def source_paths(self) -> tuple[str, ...]:
        """返回被包装生产 callable 的源码."""
        return discover_implementation_source_paths(self._node)

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
        return _normalize_node_result(
            result,
            descriptor=descriptor,
            execution_source=self._execution_source,
        )


class ContextNodeExecutor:
    """向不愿修改签名的 ``node(state, context)`` 注入 Pipeline 上下文."""

    def __init__(
        self,
        node: ContextNodeCallable,
        *,
        context_factory: NodeContextFactory,
        execution_source: str = "production_node_context",
    ) -> None:
        """绑定 Node 和由 Provider 定义的上下文工厂."""
        self._node = node
        self._context_factory = context_factory
        self._execution_source = execution_source

    def resolve_inputs(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
    ) -> dict[str, object]:
        """上下文不进入可持久化 State."""
        del descriptor, request
        return {}

    def source_paths(self) -> tuple[str, ...]:
        """返回生产 Node 与上下文工厂的源码."""
        return discover_implementation_source_paths(
            self._node,
            self._context_factory,
        )

    async def execute(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
        state: Mapping[str, object],
    ) -> NodeExecutionResult:
        """构造上下文并归一化 Node 返回值."""
        context = self._context_factory(descriptor, request, state)
        result = self._node(state, context)
        if inspect.isawaitable(result):
            result = await result
        return _normalize_node_result(
            result,
            descriptor=descriptor,
            execution_source=self._execution_source,
        )


class RunnableNodeExecutor:
    """直连具有 ``invoke``/``ainvoke`` 的 Runnable-like Node."""

    def __init__(
        self,
        runnable: object,
        *,
        config_factory: RunnableConfigFactory | None = None,
        execution_source: str = "production_runnable",
    ) -> None:
        """绑定 Runnable；config 仍由所属 Pipeline 构造."""
        self._runnable = runnable
        self._config_factory = config_factory
        self._execution_source = execution_source

    def resolve_inputs(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
    ) -> dict[str, object]:
        """Runnable 不注入隐藏 State 字段."""
        del descriptor, request
        return {}

    def source_paths(self) -> tuple[str, ...]:
        """返回 Runnable 实现与配置工厂的源码."""
        return discover_implementation_source_paths(
            self._runnable,
            self._config_factory,
        )

    async def execute(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
        state: Mapping[str, object],
    ) -> NodeExecutionResult:
        """优先调用 ainvoke，否则调用 invoke."""
        config = self._config_factory(request) if self._config_factory else None
        ainvoke = getattr(self._runnable, "ainvoke", None)
        if callable(ainvoke):
            result = (
                await ainvoke(dict(state), config)
                if config is not None
                else await ainvoke(dict(state))
            )
        else:
            invoke = getattr(self._runnable, "invoke", None)
            if not callable(invoke):
                raise TypeError("Runnable 必须提供 invoke 或 ainvoke。")
            result = (
                invoke(dict(state), config)
                if config is not None
                else invoke(dict(state))
            )
            if inspect.isawaitable(result):
                result = await result
        return _normalize_node_result(
            result,
            descriptor=descriptor,
            execution_source=self._execution_source,
        )
