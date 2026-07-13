"""在显式或临时隔离数据库中运行 Shader Memory PostgreSQL 测试."""

from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def _database_url(base_url: str, database_name: str) -> str:
    """替换连接串的数据库名并保留凭据和查询参数."""
    parsed = urlsplit(base_url)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, f"/{database_name}", parsed.query, parsed.fragment)
    )


def _run_pytest(test_database_url: str) -> None:
    """把隔离数据库地址只通过子进程环境传给 pytest."""
    env = os.environ.copy()
    env["TEST_DATABASE_URL"] = test_database_url
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/integration_tests/test_shader_memory_postgres.py",
            "-q",
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )


async def _with_temporary_database(base_url: str) -> None:
    """创建临时数据库、运行测试，并在 finally 中完整删除."""
    database_name = f"shadergen_memory_test_{secrets.token_hex(6)}"
    admin = await asyncpg.connect(base_url)
    try:
        await admin.execute(f'CREATE DATABASE "{database_name}"')
        try:
            await asyncio.to_thread(
                _run_pytest,
                _database_url(base_url, database_name),
            )
        finally:
            await admin.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = $1 AND pid <> pg_backend_pid()
                """,
                database_name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
    finally:
        await admin.close()


async def _main() -> None:
    """优先使用 TEST_DATABASE_URL，否则从 DATABASE_URL 创建临时测试库."""
    load_dotenv(ROOT / ".env")
    explicit_test_url = os.getenv("TEST_DATABASE_URL")
    if explicit_test_url:
        await asyncio.to_thread(_run_pytest, explicit_test_url)
        return
    base_url = os.getenv("DATABASE_URL")
    if not base_url:
        raise SystemExit("需要 TEST_DATABASE_URL，或可创建临时数据库的 DATABASE_URL。")
    await _with_temporary_database(base_url)


if __name__ == "__main__":
    asyncio.run(_main())
