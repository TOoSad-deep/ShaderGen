"""准备 Shader 模型调用所需的历史 Context."""

from __future__ import annotations

from langgraph.runtime import Runtime

from agent.app.context.builder import ContextPolicy, build_context_pack
from agent.app.memory.store import list_project_memories
from agent.app.states.agent_state import ShaderPipelineState


def make_prepare_context_node(policy: ContextPolicy = ContextPolicy()):
    """创建读取 Store 并调用纯 Context Builder 的节点."""

    async def prepare_context(
        state: ShaderPipelineState,
        runtime: Runtime,
    ) -> ShaderPipelineState:
        """构造本次生成或评审需要的 ContextPack."""
        events = state.get("events", ())
        status = state.get("memory_status", "ephemeral")
        memories = ()
        error_type: str | None = None
        try:
            if runtime.store is not None:
                memories = await list_project_memories(
                    runtime.store,
                    state["project_id"],
                    limit=policy.max_memory_candidates,
                )
        except Exception as exc:
            status = "degraded"
            error_type = type(exc).__name__

        pack = build_context_pack(dict(state), memories, policy)
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
        new_events: tuple[dict, ...] = (context_event,)
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
