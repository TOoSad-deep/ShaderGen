"""LangGraph checkpointer、Store 和 Shader service 生命周期."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal, TypeAlias, cast

from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.memory import InMemoryStore
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger("backend.agent_memory")
MemoryPool: TypeAlias = AsyncConnectionPool[AsyncConnection[DictRow]]


@dataclass
class AgentMemoryResources:
    """保存 Backend 生命周期管理的 Memory 资源."""

    checkpointer: InMemorySaver | AsyncPostgresSaver
    store: InMemoryStore | AsyncPostgresStore
    memory_status: Literal["durable", "ephemeral"]
    pool: MemoryPool | None = None


def _pool(database_url: str) -> MemoryPool:
    """创建独立于 asyncpg 过程账本的 psycopg pool."""
    return cast(
        MemoryPool,
        AsyncConnectionPool(
            conninfo=database_url,
            min_size=1,
            max_size=5,
            open=False,
            check=AsyncConnectionPool.check_connection,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
            name="shadergen-agent-memory",
        ),
    )


async def setup_agent_memory_schema(database_url: str) -> None:
    """执行 LangGraph saver/store 官方 migration，供部署步骤调用."""
    pool = _pool(database_url)
    await pool.open(wait=True)
    try:
        saver = AsyncPostgresSaver(pool)
        store = AsyncPostgresStore(pool)
        await saver.setup()
        await store.setup()
    finally:
        await pool.close()


async def _verify_schema(
    saver: AsyncPostgresSaver,
    store: AsyncPostgresStore,
) -> None:
    """确认运行时所需表已由独立 migration 创建."""
    await saver.aget_tuple({"configurable": {"thread_id": "__healthcheck__"}})
    await store.asearch(
        ("shadergen", "v1", "__healthcheck__", "memory"),
        limit=1,
    )


async def open_agent_memory(
    app: FastAPI,
    database_url: str | None,
) -> AgentMemoryResources:
    """创建临时或 PostgreSQL Memory 资源并返回中立 persistence 资源."""
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    if not database_url:
        saver = InMemorySaver()
        store = InMemoryStore()
        resources = AgentMemoryResources(
            checkpointer=saver,
            store=store,
            memory_status="ephemeral",
        )
        app.state.agent_memory = resources
        logger.warning("agent.memory.ephemeral database_url_missing=true")
        return resources

    pool = _pool(database_url)
    try:
        await pool.open(wait=True)
        postgres_saver = AsyncPostgresSaver(pool)
        postgres_store = AsyncPostgresStore(pool)
        await _verify_schema(postgres_saver, postgres_store)
    except BaseException:
        await pool.close()
        logger.exception("agent.memory.startup.failed")
        raise

    resources = AgentMemoryResources(
        checkpointer=postgres_saver,
        store=postgres_store,
        memory_status="durable",
        pool=pool,
    )
    app.state.agent_memory = resources
    logger.info("agent.memory.started status=durable")
    return resources


async def close_agent_memory(app: FastAPI) -> None:
    """关闭 Agent Memory psycopg pool 并清空 app state."""
    resources = getattr(app.state, "agent_memory", None)
    app.state.agent_memory = None
    if resources is not None and resources.pool is not None:
        await resources.pool.close()
