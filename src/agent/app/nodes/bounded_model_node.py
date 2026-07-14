"""为 PNG 转 Shader V1 模型节点施加共享硬预算与失败降级."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from agent.app.contracts.llm import LLMGatewayError
from agent.app.nodes.structured_output import (
    StructuredOutputExhaustedError,
    StructuredOutputInvocationError,
)
from shaderforge.contracts import BudgetPolicy, StopReason

Clock = Callable[[], float]
ModelNode = Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]]
logger = logging.getLogger("agent.png_to_shader")

STAGE_TIMEOUT_CAP_SECONDS = {
    "visual_analysis": 60.0,
    "author_initial": 120.0,
    "author_compile_repair": 60.0,
    "visual_critic": 45.0,
    "author_visual_refine": 90.0,
}
DEFAULT_STAGE_TIMEOUT_CAP_SECONDS = 60.0
MAX_DOWNSTREAM_RESERVE_SECONDS = 30.0
DOWNSTREAM_RESERVE_RATIO = 0.10


def _run_fields(state: Mapping[str, Any]) -> tuple[str, str]:
    return str(state.get("run_id", "unknown")), str(state.get("project_id", "unknown"))


def _budget(state: Mapping[str, Any]) -> BudgetPolicy:
    value = state["budget_policy"]
    if isinstance(value, BudgetPolicy):
        return value
    return BudgetPolicy(**dict(value))


def _failure_event(
    stage: str,
    error_type: str,
    attempts: int,
    **context: Any,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "event_type": "model_failed",
        "payload": {
            "error_type": error_type,
            "attempted_calls": attempts,
            **context,
        },
    }


def _elapsed_seconds(state: Mapping[str, Any], clock: Clock) -> float:
    return max(0.0, clock() - float(state["started_at"]))


def _downstream_reserve_seconds(budget: BudgetPolicy) -> float:
    """保留确定性修复、渲染、Finalize 和持久化所需时间."""
    return min(
        MAX_DOWNSTREAM_RESERVE_SECONDS,
        budget.max_wall_time_seconds * DOWNSTREAM_RESERVE_RATIO,
    )


def make_bounded_model_node(
    delegate: ModelNode,
    *,
    stage: str,
    clock: Clock,
    attempt_counter_field: str | None = None,
) -> ModelNode:
    """包装一个 M2 角色 Node，严格限制模型次数、wall-time 和失败传播."""

    async def bounded_model(state: Mapping[str, Any]) -> dict[str, Any]:
        budget = _budget(state)
        used = int(state.get("model_call_count", 0))
        events = tuple(state.get("events", ()))
        counter_update: dict[str, Any] = {}

        if state.get("cancelled", False):
            return {
                "phase": stage,
                "stop_reason": StopReason.CANCELLED.value,
                "events": events,
            }

        elapsed_before = _elapsed_seconds(state, clock)
        remaining_wall = budget.max_wall_time_seconds - elapsed_before
        if remaining_wall <= 0.0:
            return {
                "phase": stage,
                "stop_reason": StopReason.WALL_TIME_EXHAUSTED.value,
                "events": (
                    *events,
                    _failure_event(
                        stage,
                        "DeadlineUnavailable",
                        0,
                        timeout_source="global_deadline",
                        elapsed_seconds=round(elapsed_before, 3),
                        remaining_wall_seconds=0.0,
                    ),
                ),
            }

        remaining_calls = budget.max_model_calls - used
        if remaining_calls <= 0:
            return {
                "phase": stage,
                "stop_reason": StopReason.MODEL_BUDGET_EXHAUSTED.value,
                "events": events,
            }

        downstream_reserve = _downstream_reserve_seconds(budget)
        callable_wall = remaining_wall - downstream_reserve
        if callable_wall <= 0.0:
            return {
                "phase": stage,
                "stop_reason": StopReason.WALL_TIME_EXHAUSTED.value,
                "events": (
                    *events,
                    _failure_event(
                        stage,
                        "DeadlineUnavailable",
                        0,
                        timeout_source="downstream_reserve",
                        elapsed_seconds=round(elapsed_before, 3),
                        remaining_wall_seconds=round(max(0.0, remaining_wall), 3),
                        reserved_wall_seconds=round(downstream_reserve, 3),
                    ),
                ),
            }

        stage_cap = STAGE_TIMEOUT_CAP_SECONDS.get(
            stage, DEFAULT_STAGE_TIMEOUT_CAP_SECONDS
        )
        timeout_seconds = min(stage_cap, callable_wall)
        timeout_source = (
            "stage_cap" if stage_cap <= callable_wall else "wall_deadline_reserve"
        )

        if attempt_counter_field is not None:
            counter_update[attempt_counter_field] = (
                int(state.get(attempt_counter_field, 0)) + 1
            )

        prepared = dict(state)
        prepared["structured_output_max_attempts"] = min(2, remaining_calls)
        previous_calls = tuple(state.get("model_calls", ()))
        run_id, project_id = _run_fields(state)
        logger.info(
            "shader.pipeline.model.started run_id=%s project_id=%s stage=%s "
            "used_calls=%s remaining_calls=%s remaining_wall_seconds=%.2f "
            "timeout_seconds=%.2f timeout_source=%s downstream_reserve_seconds=%.2f",
            run_id,
            project_id,
            stage,
            used,
            remaining_calls,
            remaining_wall,
            timeout_seconds,
            timeout_source,
            downstream_reserve,
        )
        call_started_at = clock()
        try:
            result = await asyncio.wait_for(
                delegate(prepared),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            stage_elapsed = max(0.0, clock() - call_started_at)
            elapsed_after = _elapsed_seconds(state, clock)
            remaining_after = max(0.0, budget.max_wall_time_seconds - elapsed_after)
            stop_reason = (
                StopReason.COMPLETED_WITH_BEST_EFFORT.value
                if timeout_source == "stage_cap"
                else StopReason.WALL_TIME_EXHAUSTED.value
            )
            logger.error(
                "shader.pipeline.model.failed run_id=%s project_id=%s stage=%s "
                "error_type=TimeoutError timeout_source=%s timeout_seconds=%.2f "
                "stage_elapsed_seconds=%.2f remaining_wall_seconds=%.2f",
                run_id,
                project_id,
                stage,
                timeout_source,
                timeout_seconds,
                stage_elapsed,
                remaining_after,
            )
            return {
                **counter_update,
                "phase": stage,
                "model_call_count": used + 1,
                "stop_reason": stop_reason,
                "events": (
                    *events,
                    _failure_event(
                        stage,
                        "TimeoutError",
                        1,
                        timeout_source=timeout_source,
                        timeout_seconds=round(timeout_seconds, 3),
                        stage_elapsed_seconds=round(stage_elapsed, 3),
                        elapsed_seconds=round(elapsed_after, 3),
                        remaining_wall_seconds=round(remaining_after, 3),
                        reserved_wall_seconds=round(downstream_reserve, 3),
                        used_model_calls=used,
                        attempt_count_incomplete=True,
                    ),
                ),
            }
        except StructuredOutputExhaustedError as exc:
            audits = tuple(audit.to_dict() for audit in exc.audits)
            attempts = len(audits)
            reason = StopReason.COMPLETED_WITH_BEST_EFFORT.value
            if remaining_calls == 1 and attempts == 1:
                reason = StopReason.MODEL_BUDGET_EXHAUSTED.value
            logger.warning(
                "shader.pipeline.model.failed run_id=%s project_id=%s stage=%s "
                "error_type=%s attempted_calls=%s error_codes=%s",
                run_id,
                project_id,
                stage,
                type(exc).__name__,
                attempts,
                ",".join(exc.last_error.error_codes),
            )
            return {
                **counter_update,
                "phase": stage,
                "model_call_count": used + attempts,
                "model_calls": (*previous_calls, *audits),
                "stop_reason": reason,
                "events": (
                    *events,
                    _failure_event(
                        stage,
                        type(exc).__name__,
                        attempts,
                        elapsed_seconds=round(_elapsed_seconds(state, clock), 3),
                        stage_elapsed_seconds=round(
                            max(0.0, clock() - call_started_at), 3
                        ),
                    ),
                ),
            }
        except StructuredOutputInvocationError as exc:
            audits = tuple(audit.to_dict() for audit in exc.audits)
            logger.warning(
                "shader.pipeline.model.failed run_id=%s project_id=%s stage=%s "
                "error_type=%s attempted_calls=%s provider_error_type=%s",
                run_id,
                project_id,
                stage,
                type(exc).__name__,
                exc.attempted_calls,
                exc.error_type,
            )
            return {
                **counter_update,
                "phase": stage,
                "model_call_count": used + exc.attempted_calls,
                "model_calls": (*previous_calls, *audits),
                "stop_reason": StopReason.COMPLETED_WITH_BEST_EFFORT.value,
                "events": (
                    *events,
                    _failure_event(
                        stage,
                        exc.error_type,
                        exc.attempted_calls,
                        elapsed_seconds=round(_elapsed_seconds(state, clock), 3),
                        stage_elapsed_seconds=round(
                            max(0.0, clock() - call_started_at), 3
                        ),
                    ),
                ),
            }
        except LLMGatewayError as exc:
            logger.warning(
                "shader.pipeline.model.failed run_id=%s project_id=%s stage=%s "
                "error_type=%s attempted_calls=1 retryable=%s",
                run_id,
                project_id,
                stage,
                type(exc).__name__,
                str(exc.retryable).lower(),
            )
            return {
                **counter_update,
                "phase": stage,
                "model_call_count": used + 1,
                "stop_reason": StopReason.COMPLETED_WITH_BEST_EFFORT.value,
                "events": (
                    *events,
                    _failure_event(
                        stage,
                        type(exc).__name__,
                        1,
                        retryable=exc.retryable,
                        elapsed_seconds=round(_elapsed_seconds(state, clock), 3),
                        stage_elapsed_seconds=round(
                            max(0.0, clock() - call_started_at), 3
                        ),
                    ),
                ),
            }
        except Exception as exc:
            logger.error(
                "shader.pipeline.model.failed run_id=%s project_id=%s stage=%s "
                "error_type=%s unexpected_internal_error=true",
                run_id,
                project_id,
                stage,
                type(exc).__name__,
            )
            # 只有已知的超时、结构化输出和供应商调用失败可以降级为业务
            # 终态。编程错误或不变量破坏必须越过 Graph 交给 API 映射为 500，
            # 否则会被误报成用户可修复的 422 no_validated_shader。
            raise

        current_calls = tuple(result.get("model_calls", previous_calls))
        if current_calls[: len(previous_calls)] != previous_calls:
            raise RuntimeError("模型 Node 覆盖了既有 model_calls 审计链。")
        consumed = len(current_calls) - len(previous_calls)
        if consumed <= 0 or used + consumed > budget.max_model_calls:
            raise RuntimeError("模型 Node 违反 max_model_calls 硬预算。")
        total_latency_ms = sum(
            int(call.get("latency_ms", 0) or 0)
            for call in current_calls[len(previous_calls) :]
        )
        logger.info(
            "shader.pipeline.model.completed run_id=%s project_id=%s stage=%s "
            "consumed_calls=%s total_latency_ms=%s",
            run_id,
            project_id,
            stage,
            consumed,
            total_latency_ms,
        )
        return {
            **result,
            **counter_update,
            "phase": stage,
            "model_call_count": used + consumed,
            "events": (
                *events,
                {
                    "stage": stage,
                    "event_type": "model_completed",
                    "payload": {
                        "consumed_calls": consumed,
                        "stage_elapsed_seconds": round(
                            max(0.0, clock() - call_started_at), 3
                        ),
                        "elapsed_seconds": round(_elapsed_seconds(state, clock), 3),
                        "remaining_wall_seconds": round(
                            max(
                                0.0,
                                budget.max_wall_time_seconds
                                - _elapsed_seconds(state, clock),
                            ),
                            3,
                        ),
                    },
                },
            ),
        }

    return bounded_model
