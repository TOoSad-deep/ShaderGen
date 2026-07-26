"""低代码构造通用 NodeProvider 的声明式 Builder."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from nodelab.integration import (
    DirectNodeExecutor,
    NodeCallable,
    NodeExecutionHost,
    NodeExecutor,
    NodeExecutorBinding,
    discover_implementation_source_paths,
)
from nodelab.models import (
    ExecutionMode,
    NodeDescriptor,
    NodeInputExample,
    ensure_json_object,
)


def _object_schema() -> dict[str, object]:
    return {"type": "object", "properties": {}, "additionalProperties": True}


def _schema(
    explicit: Mapping[str, object] | None,
    model: type[BaseModel] | None,
) -> dict[str, object]:
    if explicit is not None and model is not None:
        raise ValueError("显式 JSON Schema 与 Pydantic Model 不能同时提供。")
    if model is not None:
        return ensure_json_object(model.model_json_schema())
    return ensure_json_object(dict(explicit) if explicit is not None else _object_schema())


@dataclass(frozen=True)
class _BuiltNodeProvider:
    pipeline_id: str
    descriptors: tuple[NodeDescriptor, ...]
    bindings: tuple[NodeExecutorBinding, ...]
    paths: tuple[str, ...]

    def describe_nodes(self) -> tuple[NodeDescriptor, ...]:
        """返回 Builder 冻结的节点目录."""
        return self.descriptors

    def bind(self, host: NodeExecutionHost) -> Iterable[NodeExecutorBinding]:
        """返回冻结绑定；简单 Node 不需要使用 host."""
        del host
        return self.bindings

    def source_paths(self) -> Iterable[str]:
        """返回可用于 benchmark fingerprint 的源码路径."""
        return self.paths


class NodeProviderBuilder:
    """以最少声明把现有 callable 或 Executor 暴露给 Node Lab."""

    def __init__(self, pipeline_id: str) -> None:
        """创建单 Pipeline Builder."""
        self._pipeline_id = pipeline_id
        self._descriptors: list[NodeDescriptor] = []
        self._bindings: list[NodeExecutorBinding] = []
        self._source_paths: set[str] = set()

    def add_node(
        self,
        node: NodeCallable | None = None,
        *,
        executor: NodeExecutor | None = None,
        node_id: str | None = None,
        category: str = "general",
        summary: str | None = None,
        execution_mode: ExecutionMode = "deterministic",
        input_schema: Mapping[str, object] | None = None,
        output_schema: Mapping[str, object] | None = None,
        input_model: type[BaseModel] | None = None,
        output_model: type[BaseModel] | None = None,
        example_inputs: Mapping[str, object] | None = None,
        prerequisites: Iterable[str] = (),
        side_effects: Iterable[str] = (),
        source_ref: str | None = None,
        source_paths: Iterable[str | Path] = (),
        test_profiles: Iterable[str] = ("node_lab",),
        benchmark_profiles: Iterable[str] = ("node",),
        benchmark_metrics: Iterable[str] = ("schema_pass", "duration_ms"),
    ) -> NodeProviderBuilder:
        """登记一个 Node；callable 与显式 Executor 必须且只能提供一个."""
        if (node is None) == (executor is None):
            raise ValueError("node 与 executor 必须且只能提供一个。")
        inferred_name = getattr(node, "__name__", None) if node is not None else None
        resolved_id = node_id or inferred_name
        if not isinstance(resolved_id, str) or not resolved_id:
            raise ValueError("无法推断 node_id，请显式提供。")
        if any(item.node_id == resolved_id for item in self._descriptors):
            raise ValueError(f"NodeProviderBuilder 包含重复 node_id：{resolved_id}。")
        resolved_source = source_ref or (
            f"{getattr(node, '__module__', 'provider')}."
            f"{getattr(node, '__qualname__', resolved_id)}"
            if node is not None
            else f"provider:{self._pipeline_id}/{resolved_id}"
        )
        resolved_executor = executor or DirectNodeExecutor(node)  # type: ignore[arg-type]
        explicit_source_paths = tuple(Path(path) for path in source_paths)
        missing_source_paths = [
            str(path) for path in explicit_source_paths if not path.is_file()
        ]
        if missing_source_paths:
            raise ValueError(
                "benchmark source_paths 必须指向已有文件："
                + ", ".join(sorted(missing_source_paths))
            )
        self._source_paths.update(
            discover_implementation_source_paths(
                node,
                resolved_executor,
                *explicit_source_paths,
            )
        )
        example = ensure_json_object(dict(example_inputs or {}))
        descriptor = NodeDescriptor(
            pipeline_id=self._pipeline_id,
            node_id=resolved_id,
            category=category,
            summary=summary or f"执行生产 Node {resolved_id}。",
            prerequisites=list(prerequisites),
            side_effects=list(side_effects),
            implementation_status="available",
            execution_modes=[execution_mode],
            test_profiles=list(test_profiles),
            benchmark_profiles=list(benchmark_profiles),
            benchmark_metrics=list(benchmark_metrics),
            source_ref=resolved_source,
            input_schema=_schema(input_schema, input_model),
            output_schema=_schema(output_schema, output_model),
            input_examples=[
                NodeInputExample(
                    example_id=f"{resolved_id}-example",
                    summary=f"{resolved_id} 的自动生成调用示例。",
                    execution_mode=execution_mode,
                    inputs=example,
                )
            ],
        )
        self._descriptors.append(descriptor)
        self._bindings.append(
            NodeExecutorBinding(
                node_id=resolved_id,
                execution_mode=execution_mode,
                executor=resolved_executor,
            )
        )
        return self

    def build(self) -> _BuiltNodeProvider:
        """冻结并返回符合 NodeProvider 协议的对象."""
        return _BuiltNodeProvider(
            pipeline_id=self._pipeline_id,
            descriptors=tuple(self._descriptors),
            bindings=tuple(self._bindings),
            paths=tuple(sorted(self._source_paths)),
        )


__all__ = ["NodeProviderBuilder"]
