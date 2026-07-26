"""Node Lab 通用节点目录，不感知任何生产 Graph 或 Node."""

from __future__ import annotations

from collections.abc import Iterable

from nodelab.models import NodeDescriptor, NodeLabError


class NodeRegistry:
    """只按 provider 提供的 descriptor 查找节点."""

    def __init__(self, descriptors: Iterable[NodeDescriptor] = ()) -> None:
        """校验 pipeline/node_id 唯一并冻结有序目录."""
        frozen = tuple(descriptors)
        by_id = {descriptor.node_id: descriptor for descriptor in frozen}
        if len(by_id) != len(frozen):
            raise ValueError("Node Registry 包含重复 node_id。")
        pipelines = {descriptor.pipeline_id for descriptor in frozen}
        if len(pipelines) > 1:
            raise ValueError("Node Registry 一次只能装配一个 pipeline。")
        self._descriptors = frozen
        self._by_id = by_id

    @property
    def pipeline_id(self) -> str | None:
        """返回 provider 的 pipeline id；空目录返回 None."""
        if not self._descriptors:
            return None
        return self._descriptors[0].pipeline_id

    def describe_nodes(self) -> tuple[NodeDescriptor, ...]:
        """按 provider 声明顺序返回全部 descriptor."""
        return self._descriptors

    def get(self, node_id: str) -> NodeDescriptor:
        """读取单个 provider 节点，未知 id 返回稳定错误."""
        try:
            return self._by_id[node_id]
        except KeyError as exc:
            raise NodeLabError(
                "node_not_found",
                "节点未由当前 NodeProvider 暴露。",
                stage="registry",
                node_id=node_id,
            ) from exc
