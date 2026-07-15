"""真实 WebGL1 渲染、编译失败归档与渲染证据持久化."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Any

from shaderforge.analysis import TargetMeasurements
from shaderforge.contracts import StopReason
from shaderforge.evaluation import CandidateRecord
from shaderforge.rendering import RendererUnavailableError, RenderResult
from shaderforge.store import ArtifactRef, RunArtifactStore

from .runtime import (
    Clock,
    RunRendererRegistry,
    _budget,
    _elapsed_seconds,
    _evaluation_measurements,
    _finalize_reserve_seconds,
    _replace_record,
    _run_key,
    _validation_diagnostics,
    _wall_remaining,
    _write_candidate_manifest,
    logger,
)


@dataclass(frozen=True)
class SuccessfulRender:
    """评分阶段所需的真实渲染结果和证据绑定."""

    render: RenderResult
    render_ref: ArtifactRef
    rendered_record: CandidateRecord
    evaluation_measurements: TargetMeasurements


async def render_candidate(
    store: RunArtifactStore,
    state: Mapping[str, Any],
    record: CandidateRecord,
    glsl: str,
    events: tuple[Any, ...],
    repair_update: Mapping[str, Any],
    *,
    renderer_registry: RunRendererRegistry,
    clock: Clock,
    run_id: str,
    project_id: str,
) -> SuccessfulRender | dict[str, Any]:
    """在 finalize 预留时间边界内完成真实渲染和证据落盘."""
    budget = _budget(state)
    remaining_wall = _wall_remaining(state, clock)
    finalize_reserve = _finalize_reserve_seconds(state)
    renderer_timeout = remaining_wall - finalize_reserve
    if renderer_timeout <= 0.0:
        return {
            **repair_update,
            "phase": "render_skipped",
            "render_status": "wall_time_exhausted",
            "stop_reason": StopReason.WALL_TIME_EXHAUSTED.value,
            "events": (
                *events,
                {
                    "stage": "render",
                    "event_type": "renderer_skipped",
                    "payload": {
                        "candidate_id": record.candidate_id,
                        "reason": StopReason.WALL_TIME_EXHAUSTED.value,
                        "remaining_wall_seconds": round(max(0.0, remaining_wall), 3),
                        "reserved_wall_seconds": round(finalize_reserve, 3),
                        "elapsed_seconds": round(_elapsed_seconds(state, clock), 3),
                    },
                },
            ),
        }

    measurements = state["target_measurements"]
    if not isinstance(measurements, TargetMeasurements):
        raise TypeError("target_measurements 必须是 TargetMeasurements。")
    evaluation_measurements = _evaluation_measurements(state, measurements)
    try:
        render = await asyncio.wait_for(
            renderer_registry.render(
                _run_key(state),
                replay_on_worker_failure=budget.renderer_replay_on_crash,
                fragment_source=glsl,
                width=measurements.analysis_width,
                height=measurements.analysis_height,
            ),
            timeout=renderer_timeout,
        )
    except TimeoutError:
        logger.error(
            "shader.pipeline.render.failed run_id=%s project_id=%s "
            "candidate_id=%s failure_stage=renderer error_type=TimeoutError",
            run_id,
            project_id,
            record.candidate_id,
        )
        return {
            **repair_update,
            "phase": "render_failed",
            "render_status": "wall_time_exhausted",
            "stop_reason": StopReason.WALL_TIME_EXHAUSTED.value,
            "events": (
                *events,
                {
                    "stage": "render",
                    "event_type": "renderer_failed",
                    "payload": {
                        "candidate_id": record.candidate_id,
                        "error_type": "TimeoutError",
                        "timeout_seconds": round(renderer_timeout, 3),
                        "reserved_wall_seconds": round(finalize_reserve, 3),
                        "elapsed_seconds": round(_elapsed_seconds(state, clock), 3),
                    },
                },
            ),
        }
    except RendererUnavailableError as exc:
        return _renderer_failure(
            state,
            record,
            events,
            repair_update,
            clock=clock,
            run_id=run_id,
            project_id=project_id,
            error_type=type(exc).__name__,
        )
    except Exception as exc:
        return _renderer_failure(
            state,
            record,
            events,
            repair_update,
            clock=clock,
            run_id=run_id,
            project_id=project_id,
            error_type=type(exc).__name__,
        )

    prefix = f"candidates/{record.candidate_id}"
    compile_ref = store.write_json(f"{prefix}/compile.json", render.compile)
    if not render.success or not render.compile.success or render.image_bytes is None:
        logger.warning(
            "shader.pipeline.render.failed run_id=%s project_id=%s "
            "candidate_id=%s failure_stage=webgl_compile draw_error=%s",
            run_id,
            project_id,
            record.candidate_id,
            render.compile.draw_error or "none",
        )
        failed = replace(record, compile_ref=compile_ref.relative_path)
        _write_candidate_manifest(store, failed)
        return {
            **repair_update,
            "phase": "compile_failed",
            "candidate_record": failed,
            "candidate_records": _replace_record(
                tuple(state.get("candidate_records", ())),
                failed,
            ),
            "static_validation": render.compile.static_validation.to_dict(),
            "compile_result": render.compile.to_dict(),
            "render_status": "compile_failed",
            "events": (
                *events,
                {
                    "stage": "render",
                    "event_type": "compile_failed",
                    "payload": {
                        "candidate_id": record.candidate_id,
                        "failure_stage": "webgl_compile",
                        **_validation_diagnostics(render.compile.static_validation),
                        "draw_error": render.compile.draw_error,
                        # 编译器日志可能回显源码；事件只写长度与摘要。
                        "fragment_log_chars": len(render.compile.fragment_log),
                        "fragment_log_sha256": sha256(
                            render.compile.fragment_log.encode("utf-8")
                        ).hexdigest(),
                        "link_log_chars": len(render.compile.link_log),
                        "link_log_sha256": sha256(
                            render.compile.link_log.encode("utf-8")
                        ).hexdigest(),
                        "elapsed_seconds": round(_elapsed_seconds(state, clock), 3),
                    },
                },
            ),
        }

    render_ref = store.write_bytes(
        f"{prefix}/render.png",
        render.image_bytes,
        content_type="image/png",
    )
    rendered_record = replace(
        record,
        compile_ref=compile_ref.relative_path,
        render_ref=render_ref.relative_path,
        render_sha256=render_ref.sha256,
        hard_constraints_passed=True,
    )
    _write_candidate_manifest(store, rendered_record)
    return SuccessfulRender(
        render=render,
        render_ref=render_ref,
        rendered_record=rendered_record,
        evaluation_measurements=evaluation_measurements,
    )


def _renderer_failure(
    state: Mapping[str, Any],
    record: CandidateRecord,
    events: tuple[Any, ...],
    repair_update: Mapping[str, Any],
    *,
    clock: Clock,
    run_id: str,
    project_id: str,
    error_type: str,
) -> dict[str, Any]:
    logger.error(
        "shader.pipeline.render.failed run_id=%s project_id=%s "
        "candidate_id=%s failure_stage=renderer error_type=%s",
        run_id,
        project_id,
        record.candidate_id,
        error_type,
    )
    return {
        **repair_update,
        "phase": "render_failed",
        "render_status": "renderer_unavailable",
        "stop_reason": StopReason.RENDERER_UNAVAILABLE.value,
        "events": (
            *events,
            {
                "stage": "render",
                "event_type": "renderer_failed",
                "payload": {
                    "candidate_id": record.candidate_id,
                    "error_type": error_type,
                    "elapsed_seconds": round(_elapsed_seconds(state, clock), 3),
                },
            },
        ),
    }
