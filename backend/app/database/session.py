"""PostgreSQL 连接池和 SQL schema 初始化."""

import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv
from fastapi import FastAPI

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


async def open_database_pool(app: FastAPI) -> None:
    """在配置 DATABASE_URL 时创建应用数据库连接池."""
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        app.state.db_pool = None
        return

    app.state.db_pool = await asyncpg.create_pool(
        database_url,
        min_size=1,
        max_size=5,
    )
    await initialize_database_schema(app.state.db_pool)


async def initialize_database_schema(pool) -> None:
    """按文件名顺序执行后端 SQL schema."""
    async with pool.acquire() as connection:
        for schema_file in sorted(SQL_DIR.glob("*.sql")):
            await connection.execute(schema_file.read_text(encoding="utf-8"))


async def close_database_pool(app: FastAPI) -> None:
    """关闭应用数据库连接池."""
    pool = getattr(app.state, "db_pool", None)
    if pool is not None:
        await pool.close()
    app.state.db_pool = None


async def ping_database(pool) -> bool:
    """返回数据库是否能执行最小查询."""
    async with pool.acquire() as connection:
        return await connection.fetchval("SELECT 1") == 1
