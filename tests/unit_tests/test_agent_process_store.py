import json
import logging
from uuid import uuid4

import pytest

from backend.app.database.session import initialize_database_schema
from backend.app.services.agent_process_store import (
    AgentRunOutcomeConflictError,
    append_agent_event,
    append_agent_log,
    complete_agent_run,
    create_agent_run,
    fail_agent_run,
    record_shader_generation_failure,
    record_shader_generation_success,
    start_shader_generation_run,
)


class FakeConnection:
    def __init__(self) -> None:
        self.executed = []
        self.executed_many = []
        self.persisted = []
        self._pending = []
        self.in_transaction = False
        self.transaction_entries = 0
        self.transaction_commits = 0
        self.transaction_rollbacks = 0
        self.fail_on_query: str | None = None
        self.outcome_row = {"status": "running", "result": {}, "error": None}

    def transaction(self):
        return FakeTransaction(self)

    def _persist(self, statement) -> None:
        if self.in_transaction:
            self._pending.append(statement)
        else:
            self.persisted.append(statement)

    async def fetchrow(self, query: str, *args):
        assert "FOR UPDATE" in query
        return self.outcome_row

    async def execute(self, query: str, *args):
        self.executed.append((query, args))
        if self.fail_on_query and self.fail_on_query in query:
            raise RuntimeError("simulated database failure")
        self._persist((query, args))
        return "OK"

    async def executemany(self, query: str, args):
        rows = [tuple(row) for row in args]
        self.executed_many.append((query, rows))
        self.executed.extend((query, row) for row in rows)
        if self.fail_on_query and self.fail_on_query in query:
            raise RuntimeError("simulated database failure")
        for row in rows:
            self._persist((query, row))
        return None


class FakeTransaction:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self):
        assert not self.connection.in_transaction
        self.connection.in_transaction = True
        self.connection.transaction_entries += 1
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.connection.in_transaction = False
        if exc_type is not None:
            self.connection.transaction_rollbacks += 1
            self.connection._pending.clear()
            return None
        self.connection.transaction_commits += 1
        self.connection.persisted.extend(self.connection._pending)
        self.connection._pending.clear()
        return None


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
    assert len(pool.connection.executed_many) == 2
    assert "INSERT INTO agent_events" in pool.connection.executed_many[0][0]
    assert "INSERT INTO agent_logs" in pool.connection.executed_many[1][0]
    assert pool.connection.transaction_entries == 1
    assert pool.connection.transaction_commits == 1
    assert pool.connection.transaction_rollbacks == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failing_statement",
    ("INSERT INTO agent_events", "INSERT INTO agent_logs", "UPDATE agent_runs"),
)
async def test_generation_outcome_rolls_back_events_logs_and_terminal_status(
    failing_statement: str,
) -> None:
    pool = FakePool()
    run_id = uuid4()
    pool.connection.fail_on_query = failing_statement

    with pytest.raises(RuntimeError, match="simulated database failure"):
        await record_shader_generation_success(
            pool,
            run_id=run_id,
            model_name="qwen-glsl",
            glsl_chars=42,
        )

    assert pool.connection.transaction_entries == 1
    assert pool.connection.transaction_commits == 0
    assert pool.connection.transaction_rollbacks == 1
    assert pool.connection.persisted == []


@pytest.mark.anyio
async def test_generation_outcome_retry_is_idempotent_after_same_terminal_commit(
    caplog,
) -> None:
    pool = FakePool()
    run_id = uuid4()
    pool.connection.outcome_row = {
        "status": "succeeded",
        "result": '{"glsl_chars":42}',
        "error": None,
    }
    caplog.set_level(logging.INFO, logger="backend.agent_process")

    await record_shader_generation_success(
        pool,
        run_id=run_id,
        model_name="qwen-glsl",
        glsl_chars=42,
    )

    assert pool.connection.executed_many == []
    assert pool.connection.persisted == []
    assert pool.connection.transaction_commits == 1
    assert "agent.process.database.write.idempotent" in caplog.text


@pytest.mark.anyio
async def test_generation_outcome_retry_rejects_different_terminal_state() -> None:
    pool = FakePool()
    run_id = uuid4()
    pool.connection.outcome_row = {
        "status": "failed",
        "result": {"stop_reason": "generation_failed"},
        "error": "previous safe error",
    }

    with pytest.raises(
        AgentRunOutcomeConflictError,
        match="拒绝覆盖",
    ):
        await record_shader_generation_success(
            pool,
            run_id=run_id,
            model_name="qwen-glsl",
            glsl_chars=42,
        )

    assert pool.connection.executed_many == []
    assert pool.connection.persisted == []
    assert pool.connection.transaction_commits == 0
    assert pool.connection.transaction_rollbacks == 1


@pytest.mark.anyio
async def test_agent_process_store_persists_failure_diagnostics() -> None:
    pool = FakePool()
    run_id = uuid4()

    await record_shader_generation_failure(
        pool,
        run_id=run_id,
        error=RuntimeError("PRIVATE_PROVIDER_RESPONSE"),
        stop_reason="wall_time_exhausted",
        diagnostics={
            "failure_stage": "author_initial",
            "failure_error_type": "TimeoutError",
            "candidate_count": 0,
        },
    )

    log_args = next(
        arguments
        for query, arguments in pool.connection.executed
        if "INSERT INTO agent_logs" in query
    )
    assert json.loads(log_args[5]) == {
        "error_type": "RuntimeError",
        "stop_reason": "wall_time_exhausted",
        "failure_stage": "author_initial",
        "failure_error_type": "TimeoutError",
        "candidate_count": 0,
    }
    update_args = next(
        arguments
        for query, arguments in pool.connection.executed
        if "UPDATE agent_runs" in query
    )
    assert json.loads(update_args[2]) == {
        "stop_reason": "wall_time_exhausted",
        "diagnostics": {
            "failure_stage": "author_initial",
            "failure_error_type": "TimeoutError",
            "candidate_count": 0,
        },
    }
    assert update_args[3] == "RuntimeError: wall_time_exhausted"
    serialized = "\n".join(
        str(argument)
        for _query, arguments in pool.connection.executed
        for argument in arguments
    )
    assert "PRIVATE_PROVIDER_RESPONSE" not in serialized


@pytest.mark.anyio
async def test_process_store_records_v1_request_stages_and_current_best() -> None:
    pool = FakePool()
    run_id = uuid4()
    project_id = uuid4()

    await start_shader_generation_run(
        pool,
        run_id=run_id,
        project_id=project_id,
        filename="target.png",
        content_type="image/png",
        size_bytes=128,
        glsl_model_name="author-model",
        vision_model_name="vision-model",
        generation_mode="procedural_v1",
        quality_preset="high",
        instruction="保留左上高光",
        runtime_policy={
            "schema_version": "png_to_shader_runtime_policy_v2",
            "config_sha256": "a" * 64,
            "profile": "high",
            "budget": {"max_model_calls": 12},
            "acceptance": {"quality_threshold": 0.12},
        },
    )
    await record_shader_generation_success(
        pool,
        run_id=run_id,
        model_name="author-model",
        glsl_chars=1024,
        model_calls=({"model_ref": "author-model"},),
        events=(
            {"stage": "measure_target", "event_type": "target_measured"},
            {
                "stage": "selection",
                "event_type": "current_best_updated",
                "payload": {"candidate_id": "candidate-0002", "total_loss": 0.1},
            },
            {"stage": "finalize", "event_type": "run_finalized"},
        ),
        result_summary={
            "generation_mode": "procedural_v1",
            "stop_reason": "stagnation",
            "best_candidate_id": "candidate-0002",
        },
    )

    serialized = "\n".join(
        str(argument)
        for _query, arguments in pool.connection.executed
        for argument in arguments
    )
    assert "procedural_v1" in serialized
    assert "保留左上高光" in serialized
    assert "target_measured" in serialized
    assert "current_best_updated" in serialized
    assert "candidate-0002" in serialized
    assert "png_to_shader_runtime_policy_v2" in serialized
    assert '"max_model_calls":12' in serialized
    assert "run_finalized" in serialized
