"""PostgreSQL 连接池和 SQL schema 初始化."""

from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Protocol

import asyncpg  # type: ignore[import-untyped]
from fastapi import FastAPI

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


class _DatabaseConnection(Protocol):
    """描述 schema 与健康检查使用的最小 asyncpg 连接能力."""

    async def execute(self, query: str, *args: object) -> object:
        """执行一条 SQL."""
        ...

    async def fetchval(self, query: str, *args: object) -> object:
        """读取查询结果的首个值."""
        ...


class _DatabasePool(Protocol):
    """描述 schema 与健康检查使用的最小 asyncpg pool 能力."""

    def acquire(self) -> AbstractAsyncContextManager[_DatabaseConnection]:
        """借出一条由异步上下文管理器托管的连接."""
        ...


async def open_database_pool(app: FastAPI, database_url: str | None) -> None:
    """在配置 DATABASE_URL 时创建应用数据库连接池."""
    if not database_url:
        app.state.db_pool = None
        return

    pool = await asyncpg.create_pool(
        database_url,
        min_size=1,
        max_size=5,
    )
    try:
        await initialize_database_schema(pool)
    except BaseException:
        await pool.close()
        raise
    app.state.db_pool = pool


async def initialize_database_schema(pool: _DatabasePool) -> None:
    """按文件名顺序执行后端 SQL schema."""
    async with pool.acquire() as connection:
        for schema_file in sorted(SQL_DIR.glob("*.sql")):
            await connection.execute(schema_file.read_text(encoding="utf-8"))


async def close_database_pool(app: FastAPI) -> None:
    """关闭应用数据库连接池."""
    pool = getattr(app.state, "db_pool", None)
    app.state.db_pool = None
    if pool is not None:
        await pool.close()


async def ping_database(pool: _DatabasePool) -> bool:
    """返回数据库是否能执行最小查询."""
    async with pool.acquire() as connection:
        return await connection.fetchval("SELECT 1") == 1
