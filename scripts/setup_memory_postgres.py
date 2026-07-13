"""执行 LangGraph PostgreSQL persistence migration."""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

from backend.app.database.agent_memory import setup_agent_memory_schema


async def _main() -> None:
    """从环境变量读取数据库地址并执行 setup."""
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL 未配置，无法初始化 Agent Memory。")
    await setup_agent_memory_schema(database_url)
    sys.stdout.write("Agent Memory PostgreSQL setup completed.\n")


if __name__ == "__main__":
    asyncio.run(_main())
