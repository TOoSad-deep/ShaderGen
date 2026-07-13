"""把结构化 Shader Review 晋升为项目长期记忆."""

from __future__ import annotations

from langgraph.runtime import Runtime

from agent.app.memory.store import upsert_review_memory
from agent.app.states.agent_state import ShaderPipelineState


async def promote_memory(
    state: ShaderPipelineState,
    runtime: Runtime,
) -> ShaderPipelineState:
    """幂等 upsert 当前 Review Memory，并安全降级写入失败."""
    events = state.get("events", ())
    if runtime.store is None:
        return {
            "memory_status": "degraded",
            "events": (
                *events,
                {
                    "stage": "memory",
                    "event_type": "memory_degraded",
                    "payload": {"operation": "write", "error_type": "StoreMissing"},
                },
            ),
        }

    try:
        item = await upsert_review_memory(
            runtime.store,
            project_id=state["project_id"],
            source_run_id=state["run_id"],
            glsl_sha256=state["last_glsl_sha256"],
            iteration=state.get("iteration"),
            evaluation=state["evaluation"],
            suggestions=state.get("suggestions", ()),
        )
    except Exception as exc:
        return {
            "memory_status": "degraded",
            "events": (
                *events,
                {
                    "stage": "memory",
                    "event_type": "memory_degraded",
                    "payload": {
                        "operation": "write",
                        "error_type": type(exc).__name__,
                    },
                },
            ),
        }

    return {
        "events": (
            *events,
            {
                "stage": "memory",
                "event_type": "memory_promoted",
                "payload": {
                    "memory_id": item.memory_id,
                    "kind": item.kind,
                    "source_run_id": item.source_run_id,
                    "glsl_sha256": item.glsl_sha256,
                },
            },
        )
    }
