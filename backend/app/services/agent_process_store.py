"""Agent 过程数据写入服务."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol, cast
from uuid import UUID

logger = logging.getLogger("backend.agent_process")

_EVENT_INSERT_SQL = """
    INSERT INTO agent_events (
        run_id,
        seq,
        stage,
        event_type,
        payload,
        reasoning_content
    )
    VALUES ($1, $2, $3, $4, $5::jsonb, $6)
"""
_LOG_INSERT_SQL = """
    INSERT INTO agent_logs (
        run_id,
        event_seq,
        level,
        source,
        message,
        context
    )
    VALUES ($1, $2, $3, $4, $5, $6::jsonb)
"""
_RUN_UPDATE_SQL = """
    UPDATE agent_runs
    SET
        status = $2,
        result = $3::jsonb,
        error = $4,
        finished_at = now()
    WHERE id = $1
"""
_RUN_OUTCOME_LOCK_SQL = """
    SELECT status, result, error
    FROM agent_runs
    WHERE id = $1
    FOR UPDATE
"""


class AgentRunOutcomeConflictError(RuntimeError):
    """表示同一 run_id 已经存在不同终态，禁止静默覆盖."""


class _DatabaseConnection(Protocol):
    """描述过程账本实际使用的最小异步连接能力."""

    async def execute(self, query: str, *args: Any) -> Any:
        """执行一条参数化 SQL."""
        ...


class _DatabasePool(Protocol):
    """描述过程账本实际使用的最小异步连接池能力."""

    def acquire(self) -> AbstractAsyncContextManager[_DatabaseConnection]:
        """借出一条由异步上下文管理器托管的连接."""
        ...


def _jsonb(value: dict[str, Any] | None) -> str:
    """把 Python 字典转成稳定 JSON 字符串，交给 SQL 转 jsonb."""
    return json.dumps(value or {}, ensure_ascii=False, separators=(",", ":"))


def _decode_jsonb(value: Any) -> Any:
    """兼容 asyncpg 默认把 jsonb 解码为字符串的行为."""
    if isinstance(value, str):
        return json.loads(value)
    return value


def _pop_reasoning_content(payload: dict[str, Any]) -> str | None:
    """从事件 payload 中取出需要单独入列保存的思维链."""
    reasoning_content = payload.pop("reasoning_content", None)
    if not reasoning_content:
        return None
    return str(reasoning_content)


def _safe_error_summary(error: Exception, stop_reason: str) -> str:
    """生成不含供应商原文、GLSL 或用户输入的持久化错误摘要."""
    return f"{type(error).__name__}: {stop_reason}"


async def _execute_many(
    connection: _DatabaseConnection,
    query: str,
    rows: list[tuple[Any, ...]],
) -> None:
    """生产连接使用批量协议；简单测试替身回退为逐条 execute."""
    if not rows:
        return
    executemany = getattr(connection, "executemany", None)
    if executemany is not None:
        await executemany(query, rows)
        return
    for row in rows:
        await connection.execute(query, *row)


@asynccontextmanager
async def _outcome_transaction(
    connection: _DatabaseConnection,
) -> AsyncIterator[None]:
    """生产 asyncpg 使用显式事务；仅兼容缺少 transaction 的简单测试替身."""
    transaction = getattr(connection, "transaction", None)
    if transaction is None:
        yield
        return
    async with transaction():
        yield


async def _locked_existing_outcome(
    connection: _DatabaseConnection,
    run_id: UUID,
) -> Mapping[str, Any]:
    """锁定 run 终态；简单测试替身没有 fetchrow 时视为 running."""
    fetchrow = getattr(connection, "fetchrow", None)
    if fetchrow is None:
        return {"status": "running", "result": {}, "error": None}
    existing = await fetchrow(_RUN_OUTCOME_LOCK_SQL, run_id)
    if existing is None:
        raise AgentRunOutcomeConflictError("Agent run 不存在，无法写入终态。")
    return cast(Mapping[str, Any], existing)


async def _persist_generation_outcome(
    pool: _DatabasePool,
    *,
    event_rows: list[tuple[Any, ...]],
    log_rows: list[tuple[Any, ...]],
    run_id: UUID,
    status: str,
    result: dict[str, Any],
    error: str | None,
) -> None:
    """在同一个显式事务内批量落事件、日志并提交 run 终态."""
    async with pool.acquire() as connection:
        async with _outcome_transaction(connection):
            existing = await _locked_existing_outcome(connection, run_id)
            existing_status = str(existing["status"])
            if existing_status in {"succeeded", "failed"}:
                if (
                    existing_status == status
                    and _decode_jsonb(existing["result"]) == result
                    and existing["error"] == error
                ):
                    logger.info(
                        "agent.process.database.write.idempotent run_id=%s "
                        "status=%s persistence_stage=outcome_transaction",
                        run_id,
                        status,
                    )
                    return
                raise AgentRunOutcomeConflictError(
                    "Agent run 已存在不同终态，拒绝覆盖。"
                )
            await _execute_many(connection, _EVENT_INSERT_SQL, event_rows)
            await _execute_many(connection, _LOG_INSERT_SQL, log_rows)
            await connection.execute(
                _RUN_UPDATE_SQL,
                run_id,
                status,
                _jsonb(result),
                error,
            )


async def create_agent_run(
    pool: _DatabasePool,
    *,
    run_id: UUID,
    project_id: UUID | None = None,
    input: dict[str, Any] | None = None,
    glsl_model_name: str | None = None,
    vision_model_name: str | None = None,
    status: str = "running",
) -> None:
    """写入一次 Agent 运行总账."""
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO agent_runs (
                id,
                project_id,
                status,
                glsl_model_name,
                vision_model_name,
                input
            )
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            """,
            run_id,
            project_id,
            status,
            glsl_model_name,
            vision_model_name,
            _jsonb(input),
        )


async def start_shader_generation_run(
    pool: _DatabasePool,
    *,
    run_id: UUID,
    project_id: UUID,
    filename: str | None,
    content_type: str,
    size_bytes: int,
    glsl_model_name: str,
    vision_model_name: str,
    generation_mode: str = "procedural_v1",
    quality_preset: str | None = None,
    instruction: str = "",
    runtime_policy: Mapping[str, Any] | None = None,
) -> None:
    """写入 Shader 生成运行记录."""
    try:
        await create_agent_run(
            pool,
            run_id=run_id,
            project_id=project_id,
            input={
                "filename": filename,
                "content_type": content_type,
                "size_bytes": size_bytes,
                "generation_mode": generation_mode,
                "quality_preset": quality_preset,
                "instruction": instruction,
                "runtime_policy": dict(runtime_policy or {}),
            },
            glsl_model_name=glsl_model_name,
            vision_model_name=vision_model_name,
        )
    except Exception as error:
        logger.error(
            "agent.process.database.write.failed run_id=%s "
            "persistence_stage=create_generation_run error_type=%s",
            run_id,
            type(error).__name__,
        )
        raise


async def record_shader_generation_success(
    pool: _DatabasePool,
    *,
    run_id: UUID,
    model_name: str,
    glsl_chars: int,
    model_calls: Iterable[Mapping[str, Any]] | None = None,
    events: Iterable[Mapping[str, Any]] | None = None,
    logs: Iterable[Mapping[str, Any]] | None = None,
    result_summary: dict[str, Any] | None = None,
) -> None:
    """写入 Shader 生成成功后的事件、日志和状态."""
    try:
        seq = 1
        wrote_model_call = False
        event_rows: list[tuple[Any, ...]] = []
        log_rows: list[tuple[Any, ...]] = []
        for model_call in model_calls or ():
            payload = dict(model_call)
            reasoning_content = _pop_reasoning_content(payload)
            event_rows.append(
                (
                    run_id,
                    seq,
                    "agent",
                    "model_call",
                    _jsonb(payload),
                    reasoning_content,
                )
            )
            seq += 1
            wrote_model_call = True
        if not wrote_model_call:
            event_rows.append(
                (
                    run_id,
                    seq,
                    "agent",
                    "model_call",
                    _jsonb({"model": model_name, "glsl_chars": glsl_chars}),
                    None,
                )
            )
            seq += 1
        for event in events or ():
            payload = dict(event.get("payload", {}))
            reasoning_content = _pop_reasoning_content(payload)
            if not reasoning_content and event.get("reasoning_content"):
                reasoning_content = str(event["reasoning_content"])
            event_rows.append(
                (
                    run_id,
                    seq,
                    str(event.get("stage", "agent")),
                    str(event.get("event_type", "completed")),
                    _jsonb(payload),
                    reasoning_content,
                )
            )
            seq += 1
        for log in logs or ():
            log_rows.append(
                (
                    run_id,
                    log.get("event_seq"),
                    str(log.get("level", "info")),
                    str(log.get("source", "agent")),
                    str(log.get("message", "")),
                    _jsonb(dict(log.get("context", {}))),
                )
            )
        log_rows.append(
            (
                run_id,
                1,
                "info",
                "backend.shader",
                "GLSL 生成完成",
                _jsonb({"model": model_name, "glsl_chars": glsl_chars}),
            )
        )
        result = {"glsl_chars": glsl_chars}
        result.update(result_summary or {})
        await _persist_generation_outcome(
            pool,
            event_rows=event_rows,
            log_rows=log_rows,
            run_id=run_id,
            status="succeeded",
            result=result,
            error=None,
        )
        logger.info(
            "agent.process.database.write.succeeded run_id=%s status=succeeded "
            "persistence_stage=outcome_transaction",
            run_id,
        )
    except Exception as error:
        logger.error(
            "agent.process.database.write.failed run_id=%s status=succeeded "
            "persistence_stage=outcome_transaction error_type=%s",
            run_id,
            type(error).__name__,
        )
        raise


async def record_shader_generation_failure(
    pool: _DatabasePool,
    *,
    run_id: UUID,
    error: Exception,
    model_calls: Iterable[Mapping[str, Any]] | None = None,
    events: Iterable[Mapping[str, Any]] | None = None,
    logs: Iterable[Mapping[str, Any]] | None = None,
    stop_reason: str | None = None,
    diagnostics: Mapping[str, Any] | None = None,
) -> None:
    """写入 Shader 生成失败后的日志和状态."""
    try:
        seq = 1
        event_rows: list[tuple[Any, ...]] = []
        log_rows: list[tuple[Any, ...]] = []
        for model_call in model_calls or ():
            payload = dict(model_call)
            reasoning_content = _pop_reasoning_content(payload)
            event_rows.append(
                (
                    run_id,
                    seq,
                    "agent",
                    "model_call",
                    _jsonb(payload),
                    reasoning_content,
                )
            )
            seq += 1
        for event in events or ():
            payload = dict(event.get("payload", {}))
            reasoning_content = _pop_reasoning_content(payload)
            if not reasoning_content and event.get("reasoning_content"):
                reasoning_content = str(event["reasoning_content"])
            event_rows.append(
                (
                    run_id,
                    seq,
                    str(event.get("stage", "agent")),
                    str(event.get("event_type", "failed")),
                    _jsonb(payload),
                    reasoning_content,
                )
            )
            seq += 1
        for log in logs or ():
            log_rows.append(
                (
                    run_id,
                    log.get("event_seq"),
                    str(log.get("level", "info")),
                    str(log.get("source", "agent")),
                    str(log.get("message", "")),
                    _jsonb(dict(log.get("context", {}))),
                )
            )
        failure_context = {
            "error_type": type(error).__name__,
            "stop_reason": stop_reason,
            **dict(diagnostics or {}),
        }
        log_rows.append(
            (
                run_id,
                None,
                "error",
                "backend.shader",
                "生成 GLSL 失败",
                _jsonb(failure_context),
            )
        )
        failure_result = {
            "stop_reason": stop_reason,
            "diagnostics": dict(diagnostics or {}),
        }
        await _persist_generation_outcome(
            pool,
            event_rows=event_rows,
            log_rows=log_rows,
            run_id=run_id,
            status="failed",
            result=failure_result,
            error=_safe_error_summary(
                error,
                stop_reason or "generation_failed",
            ),
        )
        logger.info(
            "agent.process.database.write.succeeded run_id=%s status=failed "
            "persistence_stage=outcome_transaction",
            run_id,
        )
    except Exception as error:
        logger.error(
            "agent.process.database.write.failed run_id=%s status=failed "
            "persistence_stage=outcome_transaction error_type=%s",
            run_id,
            type(error).__name__,
        )
        raise


async def append_agent_event(
    pool: _DatabasePool,
    *,
    run_id: UUID,
    seq: int,
    stage: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    reasoning_content: str | None = None,
) -> None:
    """写入一次运行内的业务过程事件."""
    async with pool.acquire() as connection:
        await connection.execute(
            _EVENT_INSERT_SQL,
            run_id,
            seq,
            stage,
            event_type,
            _jsonb(payload),
            reasoning_content,
        )


async def append_agent_log(
    pool: _DatabasePool,
    *,
    run_id: UUID,
    level: str,
    source: str,
    message: str,
    context: dict[str, Any] | None = None,
    event_seq: int | None = None,
) -> None:
    """写入一次运行内的安全诊断日志摘要."""
    async with pool.acquire() as connection:
        await connection.execute(
            _LOG_INSERT_SQL,
            run_id,
            event_seq,
            level,
            source,
            message,
            _jsonb(context),
        )


async def complete_agent_run(
    pool: _DatabasePool,
    *,
    run_id: UUID,
    result: dict[str, Any] | None = None,
) -> None:
    """把一次 Agent 运行标记为成功."""
    await _finish_agent_run(pool, run_id=run_id, status="succeeded", result=result)


async def fail_agent_run(
    pool: _DatabasePool,
    *,
    run_id: UUID,
    error: str,
    result: dict[str, Any] | None = None,
) -> None:
    """把一次 Agent 运行标记为失败."""
    await _finish_agent_run(
        pool,
        run_id=run_id,
        status="failed",
        result=result,
        error=error,
    )


async def _finish_agent_run(
    pool: _DatabasePool,
    *,
    run_id: UUID,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            _RUN_UPDATE_SQL,
            run_id,
            status,
            _jsonb(result),
            error,
        )
