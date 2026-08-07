"""LangGraph orchestration for one bounded Direct parent rollout.

Flow::

    START -> initialize_parent -> execute_attempt -> record_attempt_outcome
      -> prepare_retry -> execute_attempt
      -> publish_parent -> finalize_parent -> END
      `-----------------> finalize_parent -> ParentRunFailure
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langsmith import tracing_context

from agent.app.services.engine_rollout_artifacts import EngineRolloutArtifactError
from backend.app.core.log_context import scoped_log_context
from backend.app.core.logging import safe_exception_diagnostics
from backend.app.services.engine_rollout_state import (
    DIRECT_ENGINE,
    DIRECT_REPRESENTATION,
    AttemptRef,
    EngineAttemptContext,
    EngineAttemptFailure,
    EngineAttemptSuccess,
    EngineParentGraphContext,
    EngineParentGraphInput,
    EngineParentGraphOutput,
    EngineParentState,
    ParentRunFailure,
    ParentRunPlan,
    ParentRunRequest,
    ParentRunResult,
    child_attempt_id,
)

OutcomeRoute = Literal["prepare_retry", "publish_parent", "finalize_parent"]
logger = logging.getLogger("backend.engine_rollout")


def _trace(state: EngineParentState, node_name: str) -> tuple[str, ...]:
    return (*state.get("completed_nodes", ()), node_name)


def initialize_parent(
    state: EngineParentState,
    runtime: Runtime[EngineParentGraphContext],
) -> dict[str, Any]:
    """Validate parent identity and initialize the bounded attempt state."""
    del runtime
    request = state["request"]
    plan = state["plan"]
    if (
        request.parent_run_id != plan.parent_run_id
        or request.project_id != plan.project_id
    ):
        logger.warning(
            "event=engine.parent_run.rejected run_id=%s project_id=%s "
            "attempt_id=- attempt_index=- stage=parent_run_plan "
            "error_code=parent_run_identity_mismatch "
            "error_type=ParentRunFailure retryable=false suppressed=false",
            request.parent_run_id,
            request.project_id,
        )
        raise ParentRunFailure("parent_run_identity_mismatch", attempt_refs=())
    return {
        "attempt_index": 0,
        "attempt_success": None,
        "attempt_failure_code": None,
        "attempt_refs": (),
        "selected": None,
        "completed_nodes": _trace(state, "initialize_parent"),
    }


def _validate_success(
    result: EngineAttemptSuccess,
    context: EngineAttemptContext,
) -> None:
    if (
        result.attempt_id != context.attempt_id
        or result.engine != DIRECT_ENGINE
        or result.representation != DIRECT_REPRESENTATION
        or result.artifact_scope != "private_attempt"
    ):
        raise EngineAttemptFailure("engine_attempt_identity_mismatch")
    if not isinstance(result.response_payload, dict):
        raise EngineAttemptFailure("engine_response_contract_failed")
    forbidden = {
        "engine_manifest",
        "fragment_source",
        "layer_plan",
        "program_spec",
        "prompt",
        "repair_context",
    }
    pending: list[Any] = [result.response_payload]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if forbidden.intersection(value):
                raise EngineAttemptFailure("engine_response_private_data")
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


async def execute_attempt(
    state: EngineParentState,
    runtime: Runtime[EngineParentGraphContext],
) -> dict[str, Any]:
    """Create, execute, validate, and close exactly one fresh executor."""
    graph_context = runtime.context
    request = state["request"]
    attempt_index = state["attempt_index"]
    attempt_context = EngineAttemptContext(
        parent_run_id=state["plan"].parent_run_id,
        attempt_id=child_attempt_id(state["plan"].parent_run_id, attempt_index),
        attempt_index=attempt_index,
    )
    executor = None
    success: EngineAttemptSuccess | None = None
    failure_code: str | None = None
    try:
        executor = graph_context.direct_factory(attempt_context)
        with scoped_log_context(
            attempt_id=str(attempt_context.attempt_id),
            stage="engine_attempt",
        ):
            success = await asyncio.wait_for(
                executor.execute(request, attempt_context),
                timeout=graph_context.attempt_timeout_seconds,
            )
        _validate_success(success, attempt_context)
    except EngineAttemptFailure as exc:
        success = None
        failure_code = exc.code
        logger.warning(
            "event=engine.attempt.failed run_id=%s project_id=%s "
            "attempt_id=%s attempt_index=%s stage=engine_attempt "
            "error_code=%s error_type=%s retryable=true suppressed=false",
            request.parent_run_id,
            request.project_id,
            attempt_context.attempt_id,
            attempt_context.attempt_index,
            exc.code,
            type(exc).__name__,
        )
    except (TimeoutError, asyncio.TimeoutError) as exc:
        success = None
        failure_code = "engine_attempt_timeout"
        cause_types, stack_frames = safe_exception_diagnostics(exc)
        logger.error(
            "event=engine.attempt.failed run_id=%s project_id=%s "
            "attempt_id=%s attempt_index=%s stage=engine_attempt "
            "error_code=engine_attempt_timeout error_type=%s "
            "cause_type_chain=%s stack_frames=%s retryable=true suppressed=false",
            request.parent_run_id,
            request.project_id,
            attempt_context.attempt_id,
            attempt_context.attempt_index,
            type(exc).__name__,
            cause_types,
            stack_frames,
        )
    except Exception as exc:
        success = None
        failure_code = "engine_attempt_failed"
        cause_types, stack_frames = safe_exception_diagnostics(exc)
        logger.error(
            "event=engine.attempt.failed run_id=%s project_id=%s "
            "attempt_id=%s attempt_index=%s stage=engine_attempt "
            "error_code=engine_attempt_failed error_type=%s "
            "cause_type_chain=%s stack_frames=%s retryable=true suppressed=false",
            request.parent_run_id,
            request.project_id,
            attempt_context.attempt_id,
            attempt_context.attempt_index,
            type(exc).__name__,
            cause_types,
            stack_frames,
        )
    finally:
        if executor is not None:
            try:
                closed = executor.close()
                if inspect.isawaitable(closed):
                    await asyncio.wait_for(
                        closed,
                        timeout=graph_context.close_timeout_seconds,
                    )
            except Exception as exc:
                cause_types, stack_frames = safe_exception_diagnostics(exc)
                logger.warning(
                    "event=engine.attempt.close_failed run_id=%s project_id=%s "
                    "attempt_id=%s attempt_index=%s stage=executor_close "
                    "error_code=engine_attempt_close_failed error_type=%s "
                    "cause_type_chain=%s stack_frames=%s retryable=true "
                    "suppressed=true",
                    request.parent_run_id,
                    request.project_id,
                    attempt_context.attempt_id,
                    attempt_context.attempt_index,
                    type(exc).__name__,
                    cause_types,
                    stack_frames,
                )
    return {
        "attempt_context": attempt_context,
        "attempt_success": success,
        "attempt_failure_code": failure_code,
        "completed_nodes": _trace(state, "execute_attempt"),
    }


def record_attempt_outcome(
    state: EngineParentState,
    runtime: Runtime[EngineParentGraphContext],
) -> dict[str, Any]:
    """Append one safe attempt reference and retain the first success."""
    del runtime
    context = state["attempt_context"]
    success = state.get("attempt_success")
    refs = state["attempt_refs"]
    selected: EngineAttemptSuccess | None
    if success is not None:
        ref = AttemptRef(
            str(context.attempt_id),
            DIRECT_ENGINE,
            DIRECT_REPRESENTATION,
            "succeeded",
        )
        selected = success
    else:
        failure_code = state.get("attempt_failure_code") or "engine_attempt_failed"
        ref = AttemptRef(
            str(context.attempt_id),
            DIRECT_ENGINE,
            DIRECT_REPRESENTATION,
            "failed",
            failure_code,
        )
        selected = state.get("selected")
    return {
        "attempt_refs": (*refs, ref),
        "selected": selected,
        "completed_nodes": _trace(state, "record_attempt_outcome"),
    }


def route_after_outcome(
    state: EngineParentState,
    runtime: Runtime[EngineParentGraphContext],
) -> OutcomeRoute:
    """Route success to publication, otherwise retry or fail closed."""
    if state.get("selected") is not None:
        return "publish_parent"
    if state["attempt_index"] + 1 < runtime.context.direct_attempt_limit:
        return "prepare_retry"
    return "finalize_parent"


def prepare_retry(
    state: EngineParentState,
    runtime: Runtime[EngineParentGraphContext],
) -> dict[str, Any]:
    """Publish the retry transition and advance to the next fresh attempt."""
    del runtime
    next_index = state["attempt_index"] + 1
    request = state["request"]
    failure_code = state.get("attempt_failure_code") or "engine_attempt_failed"
    if request.progress_callback is not None:
        try:
            request.progress_callback(
                {
                    "node": "engine_rollout",
                    "phase": "engine_retry",
                    "status": "running",
                    "engine": DIRECT_ENGINE,
                    "attempt_index": next_index,
                    "failure_code": failure_code,
                },
                None,
            )
        except Exception as exc:
            cause_types, stack_frames = safe_exception_diagnostics(exc)
            logger.warning(
                "event=engine.progress_callback.failed run_id=%s project_id=%s "
                "attempt_id=%s attempt_index=%s stage=progress_callback "
                "error_code=progress_callback_failed error_type=%s "
                "cause_type_chain=%s stack_frames=%s retryable=true suppressed=true",
                request.parent_run_id,
                request.project_id,
                child_attempt_id(request.parent_run_id, next_index),
                next_index,
                type(exc).__name__,
                cause_types,
                stack_frames,
            )
    return {
        "attempt_index": next_index,
        "attempt_success": None,
        "attempt_failure_code": None,
        "completed_nodes": _trace(state, "prepare_retry"),
    }


async def publish_parent(
    state: EngineParentState,
    runtime: Runtime[EngineParentGraphContext],
) -> dict[str, Any]:
    """Atomically publish the selected private attempt as the parent result."""
    selected = state["selected"]
    assert selected is not None
    request = state["request"]
    refs = state["attempt_refs"]
    engine_run = {
        "selected_engine": DIRECT_ENGINE,
        "selected_representation": DIRECT_REPRESENTATION,
        "selected_attempt_id": str(selected.attempt_id),
        "attempt_refs": [item.to_dict() for item in refs],
    }
    try:
        published = await asyncio.to_thread(
            runtime.context.artifacts.publish_parent,
            project_id=request.project_id,
            parent_run_id=str(request.parent_run_id),
            engine=DIRECT_ENGINE,
            representation=DIRECT_REPRESENTATION,
            engine_run=engine_run,
            selected=selected.artifacts,
            source_filename=request.filename,
            publication_date=request.publication_date,
        )
    except EngineRolloutArtifactError as exc:
        failure = ParentRunFailure(
            "parent_artifact_publish_failed",
            attempt_refs=refs,
        )
        logger.error(
            "event=engine.parent_run.failed run_id=%s project_id=%s "
            "attempt_id=- attempt_index=- stage=parent_artifact_publish "
            "error_code=%s error_type=%s retryable=false suppressed=false "
            "attempt_refs=%s",
            request.parent_run_id,
            request.project_id,
            failure.code,
            type(exc).__name__,
            [item.to_dict() for item in failure.attempt_refs],
        )
        raise failure from exc
    return {
        "engine_run": engine_run,
        "published_artifacts": published,
        "completed_nodes": _trace(state, "publish_parent"),
    }


def finalize_parent(
    state: EngineParentState,
    runtime: Runtime[EngineParentGraphContext],
) -> dict[str, Any]:
    """Freeze a successful result or raise the exhausted parent failure."""
    del runtime
    selected = state.get("selected")
    refs = state["attempt_refs"]
    if selected is None:
        cause = EngineAttemptFailure(
            state.get("attempt_failure_code") or "engine_attempt_failed"
        )
        failure = ParentRunFailure(
            "direct_attempts_failed",
            attempt_refs=refs,
        )
        request = state["request"]
        logger.error(
            "event=engine.parent_run.failed run_id=%s project_id=%s "
            "attempt_id=- attempt_index=- stage=engine_rollout "
            "error_code=%s error_type=%s retryable=true suppressed=false "
            "attempt_refs=%s",
            request.parent_run_id,
            request.project_id,
            failure.code,
            type(failure).__name__,
            [item.to_dict() for item in failure.attempt_refs],
        )
        raise failure from cause
    request = state["request"]
    engine_run = state["engine_run"]
    base = f"/api/shader/runs/{request.parent_run_id}/artifacts"
    payload = {
        **selected.response_payload,
        "project_id": request.project_id,
        "run_id": str(request.parent_run_id),
        "engine": DIRECT_ENGINE,
        "representation": DIRECT_REPRESENTATION,
        "engine_run": engine_run,
        "final_render_url": f"{base}/final-render",
        "metrics_url": f"{base}/metrics",
        "manifest_url": f"{base}/manifest",
    }
    completed_nodes = _trace(state, "finalize_parent")
    return {
        "result": ParentRunResult(
            response_payload=payload,
            engine=DIRECT_ENGINE,
            representation=DIRECT_REPRESENTATION,
            engine_run=engine_run,
            published_artifacts=state["published_artifacts"],
        ),
        "completed_nodes": completed_nodes,
    }


def build_engine_parent_graph() -> CompiledStateGraph[
    EngineParentState,
    EngineParentGraphContext,
    EngineParentGraphInput,
    EngineParentGraphOutput,
]:
    """Build the bounded parent rollout workflow."""
    builder = StateGraph(
        EngineParentState,
        context_schema=EngineParentGraphContext,
        input_schema=EngineParentGraphInput,
        output_schema=EngineParentGraphOutput,
    )
    builder.add_node("initialize_parent", initialize_parent)
    builder.add_node("execute_attempt", execute_attempt)
    builder.add_node("record_attempt_outcome", record_attempt_outcome)
    builder.add_node("prepare_retry", prepare_retry)
    builder.add_node("publish_parent", publish_parent)
    builder.add_node("finalize_parent", finalize_parent)
    builder.add_edge(START, "initialize_parent")
    builder.add_edge("initialize_parent", "execute_attempt")
    builder.add_edge("execute_attempt", "record_attempt_outcome")
    builder.add_conditional_edges(
        "record_attempt_outcome",
        route_after_outcome,
        {
            "prepare_retry": "prepare_retry",
            "publish_parent": "publish_parent",
            "finalize_parent": "finalize_parent",
        },
    )
    builder.add_edge("prepare_retry", "execute_attempt")
    builder.add_edge("publish_parent", "finalize_parent")
    builder.add_edge("finalize_parent", END)
    return builder.compile(name="engine_parent_rollout")


_engine_parent_graph = build_engine_parent_graph()


async def run_engine_parent_graph(
    *,
    request: ParentRunRequest,
    plan: ParentRunPlan,
    context: EngineParentGraphContext,
) -> EngineParentGraphOutput:
    """Invoke one parent rollout through its explicit graph."""
    with tracing_context(enabled=False, parent=False):
        output = await _engine_parent_graph.ainvoke(
            {"request": request, "plan": plan},
            context=context,
        )
    return EngineParentGraphOutput(
        result=output["result"],
        completed_nodes=output["completed_nodes"],
    )


__all__ = [
    "build_engine_parent_graph",
    "execute_attempt",
    "finalize_parent",
    "initialize_parent",
    "prepare_retry",
    "publish_parent",
    "record_attempt_outcome",
    "route_after_outcome",
    "run_engine_parent_graph",
]
