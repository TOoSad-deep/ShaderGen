"""渲染图评分、评分失败降级与最终候选证据持久化."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from shaderforge.contracts import StopReason
from shaderforge.store import RunArtifactStore

from .render_evaluate_rendering import SuccessfulRender
from .runtime import (
    Clock,
    RenderEvaluator,
    _elapsed_seconds,
    _finalize_reserve_seconds,
    _replace_record,
    _wall_remaining,
    _work_seconds_before_finalize,
    _write_candidate_manifest,
    logger,
)


async def evaluate_rendered_candidate(
    store: RunArtifactStore,
    state: Mapping[str, Any],
    rendered: SuccessfulRender,
    events: tuple[Any, ...],
    repair_update: Mapping[str, Any],
    *,
    evaluator: RenderEvaluator,
    clock: Clock,
    run_id: str,
    project_id: str,
) -> dict[str, Any]:
    """在 finalize 预留时间边界内评分并冻结候选结果."""
    render = rendered.render
    image_bytes = render.image_bytes
    if image_bytes is None:
        raise ValueError("SuccessfulRender 必须包含渲染图片。")
    record = rendered.rendered_record
    evaluation_timeout = _work_seconds_before_finalize(state, clock)
    evaluation_started_at = clock()
    try:
        if evaluation_timeout <= 0.0:
            raise TimeoutError("evaluation deadline unavailable")
        score = await asyncio.wait_for(
            asyncio.to_thread(
                evaluator,
                state["image"],
                image_bytes,
                measurements=rendered.evaluation_measurements,
            ),
            timeout=evaluation_timeout,
        )
    except TimeoutError:
        evaluation_elapsed = max(0.0, clock() - evaluation_started_at)
        logger.error(
            "shader.pipeline.evaluate.failed run_id=%s project_id=%s "
            "candidate_id=%s error_type=TimeoutError timeout_seconds=%.2f",
            run_id,
            project_id,
            record.candidate_id,
            max(0.0, evaluation_timeout),
        )
        return _evaluation_failure(
            state,
            rendered,
            events,
            repair_update,
            clock=clock,
            error_type="TimeoutError",
            evaluation_elapsed=evaluation_elapsed,
            timeout_seconds=max(0.0, evaluation_timeout),
        )
    except Exception as exc:
        evaluation_elapsed = max(0.0, clock() - evaluation_started_at)
        logger.error(
            "shader.pipeline.evaluate.failed run_id=%s project_id=%s "
            "candidate_id=%s error_type=%s",
            run_id,
            project_id,
            record.candidate_id,
            type(exc).__name__,
        )
        return _evaluation_failure(
            state,
            rendered,
            events,
            repair_update,
            clock=clock,
            error_type=type(exc).__name__,
            evaluation_elapsed=evaluation_elapsed,
        )

    prefix = f"candidates/{record.candidate_id}"
    metrics_ref = store.write_json(f"{prefix}/metrics.json", score.to_dict())
    completed = replace(
        record,
        metrics_ref=metrics_ref.relative_path,
        score_summary=score,
    )
    _write_candidate_manifest(store, completed)
    stop_reason = ""
    if _wall_remaining(state, clock) <= 0.0:
        stop_reason = StopReason.WALL_TIME_EXHAUSTED.value
    logger.info(
        "shader.pipeline.evaluate.completed run_id=%s project_id=%s "
        "candidate_id=%s total_loss=%.6f wall_time_exhausted=%s",
        run_id,
        project_id,
        record.candidate_id,
        score.total_loss,
        bool(stop_reason),
    )
    return {
        **repair_update,
        "phase": "evaluated",
        "candidate_record": completed,
        "candidate_records": _replace_record(
            tuple(state.get("candidate_records", ())),
            completed,
        ),
        "static_validation": render.compile.static_validation.to_dict(),
        "compile_result": render.compile.to_dict(),
        "render_status": "success",
        "rendered_image": image_bytes,
        "rendered_content_type": "image/png",
        "score_breakdown": score,
        "stop_reason": stop_reason,
        "events": (
            *events,
            {
                "stage": "evaluate",
                "event_type": "candidate_evaluated",
                "payload": {
                    "candidate_id": record.candidate_id,
                    "total_loss": score.total_loss,
                    "render_sha256": rendered.render_ref.sha256,
                    "elapsed_seconds": round(_elapsed_seconds(state, clock), 3),
                },
            },
        ),
    }


def _evaluation_failure(
    state: Mapping[str, Any],
    rendered: SuccessfulRender,
    events: tuple[Any, ...],
    repair_update: Mapping[str, Any],
    *,
    clock: Clock,
    error_type: str,
    evaluation_elapsed: float,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    record = rendered.rendered_record
    render = rendered.render
    payload: dict[str, Any] = {
        "candidate_id": record.candidate_id,
        "failure_stage": "evaluation",
        "error_type": error_type,
    }
    if timeout_seconds is not None:
        payload.update(
            {
                "timeout_source": "finalize_reserve",
                "timeout_seconds": round(timeout_seconds, 3),
                "stage_elapsed_seconds": round(evaluation_elapsed, 3),
                "remaining_wall_seconds": round(
                    max(0.0, _wall_remaining(state, clock)),
                    3,
                ),
                "reserved_wall_seconds": round(
                    _finalize_reserve_seconds(state),
                    3,
                ),
                "worker_may_finish_in_background": timeout_seconds > 0.0,
            }
        )
    else:
        payload["stage_elapsed_seconds"] = round(evaluation_elapsed, 3)
    payload["elapsed_seconds"] = round(_elapsed_seconds(state, clock), 3)
    return {
        **repair_update,
        "phase": "evaluation_failed",
        "candidate_record": record,
        "candidate_records": _replace_record(
            tuple(state.get("candidate_records", ())),
            record,
        ),
        "static_validation": render.compile.static_validation.to_dict(),
        "compile_result": render.compile.to_dict(),
        "render_status": "evaluation_failed",
        "rendered_image": render.image_bytes,
        "rendered_content_type": "image/png",
        "stop_reason": StopReason.COMPLETED_WITH_BEST_EFFORT.value,
        "events": (
            *events,
            {
                "stage": "evaluate",
                "event_type": "evaluation_failed",
                "payload": payload,
            },
        ),
    }
