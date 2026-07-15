from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import START, StateGraph
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from agent.app.memory.store import (
    list_project_memories,
    upsert_validated_strategy_memory,
)
from agent.app.services.png_to_shader_v1 import PngToShaderV1Service
from agent.app.states.agent_state import PngToShaderV1State
from backend.app.database.agent_memory import setup_agent_memory_schema
from shaderforge.store import LocalArtifactStore

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="需要隔离的 TEST_DATABASE_URL 才能运行 PostgreSQL Memory 测试。",
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


def build_persistence_probe(*, checkpointer, store):
    """构造不调用模型或 Renderer 的最小 V1 checkpoint 探针图."""

    async def mark_persisted(_state: PngToShaderV1State) -> PngToShaderV1State:
        return {"phase": "persistence_verified"}

    return (
        StateGraph(PngToShaderV1State)
        .add_node("mark_persisted", mark_persisted)
        .add_edge(START, "mark_persisted")
        .compile(checkpointer=checkpointer, store=store)
    )


@pytest.mark.anyio
async def test_v1_postgres_memory_survives_recreation_and_isolates_projects(
    tmp_path: Path,
) -> None:
    assert TEST_DATABASE_URL is not None
    await setup_agent_memory_schema(TEST_DATABASE_URL)
    project_id = str(uuid4())
    other_project_id = str(uuid4())
    config = {
        "configurable": {"thread_id": PngToShaderV1Service.thread_id(project_id)}
    }
    historical_config = {"configurable": {"thread_id": project_id}}
    other_config = {
        "configurable": {
            "thread_id": PngToShaderV1Service.thread_id(other_project_id)
        }
    }

    first_pool = pool(TEST_DATABASE_URL)
    await first_pool.open(wait=True)
    first_saver = AsyncPostgresSaver(first_pool)
    first_store = AsyncPostgresStore(first_pool)
    first_graph = build_persistence_probe(
        checkpointer=first_saver,
        store=first_store,
    )
    try:
        await first_graph.ainvoke({"project_id": project_id}, config)
        await first_graph.ainvoke({"project_id": project_id}, historical_config)
        await first_graph.ainvoke(
            {"project_id": other_project_id},
            other_config,
        )
        await upsert_validated_strategy_memory(
            first_store,
            project_id=project_id,
            source_run_id=str(uuid4()),
            glsl_sha256="a" * 64,
            iteration=1,
            strategy_summary="保留已验证的主体轮廓与高光层。",
            changed_problem_domain="geometry",
            metric_version="basic_oracle_v1",
            total_loss=0.125,
        )
        await upsert_validated_strategy_memory(
            first_store,
            project_id=other_project_id,
            source_run_id=str(uuid4()),
            glsl_sha256="b" * 64,
            iteration=2,
            strategy_summary="保留另一个项目的颜色分层。",
            changed_problem_domain="color",
            metric_version="basic_oracle_v1",
            total_loss=0.25,
        )
    finally:
        await first_pool.close()

    second_pool = pool(TEST_DATABASE_URL)
    await second_pool.open(wait=True)
    second_saver = AsyncPostgresSaver(second_pool)
    second_store = AsyncPostgresStore(second_pool)
    second_graph = build_persistence_probe(
        checkpointer=second_saver,
        store=second_store,
    )
    service = PngToShaderV1Service(
        second_graph,
        second_saver,
        second_store,
        LocalArtifactStore(tmp_path / "artifacts"),
        "durable",
    )
    try:
        memories = await list_project_memories(second_store, project_id)
        other_memories = await list_project_memories(second_store, other_project_id)
        snapshot = await second_graph.aget_state(config)
        historical_snapshot = await second_graph.aget_state(historical_config)
        other_snapshot = await second_graph.aget_state(other_config)

        assert len(memories) == 1
        assert memories[0].kind == "strategy"
        assert "主体轮廓与高光层" in memories[0].summary
        assert snapshot.values["phase"] == "persistence_verified"
        assert historical_snapshot.values["phase"] == "persistence_verified"
        assert len(other_memories) == 1
        assert "另一个项目的颜色分层" in other_memories[0].summary
        assert other_snapshot.values["phase"] == "persistence_verified"

        cleared = await service.clear_memory(project_id)
        assert cleared.deleted_memories == 1
        assert await list_project_memories(second_store, project_id) == ()
        assert (await second_graph.aget_state(config)).values == {}
        assert (await second_graph.aget_state(historical_config)).values == {}

        remaining = await list_project_memories(second_store, other_project_id)
        assert len(remaining) == 1
        assert "另一个项目的颜色分层" in remaining[0].summary
        assert (await second_graph.aget_state(other_config)).values[
            "phase"
        ] == "persistence_verified"

        other_cleared = await service.clear_memory(other_project_id)
        assert other_cleared.deleted_memories == 1
    finally:
        await second_pool.close()
