from __future__ import annotations

import os
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from agent.app.contracts.llm import LLMResponse
from agent.app.graphs.shader_generation_graph import build_shader_generation_graph
from agent.app.memory.store import list_project_memories
from agent.app.services.shader_generation import (
    ShaderGenerationService,
    generate_glsl_from_image,
    review_shader_render,
)
from backend.app.database.agent_memory import setup_agent_memory_schema

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="需要隔离的 TEST_DATABASE_URL 才能运行 PostgreSQL Memory 测试。",
)


class FakeGateway:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)

    async def ainvoke(self, messages, options):
        text = next(self.responses)
        return LLMResponse(
            message=AIMessage(content=text),
            text=text,
            reasoning_content=None,
            model_ref=options.model_ref,
            latency_ms=1,
        )


def pool(database_url: str) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo=database_url,
        min_size=1,
        max_size=3,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    )


@pytest.mark.anyio
async def test_postgres_memory_survives_resource_recreation() -> None:
    assert TEST_DATABASE_URL is not None
    await setup_agent_memory_schema(TEST_DATABASE_URL)
    project_id = str(uuid4())
    shader = "precision mediump float; void main(){gl_FragColor=vec4(1.0);}"

    first_pool = pool(TEST_DATABASE_URL)
    await first_pool.open(wait=True)
    first_saver = AsyncPostgresSaver(first_pool)
    first_store = AsyncPostgresStore(first_pool)
    first_gateway = FakeGateway(
        [
            shader,
            '{"evaluation":"颜色偏暗。","suggestions":["提高亮度"]}',
        ]
    )
    first_graph = build_shader_generation_graph(
        first_gateway,
        checkpointer=first_saver,
        store=first_store,
    )
    first_service = ShaderGenerationService(
        first_graph,
        first_saver,
        first_store,
        "durable",
    )
    try:
        generated = await generate_glsl_from_image(
            b"image",
            "image/png",
            project_id=project_id,
            run_id=str(uuid4()),
            service=first_service,
        )
        await review_shader_render(
            b"image",
            "image/png",
            b"rendered",
            "image/png",
            generated.glsl,
            project_id=project_id,
            run_id=str(uuid4()),
            service=first_service,
        )
    finally:
        await first_pool.close()

    second_pool = pool(TEST_DATABASE_URL)
    await second_pool.open(wait=True)
    second_saver = AsyncPostgresSaver(second_pool)
    second_store = AsyncPostgresStore(second_pool)
    second_gateway = FakeGateway(
        ["precision mediump float; void main(){gl_FragColor=vec4(0.9);}"]
    )
    second_graph = build_shader_generation_graph(
        second_gateway,
        checkpointer=second_saver,
        store=second_store,
    )
    second_service = ShaderGenerationService(
        second_graph,
        second_saver,
        second_store,
        "durable",
    )
    try:
        memories = await list_project_memories(second_store, project_id)
        snapshot = await second_graph.aget_state(
            {"configurable": {"thread_id": project_id}}
        )
        assert len(memories) == 1
        assert "提高亮度" in memories[0].summary
        assert snapshot.values["phase"] == "reviewed"
        await second_service.clear_memory(project_id)
    finally:
        await second_pool.close()
