"""Node Lab 通用 capability 目录；具体能力由 Pipeline Provider 装配."""

from __future__ import annotations

from collections.abc import Iterable

from nodelab.models import CapabilityDescriptor, NodeLabError


class CapabilityRegistry:
    """按 Pipeline 隔离的 capability allowlist."""

    def __init__(self, descriptors: Iterable[CapabilityDescriptor] = ()) -> None:
        """校验 pipeline/capability_id 唯一并冻结顺序."""
        frozen = tuple(descriptors)
        by_id = {descriptor.capability_id: descriptor for descriptor in frozen}
        if len(by_id) != len(frozen):
            raise ValueError("Capability Registry 包含重复 id。")
        pipelines = {descriptor.pipeline_id for descriptor in frozen}
        if len(pipelines) > 1:
            raise ValueError("Capability Registry 一次只能装配一个 pipeline。")
        self._descriptors = frozen
        self._by_id = by_id

    @property
    def pipeline_id(self) -> str | None:
        """返回 capability 所属 pipeline；空目录返回 None."""
        if not self._descriptors:
            return None
        return self._descriptors[0].pipeline_id

    def describe_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        """返回全部 capability descriptor."""
        return self._descriptors

    def get(self, capability_id: str) -> CapabilityDescriptor:
        """读取 allowlist capability，未知 id fail closed."""
        try:
            return self._by_id[capability_id]
        except KeyError as exc:
            raise NodeLabError(
                "capability_not_found",
                "能力未由当前 Pipeline Provider 暴露。",
                stage="capability_registry",
                details={"capability_id": capability_id},
            ) from exc


__all__ = ["CapabilityRegistry"]
