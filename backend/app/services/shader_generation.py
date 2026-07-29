"""Direct Shader generation use case: lock, ledger, runtime and response."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from backend.app.core.logging import safe_exception_diagnostics
from backend.app.schemas.shader import (
    QualityPresetName,
    ShaderEngineRunSummary,
    ShaderMinPipelineSummary,
    ShaderResponse,
)
from backend.app.services.agent_process_store import (
    record_shader_generation_failure,
    record_shader_generation_success,
    start_shader_generation_run,
)
from backend.app.services.engine_rollout import ParentRunFailure
from backend.app.services.run_progress import RunProgressRegistry
from backend.app.services.shader import (
    ProjectBusyError,
    ProjectLockRegistry,
    generate_scene_shader_from_image,
)

logger = logging.getLogger("backend.shader")
GENERATION_MODE: Literal["scene_mvp"] = "scene_mvp"


@dataclass(frozen=True)
class ShaderGenerationCommand:
    image: bytes
    filename: str | None
    content_type: str
    project_id: UUID
    run_id: UUID
    quality_preset: QualityPresetName
    instruction: str
    started_at: float


@dataclass(frozen=True)
class ShaderGenerationDependencies:
    pool: Any
    runtime: Any
    locks: ProjectLockRegistry
    progress: RunProgressRegistry | None = None


class ShaderGenerationUseCaseError(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        message: str,
        code: str,
        run_id: UUID,
        stage: str,
        retryable: bool,
        stop_reason: str | None,
    ) -> None:
        self.status_code = status_code
        self.message = message
        self.code = code
        self.run_id = run_id
        self.stage = stage
        self.retryable = retryable
        self.stop_reason = stop_reason
        super().__init__(message)


def _error(
    command: ShaderGenerationCommand,
    *,
    status_code: int,
    message: str,
    code: str,
    stage: str,
    retryable: bool,
) -> ShaderGenerationUseCaseError:
    return ShaderGenerationUseCaseError(
        status_code=status_code,
        message=message,
        code=code,
        run_id=command.run_id,
        stage=stage,
        retryable=retryable,
        stop_reason=code,
    )


async def _record_failure(
    pool: Any,
    command: ShaderGenerationCommand,
    *,
    stop_reason: str,
    stage: str,
    error: Exception,
    diagnostics: dict[str, Any],
) -> None:
    if pool is None:
        return
    try:
        await record_shader_generation_failure(
            pool,
            run_id=command.run_id,
            stop_reason=stop_reason,
            error=error,
            diagnostics=diagnostics,
        )
    except Exception as exc:
        cause_types, stack_frames = safe_exception_diagnostics(exc)
        logger.warning(
            "event=shader.generate.failure_persistence_failed run_id=%s "
            "project_id=%s attempt_id=- attempt_index=- "
            "stage=failure_persistence error_code=failure_persistence_failed "
            "error_type=%s cause_type_chain=%s stack_frames=%s "
            "retryable=false suppressed=true",
            command.run_id,
            command.project_id,
            type(exc).__name__,
            cause_types,
            stack_frames,
        )


async def execute_shader_generation(
    command: ShaderGenerationCommand,
    dependencies: ShaderGenerationDependencies,
) -> ShaderResponse:
    """Execute the sole current Layered Direct product pipeline."""
    progress = dependencies.progress
    pool = dependencies.pool
    run_started = False
    succeeded = False
    stop_reason: str | None = None
    result: Any = None
    response: ShaderResponse | None = None
    trace: list[dict[str, Any]] = []
    if dependencies.runtime is None:
        logger.warning(
            "event=shader.generate.rejected run_id=%s project_id=%s "
            "attempt_id=- attempt_index=- stage=runtime_init "
            "error_code=service_unavailable error_type=ShaderGenerationUseCaseError "
            "retryable=true suppressed=false",
            command.run_id,
            command.project_id,
        )
        raise _error(
            command,
            status_code=503,
            message="Shader Direct 服务尚未就绪。",
            code="service_unavailable",
            stage="runtime_init",
            retryable=True,
        )
    if progress is not None:
        try:
            progress.begin(
                str(command.run_id),
                project_id=str(command.project_id),
                generation_mode=GENERATION_MODE,
                quality_preset=command.quality_preset,
            )
        except ValueError as exc:
            logger.warning(
                "event=shader.generate.rejected run_id=%s project_id=%s "
                "attempt_id=- attempt_index=- stage=run_registry "
                "error_code=run_conflict error_type=%s retryable=false "
                "suppressed=false",
                command.run_id,
                command.project_id,
                type(exc).__name__,
            )
            raise _error(
                command,
                status_code=409,
                message="相同 run_id 的运行正在执行中。",
                code="run_conflict",
                stage="run_registry",
                retryable=False,
            ) from exc
    logger.info(
        "shader.generate.started run_id=%s project_id=%s quality_preset=%s "
        "image_bytes=%s",
        command.run_id,
        command.project_id,
        command.quality_preset,
        len(command.image),
    )
    try:
        async with dependencies.locks.hold(str(command.project_id)):
            if pool is not None:
                await start_shader_generation_run(
                    pool,
                    run_id=command.run_id,
                    project_id=command.project_id,
                    filename=command.filename,
                    content_type=command.content_type,
                    size_bytes=len(command.image),
                    glsl_model_name=GENERATION_MODE,
                    vision_model_name=GENERATION_MODE,
                    generation_mode=GENERATION_MODE,
                    quality_preset=command.quality_preset,
                    instruction=command.instruction,
                )
                run_started = True

            def publish(event: dict[str, Any], render: bytes | None) -> None:
                if progress is None:
                    return
                if render is not None:
                    progress.publish_render(str(command.run_id), render)
                progress.publish(str(command.run_id), event)

            result = await generate_scene_shader_from_image(
                command.image,
                command.content_type,
                project_id=str(command.project_id),
                run_id=str(command.run_id),
                quality_preset=command.quality_preset,
                instruction=command.instruction,
                service=dependencies.runtime,
                on_progress=publish if progress is not None else None,
            )
            try:
                if (
                    str(result.project_id) != str(command.project_id)
                    or str(result.run_id) != str(command.run_id)
                    or result.renderer_path != "direct_program_spec_v1"
                ):
                    raise ValueError("Direct result identity mismatch")
                trace = [dict(item) for item in result.trace]
                engine_run = ShaderEngineRunSummary.model_validate(result.engine_run)
                response = ShaderResponse(
                    project_id=command.project_id,
                    run_id=command.run_id,
                    glsl=str(result.glsl),
                    generation_mode=GENERATION_MODE,
                    quality_preset=command.quality_preset,
                    engine="direct_glsl_layerplan_v1",
                    representation="shader_program_spec_v1",
                    engine_run=engine_run,
                    stop_reason=str(result.stop_reason),
                    render_width=int(result.render_width),
                    render_height=int(result.render_height),
                    final_render_url=(
                        f"/api/shader/runs/{command.run_id}/artifacts/final-render"
                    ),
                    metrics_url=(
                        f"/api/shader/runs/{command.run_id}/artifacts/metrics"
                    ),
                    manifest_url=(
                        f"/api/shader/runs/{command.run_id}/artifacts/manifest"
                    ),
                    min_pipeline=ShaderMinPipelineSummary(
                        mae=float(result.current_best_mae),
                        objective_loss=float(result.current_best_loss),
                        metric_breakdown=dict(result.metric_breakdown),
                        template_version=str(result.template_version),
                        render_count=int(result.render_count),
                        render_budget=int(result.render_budget),
                        llm_call_count=int(result.llm_call_count),
                        llm_budget=int(result.llm_budget),
                        refine_budget=int(result.refine_budget),
                        config_fingerprint=str(result.config_fingerprint),
                        report_schema_version=str(result.report_schema_version),
                        renderer_path="direct_program_spec_v1",
                        target_mae=float(result.target_mae),
                        target_loss=float(result.target_loss),
                        target_reached=bool(result.target_reached),
                        trace=trace,
                    ),
                )
            except Exception as exc:
                stop_reason = "response_contract_failed"
                cause_types, stack_frames = safe_exception_diagnostics(exc)
                logger.error(
                    "event=shader.generate.failed run_id=%s project_id=%s "
                    "attempt_id=- attempt_index=- stage=backend_response "
                    "error_code=%s error_type=%s cause_type_chain=%s "
                    "stack_frames=%s retryable=false suppressed=false",
                    command.run_id,
                    command.project_id,
                    stop_reason,
                    type(exc).__name__,
                    cause_types,
                    stack_frames,
                )
                if run_started:
                    await _record_failure(
                        pool,
                        command,
                        stop_reason=stop_reason,
                        stage="backend_response",
                        error=exc,
                        diagnostics={"failure_error_type": type(exc).__name__},
                    )
                raise _error(
                    command,
                    status_code=500,
                    message="生成已完成，但结果格式校验失败。",
                    code=stop_reason,
                    stage="backend_response",
                    retryable=False,
                ) from exc
            succeeded = True
    except ProjectBusyError as exc:
        stop_reason = "project_busy"
        logger.warning(
            "event=shader.generate.rejected run_id=%s project_id=%s "
            "attempt_id=- attempt_index=- stage=project_lock "
            "error_code=%s error_type=%s retryable=true suppressed=false",
            command.run_id,
            command.project_id,
            stop_reason,
            type(exc).__name__,
        )
        raise _error(
            command,
            status_code=409,
            message="当前项目已有任务正在执行。",
            code=stop_reason,
            stage="project_lock",
            retryable=True,
        ) from exc
    except ParentRunFailure as exc:
        stop_reason = exc.code
        attempt_refs = [item.to_dict() for item in exc.attempt_refs]
        if progress is not None:
            progress.publish(
                str(command.run_id),
                {
                    "node": "engine_rollout",
                    "phase": "engine_failed",
                    "status": "failed",
                    "failure_code": exc.code,
                    "attempt_refs": attempt_refs,
                },
            )
        if run_started:
            await _record_failure(
                pool,
                command,
                stop_reason=exc.code,
                stage="engine_rollout",
                error=exc,
                diagnostics={
                    "failure_stage": "engine_rollout",
                    "attempt_refs": attempt_refs,
                    "backend_duration_ms": round(
                        (time.perf_counter() - command.started_at) * 1000,
                        2,
                    ),
                },
            )
        logger.error(
            "event=shader.generate.failed run_id=%s project_id=%s "
            "attempt_id=- attempt_index=- stage=engine_rollout "
            "error_code=%s error_type=%s retryable=%s suppressed=false "
            "attempt_count=%s attempt_refs=%s",
            command.run_id,
            command.project_id,
            exc.code,
            type(exc).__name__,
            str(exc.code == "direct_attempts_failed").lower(),
            len(attempt_refs),
            attempt_refs,
        )
        raise _error(
            command,
            status_code=502,
            message="Shader 引擎执行失败，未发布父运行结果。",
            code=exc.code,
            stage="engine_rollout",
            retryable=exc.code == "direct_attempts_failed",
        ) from exc
    except ShaderGenerationUseCaseError:
        raise
    except Exception as exc:
        stop_reason = "internal_pipeline_error"
        cause_types, stack_frames = safe_exception_diagnostics(exc)
        logger.error(
            "event=shader.generate.failed run_id=%s project_id=%s "
            "attempt_id=- attempt_index=- stage=pipeline "
            "error_code=%s error_type=%s cause_type_chain=%s stack_frames=%s "
            "retryable=false suppressed=false",
            command.run_id,
            command.project_id,
            stop_reason,
            type(exc).__name__,
            cause_types,
            stack_frames,
        )
        if run_started:
            await _record_failure(
                pool,
                command,
                stop_reason=stop_reason,
                stage="pipeline",
                error=exc,
                diagnostics={"failure_error_type": type(exc).__name__},
            )
        raise _error(
            command,
            status_code=500,
            message="Shader Direct 管线发生内部错误。",
            code=stop_reason,
            stage="pipeline",
            retryable=False,
        ) from exc
    finally:
        if progress is not None:
            progress.finish(
                str(command.run_id),
                "succeeded" if succeeded else "failed",
                stop_reason or getattr(result, "stop_reason", None),
            )

    assert response is not None
    if pool is not None:
        try:
            await record_shader_generation_success(
                pool,
                run_id=command.run_id,
                model_name=GENERATION_MODE,
                glsl_chars=len(result.glsl),
                events=tuple(
                    {
                        "stage": str(item.get("phase", "direct_glsl")),
                        "event_type": f"direct_{item.get('status', 'completed')}",
                        "payload": {
                            key: value
                            for key, value in item.items()
                            if key not in {"phase", "status"}
                        },
                    }
                    for item in trace
                ),
                result_summary=response.model_dump(mode="json"),
                record_default_model_call=False,
            )
        except Exception as exc:
            cause_types, stack_frames = safe_exception_diagnostics(exc)
            logger.warning(
                "event=shader.generate.success_persistence_failed run_id=%s "
                "project_id=%s attempt_id=- attempt_index=- "
                "stage=success_persistence error_code=success_persistence_failed "
                "error_type=%s cause_type_chain=%s stack_frames=%s "
                "retryable=false suppressed=true",
                command.run_id,
                command.project_id,
                type(exc).__name__,
                cause_types,
                stack_frames,
            )
    logger.info(
        "shader.generate.succeeded run_id=%s project_id=%s attempts=%s "
        "duration_ms=%.2f",
        command.run_id,
        command.project_id,
        len(response.engine_run.attempt_refs),
        (time.perf_counter() - command.started_at) * 1000,
    )
    return response
