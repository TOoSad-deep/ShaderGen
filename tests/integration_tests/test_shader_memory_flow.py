from __future__ import annotations

from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from agent.app.contracts.llm import LLMResponse
from agent.app.graphs.shader_generation_graph import build_shader_generation_graph
from agent.app.memory.store import list_project_memories
from agent.app.services.shader_generation import (
    ShaderGenerationService,
    generate_glsl_from_image,
    review_shader_render,
)


class FakeGateway:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.calls = []

    async def ainvoke(self, messages, options):
        self.calls.append((messages, options))
        text = next(self.responses)
        return LLMResponse(
            message=AIMessage(content=text),
            text=text,
            reasoning_content=None,
            model_ref=options.model_ref,
            latency_ms=1,
        )


def call_text(gateway: FakeGateway, index: int) -> str:
    message = gateway.calls[index][0][0]
    if isinstance(message.content, str):
        return message.content
    return "\n".join(str(part.get("text", "")) for part in message.content)


@pytest.mark.anyio
async def test_shader_memory_flow_is_project_scoped_and_clearable() -> None:
    shader_one = "precision mediump float; void main(){gl_FragColor=vec4(1.0);}"
    shader_two = "precision mediump float; void main(){gl_FragColor=vec4(0.8);}"
    shader_other = "precision mediump float; void main(){gl_FragColor=vec4(0.2);}"
    gateway = FakeGateway(
        [
            shader_one,
            '{"evaluation":"高光偏暗。","suggestions":["提高右上高光亮度"]}',
            shader_two,
            shader_other,
        ]
    )
    checkpointer = InMemorySaver()
    store = InMemoryStore()
    graph = build_shader_generation_graph(
        gateway,
        checkpointer=checkpointer,
        store=store,
    )
    service = ShaderGenerationService(graph, checkpointer, store, "ephemeral")
    project_id = str(uuid4())
    other_project_id = str(uuid4())

    generated = await generate_glsl_from_image(
        b"image",
        "image/png",
        project_id=project_id,
        run_id=str(uuid4()),
        service=service,
    )
    reviewed = await review_shader_render(
        b"image",
        "image/png",
        b"rendered",
        "image/png",
        generated.glsl,
        project_id=project_id,
        run_id=str(uuid4()),
        service=service,
    )
    regenerated = await generate_glsl_from_image(
        b"image",
        "image/png",
        project_id=project_id,
        run_id=str(uuid4()),
        service=service,
    )
    await generate_glsl_from_image(
        b"other",
        "image/png",
        project_id=other_project_id,
        run_id=str(uuid4()),
        service=service,
    )

    assert reviewed.memory_status == "ephemeral"
    assert regenerated.project_id == project_id
    assert "提高右上高光亮度" in call_text(gateway, 2)
    assert "提高右上高光亮度" not in call_text(gateway, 3)
    assert len(await list_project_memories(store, project_id)) == 1
    assert await list_project_memories(store, other_project_id) == ()

    snapshot = await graph.aget_state(
        {"configurable": {"thread_id": project_id}}
    )
    forbidden = {
        "image",
        "rendered_image",
        "glsl",
        "context_pack",
        "selected_memory_ids",
        "memory_status",
        "model_calls",
        "events",
        "logs",
        "run_id",
    }
    assert forbidden.isdisjoint(snapshot.values)
    assert snapshot.values["iteration"] == 2

    cleared = await service.clear_memory(project_id)
    assert cleared.deleted_memories == 1
    assert await list_project_memories(store, project_id) == ()
    cleared_snapshot = await graph.aget_state(
        {"configurable": {"thread_id": project_id}}
    )
    assert cleared_snapshot.values == {}


@pytest.mark.anyio
async def test_store_read_failure_degrades_without_losing_generation() -> None:
    class FailingReadStore(InMemoryStore):
        async def asearch(self, *args, **kwargs):
            raise RuntimeError("store read failed")

    gateway = FakeGateway(
        ["precision mediump float; void main(){gl_FragColor=vec4(1.0);}"]
    )
    checkpointer = InMemorySaver()
    store = FailingReadStore()
    graph = build_shader_generation_graph(
        gateway,
        checkpointer=checkpointer,
        store=store,
    )
    service = ShaderGenerationService(graph, checkpointer, store, "durable")

    result = await generate_glsl_from_image(
        b"image",
        "image/png",
        project_id=str(uuid4()),
        run_id=str(uuid4()),
        service=service,
    )

    assert result.glsl.startswith("precision mediump float;")
    assert result.memory_status == "degraded"
    assert any(event["event_type"] == "memory_degraded" for event in result.events)
