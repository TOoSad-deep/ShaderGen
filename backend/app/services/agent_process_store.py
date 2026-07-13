"""Agent 过程数据写入服务."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from typing import Any
from uuid import UUID

logger = logging.getLogger("backend.agent_process")


def _jsonb(value: dict[str, Any] | None) -> str:
    """把 Python 字典转成稳定 JSON 字符串，交给 SQL 转 jsonb."""
    return json.dumps(value or {}, ensure_ascii=False, separators=(",", ":"))


def _pop_reasoning_content(payload: dict[str, Any]) -> str | None:
    """从事件 payload 中取出需要单独入列保存的思维链."""
    reasoning_content = payload.pop("reasoning_content", None)
    if not reasoning_content:
        return None
    return str(reasoning_content)


async def create_agent_run(
    pool,
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
    pool,
    *,
    run_id: UUID,
    project_id: UUID,
    filename: str | None,
    content_type: str,
    size_bytes: int,
    glsl_model_name: str,
    vision_model_name: str,
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
            },
            glsl_model_name=glsl_model_name,
            vision_model_name=vision_model_name,
        )
    except Exception:
        logger.exception("agent.process.database.write.failed run_id=%s", run_id)
        raise


async def start_shader_review_run(
    pool,
    *,
    run_id: UUID,
    project_id: UUID,
    original_content_type: str,
    original_size_bytes: int,
    rendered_content_type: str,
    rendered_size_bytes: int,
    glsl_chars: int,
) -> None:
    """写入 Shader 渲染评审运行记录."""
    try:
        await create_agent_run(
            pool,
            run_id=run_id,
            project_id=project_id,
            input={
                "original_content_type": original_content_type,
                "original_size_bytes": original_size_bytes,
                "rendered_content_type": rendered_content_type,
                "rendered_size_bytes": rendered_size_bytes,
                "glsl_chars": glsl_chars,
            },
        )
    except Exception:
        logger.exception("agent.process.database.write.failed run_id=%s", run_id)
        raise


async def record_shader_generation_success(
    pool,
    *,
    run_id: UUID,
    model_name: str,
    glsl_chars: int,
    model_calls: Iterable[Mapping[str, Any]] | None = None,
    events: Iterable[Mapping[str, Any]] | None = None,
    logs: Iterable[Mapping[str, Any]] | None = None,
) -> None:
    """写入 Shader 生成成功后的事件、日志和状态."""
    try:
        seq = 1
        wrote_model_call = False
        for model_call in model_calls or ():
            payload = dict(model_call)
            reasoning_content = _pop_reasoning_content(payload)
            await append_agent_event(
                pool,
                run_id=run_id,
                seq=seq,
                stage="agent",
                event_type="model_call",
                payload=payload,
                reasoning_content=reasoning_content,
            )
            seq += 1
            wrote_model_call = True
        if not wrote_model_call:
            await append_agent_event(
                pool,
                run_id=run_id,
                seq=seq,
                stage="agent",
                event_type="model_call",
                payload={"model": model_name, "glsl_chars": glsl_chars},
            )
            seq += 1
        for event in events or ():
            payload = dict(event.get("payload", {}))
            reasoning_content = _pop_reasoning_content(payload)
            if not reasoning_content and event.get("reasoning_content"):
                reasoning_content = str(event["reasoning_content"])
            await append_agent_event(
                pool,
                run_id=run_id,
                seq=seq,
                stage=str(event.get("stage", "agent")),
                event_type=str(event.get("event_type", "completed")),
                payload=payload,
                reasoning_content=reasoning_content,
            )
            seq += 1
        for log in logs or ():
            await append_agent_log(
                pool,
                run_id=run_id,
                event_seq=log.get("event_seq"),
                level=str(log.get("level", "info")),
                source=str(log.get("source", "agent")),
                message=str(log.get("message", "")),
                context=dict(log.get("context", {})),
            )
        await append_agent_log(
            pool,
            run_id=run_id,
            event_seq=1,
            level="info",
            source="backend.shader",
            message="GLSL 生成完成",
            context={"model": model_name, "glsl_chars": glsl_chars},
        )
        await complete_agent_run(pool, run_id=run_id, result={"glsl_chars": glsl_chars})
        logger.info(
            "agent.process.database.write.succeeded run_id=%s status=succeeded",
            run_id,
        )
    except Exception:
        logger.exception("agent.process.database.write.failed run_id=%s", run_id)
        raise


async def record_shader_review_success(
    pool,
    *,
    run_id: UUID,
    model_name: str,
    evaluation: str,
    suggestion_count: int,
    model_calls: Iterable[Mapping[str, Any]] | None = None,
    events: Iterable[Mapping[str, Any]] | None = None,
    logs: Iterable[Mapping[str, Any]] | None = None,
) -> None:
    """写入 Shader 渲染评审成功后的事件、日志和状态."""
    try:
        seq = 1
        wrote_model_call = False
        for model_call in model_calls or ():
            payload = dict(model_call)
            reasoning_content = _pop_reasoning_content(payload)
            await append_agent_event(
                pool,
                run_id=run_id,
                seq=seq,
                stage="review",
                event_type="model_call",
                payload=payload,
                reasoning_content=reasoning_content,
            )
            seq += 1
            wrote_model_call = True
        if not wrote_model_call:
            await append_agent_event(
                pool,
                run_id=run_id,
                seq=seq,
                stage="review",
                event_type="model_call",
                payload={"model": model_name, "suggestion_count": suggestion_count},
            )
            seq += 1
        for event in events or ():
            payload = dict(event.get("payload", {}))
            reasoning_content = _pop_reasoning_content(payload)
            if not reasoning_content and event.get("reasoning_content"):
                reasoning_content = str(event["reasoning_content"])
            await append_agent_event(
                pool,
                run_id=run_id,
                seq=seq,
                stage=str(event.get("stage", "review")),
                event_type=str(event.get("event_type", "completed")),
                payload=payload,
                reasoning_content=reasoning_content,
            )
            seq += 1
        for log in logs or ():
            await append_agent_log(
                pool,
                run_id=run_id,
                event_seq=log.get("event_seq"),
                level=str(log.get("level", "info")),
                source=str(log.get("source", "agent")),
                message=str(log.get("message", "")),
                context=dict(log.get("context", {})),
            )
        await append_agent_log(
            pool,
            run_id=run_id,
            event_seq=1,
            level="info",
            source="backend.shader",
            message="渲染评审完成",
            context={"model": model_name, "suggestion_count": suggestion_count},
        )
        await complete_agent_run(
            pool,
            run_id=run_id,
            result={
                "evaluation": evaluation,
                "suggestion_count": suggestion_count,
            },
        )
        logger.info(
            "agent.process.database.write.succeeded run_id=%s status=succeeded",
            run_id,
        )
    except Exception:
        logger.exception("agent.process.database.write.failed run_id=%s", run_id)
        raise


async def record_shader_generation_failure(
    pool,
    *,
    run_id: UUID,
    error: Exception,
) -> None:
    """写入 Shader 生成失败后的日志和状态."""
    try:
        await append_agent_log(
            pool,
            run_id=run_id,
            level="error",
            source="backend.shader",
            message="生成 GLSL 失败",
            context={"error_type": type(error).__name__},
        )
        await fail_agent_run(pool, run_id=run_id, error=str(error))
        logger.info(
            "agent.process.database.write.succeeded run_id=%s status=failed",
            run_id,
        )
    except Exception:
        logger.exception("agent.process.database.write.failed run_id=%s", run_id)
        raise


async def record_shader_review_failure(
    pool,
    *,
    run_id: UUID,
    error: Exception,
) -> None:
    """写入 Shader 渲染评审失败后的日志和状态."""
    try:
        await append_agent_log(
            pool,
            run_id=run_id,
            level="error",
            source="backend.shader",
            message="评审渲染图失败",
            context={"error_type": type(error).__name__},
        )
        await fail_agent_run(pool, run_id=run_id, error=str(error))
        logger.info(
            "agent.process.database.write.succeeded run_id=%s status=failed",
            run_id,
        )
    except Exception:
        logger.exception("agent.process.database.write.failed run_id=%s", run_id)
        raise


async def append_agent_event(
    pool,
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
            """
            INSERT INTO agent_events (
                run_id,
                seq,
                stage,
                event_type,
                payload,
                reasoning_content
            )
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            """,
            run_id,
            seq,
            stage,
            event_type,
            _jsonb(payload),
            reasoning_content,
        )


async def append_agent_log(
    pool,
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
            """
            INSERT INTO agent_logs (
                run_id,
                event_seq,
                level,
                source,
                message,
                context
            )
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            """,
            run_id,
            event_seq,
            level,
            source,
            message,
            _jsonb(context),
        )


async def complete_agent_run(
    pool,
    *,
    run_id: UUID,
    result: dict[str, Any] | None = None,
) -> None:
    """把一次 Agent 运行标记为成功."""
    await _finish_agent_run(pool, run_id=run_id, status="succeeded", result=result)


async def fail_agent_run(pool, *, run_id: UUID, error: str) -> None:
    """把一次 Agent 运行标记为失败."""
    await _finish_agent_run(pool, run_id=run_id, status="failed", error=error)


async def _finish_agent_run(
    pool,
    *,
    run_id: UUID,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE agent_runs
            SET
                status = $2,
                result = $3::jsonb,
                error = $4,
                finished_at = now()
            WHERE id = $1
            """,
            run_id,
            status,
            _jsonb(result),
            error,
        )
