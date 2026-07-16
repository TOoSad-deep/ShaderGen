"""准备 PNG-to-Shader V1 模型调用所需的历史 Context."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from langgraph.runtime import Runtime

from agent.app.context.builder import ContextPolicy, build_context_pack
from agent.app.memory.models import MemoryItem
from agent.app.memory.store import list_project_memories

PrepareContextNode = Callable[
    [Mapping[str, Any], Runtime | None], Awaitable[dict[str, Any]]
]


class ProjectMemoryReader(Protocol):
    """生产 Node 对项目 Memory 只读查询的最小公共契约."""

    async def list_project_memories(
        self,
        project_id: str,
        *,
        limit: int,
    ) -> Sequence[MemoryItem]:
        """返回已经完成 project_id 隔离的候选 Memory."""


def make_prepare_context_node(
    policy: ContextPolicy = ContextPolicy(),
    *,
    memory_reader: ProjectMemoryReader | None = None,
) -> PrepareContextNode:
    """创建读取 Store 并调用纯 Context Builder 的节点."""

    async def prepare_context(
        state: Mapping[str, Any],
        runtime: Runtime | None,
    ) -> dict[str, Any]:
        """构造本次生成或评审需要的 ContextPack."""
        events = state.get("events", ())
        status = state.get("memory_status", "ephemeral")
        memories: tuple[MemoryItem, ...] = ()
        error_type: str | None = None
        try:
            if memory_reader is not None:
                memories = tuple(
                    await memory_reader.list_project_memories(
                        state["project_id"],
                        limit=policy.max_memory_candidates,
                    )
                )
            elif runtime is not None and runtime.store is not None:
                memories = await list_project_memories(
                    runtime.store,
                    state["project_id"],
                    limit=policy.max_memory_candidates,
                )
        except Exception as exc:
            if bool(state.get("memory_strict", False)):
                raise
            status = "degraded"
            error_type = type(exc).__name__

        pack_state: Mapping[str, Any] = state
        if "last_glsl_sha256" not in state and isinstance(
            state.get("current_best_glsl_sha256"), str
        ):
            pack_state = {
                **state,
                "last_glsl_sha256": state["current_best_glsl_sha256"],
            }
        pack = build_context_pack(dict(pack_state), memories, policy)
        context_event = {
            "stage": "context",
            "event_type": "context_built",
            "payload": {
                "candidate_count": len(memories),
                "selected_count": len(pack.selected_memory_ids),
                "estimated_tokens": pack.estimated_tokens,
                "dropped_count": pack.dropped_memory_count,
            },
        }
        new_events: tuple[dict[str, Any], ...] = (context_event,)
        if error_type:
            new_events += (
                {
                    "stage": "memory",
                    "event_type": "memory_degraded",
                    "payload": {"operation": "read", "error_type": error_type},
                },
            )
        return {
            "context_pack": pack.to_dict(),
            "selected_memory_ids": pack.selected_memory_ids,
            "memory_status": status,
            "events": (*events, *new_events),
        }

    return prepare_context
