import json
import logging
from uuid import uuid4

import pytest

from backend.app.database.session import initialize_database_schema
from backend.app.services.agent_process_store import (
    append_agent_event,
    append_agent_log,
    complete_agent_run,
    create_agent_run,
    fail_agent_run,
    record_shader_generation_success,
)


class FakeConnection:
    def __init__(self) -> None:
        self.executed = []

    async def execute(self, query: str, *args):
        self.executed.append((query, args))
        return "OK"


class FakeAcquire:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(self, *args) -> None:
        return None


class FakePool:
    def __init__(self) -> None:
        self.connection = FakeConnection()

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.connection)


@pytest.mark.anyio
async def test_initialize_database_schema_executes_sql_files() -> None:
    pool = FakePool()

    await initialize_database_schema(pool)

    executed_sql = "\n".join(query for query, _ in pool.connection.executed)
    assert "CREATE TABLE IF NOT EXISTS agent_runs" in executed_sql
    assert "CREATE TABLE IF NOT EXISTS agent_events" in executed_sql
    assert "reasoning_content text" in executed_sql
    assert "ADD COLUMN IF NOT EXISTS reasoning_content text" in executed_sql
    assert "CREATE TABLE IF NOT EXISTS agent_logs" in executed_sql


@pytest.mark.anyio
async def test_agent_process_store_writes_run_event_and_log() -> None:
    pool = FakePool()
    run_id = uuid4()

    await create_agent_run(
        pool,
        run_id=run_id,
        input={"idea": "流光"},
        glsl_model_name="qwen-glsl",
        vision_model_name="qwen-vl",
    )
    await append_agent_event(
        pool,
        run_id=run_id,
        seq=1,
        stage="agent",
        event_type="model_call",
        payload={"latency_ms": 120},
        reasoning_content="模型调用思维链",
    )
    await append_agent_log(
        pool,
        run_id=run_id,
        event_seq=1,
        level="debug",
        source="agent.model",
        message="模型调用完成",
        context={"model": "qwen-glsl"},
    )

    statements = pool.connection.executed
    assert "INSERT INTO agent_runs" in statements[0][0]
    assert statements[0][1][:5] == (
        run_id,
        None,
        "running",
        "qwen-glsl",
        "qwen-vl",
    )
    assert json.loads(statements[0][1][5]) == {"idea": "流光"}

    assert "INSERT INTO agent_events" in statements[1][0]
    assert "reasoning_content" in statements[1][0]
    assert statements[1][1][:4] == (run_id, 1, "agent", "model_call")
    assert json.loads(statements[1][1][4]) == {"latency_ms": 120}
    assert statements[1][1][5] == "模型调用思维链"

    assert "INSERT INTO agent_logs" in statements[2][0]
    assert statements[2][1][:5] == (
        run_id,
        1,
        "debug",
        "agent.model",
        "模型调用完成",
    )
    assert json.loads(statements[2][1][5]) == {"model": "qwen-glsl"}


@pytest.mark.anyio
async def test_agent_process_store_updates_run_status() -> None:
    pool = FakePool()
    run_id = uuid4()

    await complete_agent_run(pool, run_id=run_id, result={"glsl_chars": 12})
    await fail_agent_run(pool, run_id=run_id, error="模型调用失败")

    complete_query, complete_args = pool.connection.executed[0]
    assert "UPDATE agent_runs" in complete_query
    assert complete_args[0] == run_id
    assert complete_args[1] == "succeeded"
    assert json.loads(complete_args[2]) == {"glsl_chars": 12}
    assert complete_args[3] is None

    failed_query, failed_args = pool.connection.executed[1]
    assert "UPDATE agent_runs" in failed_query
    assert failed_args[0] == run_id
    assert failed_args[1] == "failed"
    assert json.loads(failed_args[2]) == {}
    assert failed_args[3] == "模型调用失败"


@pytest.mark.anyio
async def test_agent_process_store_records_shader_success(caplog) -> None:
    pool = FakePool()
    run_id = uuid4()
    caplog.set_level(logging.INFO, logger="backend.agent_process")

    await record_shader_generation_success(
        pool,
        run_id=run_id,
        model_name="qwen-glsl",
        glsl_chars=42,
        model_calls=(
            {
                "model": "qwen-glsl",
                "glsl_chars": 42,
                "reasoning_content": "生成 GLSL 的思维链",
            },
        ),
    )

    executed_sql = "\n".join(query for query, _ in pool.connection.executed)
    first_event_query, first_event_args = pool.connection.executed[0]
    assert "INSERT INTO agent_events" in executed_sql
    assert "reasoning_content" in first_event_query
    assert json.loads(first_event_args[4]) == {"model": "qwen-glsl", "glsl_chars": 42}
    assert first_event_args[5] == "生成 GLSL 的思维链"
    assert "INSERT INTO agent_logs" in executed_sql
    assert "UPDATE agent_runs" in executed_sql
    assert "agent.process.database.write.succeeded" in caplog.text
    assert "backend.agent_process" in caplog.text
