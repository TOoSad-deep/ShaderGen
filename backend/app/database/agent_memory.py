"""LangGraph checkpointer、Store 和 Shader service 生命周期."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv
from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.memory import InMemoryStore
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from agent.app.services.png_to_shader_v1 import (
    PngToShaderV1Service,
    create_png_to_shader_v1_service,
)
from agent.app.services.shader_generation import (
    ShaderGenerationService,
    create_shader_generation_service,
)

logger = logging.getLogger("backend.agent_memory")


@dataclass
class AgentMemoryResources:
    """保存 Backend 生命周期管理的 Memory 资源."""

    service: ShaderGenerationService
    png_to_shader_v1_service: PngToShaderV1Service
    pool: AsyncConnectionPool | None = None


def _pool(database_url: str) -> AsyncConnectionPool:
    """创建独立于 asyncpg 过程账本的 psycopg pool."""
    return AsyncConnectionPool(
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


async def open_agent_memory(app: FastAPI) -> None:
    """创建临时或 PostgreSQL Memory 资源并注入 Shader service."""
    load_dotenv()
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        saver = InMemorySaver()
        store = InMemoryStore()
        service = create_shader_generation_service(
            checkpointer=saver,
            store=store,
            memory_status="ephemeral",
        )
        png_to_shader_v1_service = create_png_to_shader_v1_service(
            checkpointer=saver,
            store=store,
            memory_status="ephemeral",
        )
        app.state.agent_memory = AgentMemoryResources(
            service=service,
            png_to_shader_v1_service=png_to_shader_v1_service,
        )
        app.state.shader_service = service
        app.state.png_to_shader_v1_service = png_to_shader_v1_service
        logger.warning("agent.memory.ephemeral database_url_missing=true")
        return

    pool = _pool(database_url)
    await pool.open(wait=True)
    try:
        saver = AsyncPostgresSaver(pool)
        store = AsyncPostgresStore(pool)
        await _verify_schema(saver, store)
        service = create_shader_generation_service(
            checkpointer=saver,
            store=store,
            memory_status="durable",
        )
        png_to_shader_v1_service = create_png_to_shader_v1_service(
            checkpointer=saver,
            store=store,
            memory_status="durable",
        )
    except Exception:
        await pool.close()
        logger.exception("agent.memory.startup.failed")
        raise

    app.state.agent_memory = AgentMemoryResources(
        service=service,
        png_to_shader_v1_service=png_to_shader_v1_service,
        pool=pool,
    )
    app.state.shader_service = service
    app.state.png_to_shader_v1_service = png_to_shader_v1_service
    logger.info("agent.memory.started status=durable")


async def close_agent_memory(app: FastAPI) -> None:
    """关闭 Agent Memory psycopg pool 并清空 app state."""
    resources = getattr(app.state, "agent_memory", None)
    if resources is not None and resources.pool is not None:
        await resources.pool.close()
    app.state.agent_memory = None
    app.state.shader_service = None
    app.state.png_to_shader_v1_service = None
