"""Shader 生成用例编排：锁、过程总账、Agent 调用和响应契约."""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, cast
from uuid import UUID

from backend.app.schemas.shader import (
    GenerationMode,
    QualityPresetName,
    ShaderMinPipelineSummary,
    ShaderResponse,
    ShaderReview,
    ShaderScore,
)
from backend.app.services.agent_process_store import (
    record_shader_generation_failure,
    record_shader_generation_success,
    start_shader_generation_run,
)
from backend.app.services.shader import (
    MemoryUnavailableError,
    NoValidatedShaderError,
    ProjectBusyError,
    ProjectLockRegistry,
    generate_procedural_shader_from_image,
    generate_scene_shader_from_image,
    get_png_to_shader_v1_models,
)

logger = logging.getLogger("backend.shader")


@dataclass(frozen=True)
class ShaderGenerationCommand:
    """一次生成请求在 HTTP 校验后的稳定输入."""

    image: bytes
    filename: str | None
    content_type: str
    project_id: UUID
    run_id: UUID
    generation_mode: GenerationMode
    quality_preset: QualityPresetName
    instruction: str
    started_at: float


@dataclass(frozen=True)
class ShaderGenerationDependencies:
    """由 Backend 应用生命周期注入的生成用例依赖."""

    pool: Any
    procedural_service: Any
    min_service: Any | None
    locks: ProjectLockRegistry


class ShaderGenerationUseCaseError(RuntimeError):
    """供 HTTP Route 映射的安全、稳定生成失败."""

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
        """保存 HTTP 层可安全公开的错误字段."""
        self.status_code = status_code
        self.message = message
        self.code = code
        self.run_id = run_id
        self.stage = stage
        self.retryable = retryable
        self.stop_reason = stop_reason
        super().__init__(message)


class _GenerationRunPersistenceError(RuntimeError):
    """表示生成 run 总账创建失败，且只保留安全异常类型."""

    def __init__(self, error_type: str) -> None:
        self.error_type = error_type
        super().__init__("生成 run 总账暂时不可用。")


def _generation_error(
    *,
    status_code: int,
    message: str,
    code: str,
    run_id: UUID,
    stage: str,
    retryable: bool,
    stop_reason: str | None,
) -> ShaderGenerationUseCaseError:
    """构造不暴露图片、GLSL 或模型原文的稳定用例失败."""
    return ShaderGenerationUseCaseError(
        status_code=status_code,
        message=message,
        code=code,
        run_id=run_id,
        stage=stage,
        retryable=retryable,
        stop_reason=stop_reason,
    )


def _no_validated_shader_error(
    exc: NoValidatedShaderError,
    *,
    run_id: UUID,
) -> ShaderGenerationUseCaseError:
    """按停止原因和安全诊断把闭环失败映射到稳定用例语义."""
    diagnostics = exc.diagnostics
    stop_reason = exc.stop_reason
    stage = str(diagnostics.get("failure_stage") or "generation")
    error_type = str(diagnostics.get("failure_error_type") or "")

    if stop_reason == "renderer_unavailable":
        return _generation_error(
            status_code=503,
            message="服务端 WebGL Renderer 暂时不可用。",
            code="renderer_unavailable",
            run_id=run_id,
            stage=stage if stage != "unknown" else "renderer",
            retryable=True,
            stop_reason=stop_reason,
        )
    if stop_reason == "wall_time_exhausted":
        return _generation_error(
            status_code=504,
            message="Shader 自动闭环执行超时。",
            code="generation_timeout",
            run_id=run_id,
            stage=stage,
            retryable=True,
            stop_reason=stop_reason,
        )
    if "timeout" in error_type.casefold():
        return _generation_error(
            status_code=504,
            message="Shader 模型阶段响应超时。",
            code="model_timeout",
            run_id=run_id,
            stage=stage,
            retryable=True,
            stop_reason=stop_reason,
        )
    if error_type == "LLMConfigurationError":
        return _generation_error(
            status_code=500,
            message="Shader 模型服务配置无效。",
            code="model_configuration_error",
            run_id=run_id,
            stage=stage,
            retryable=False,
            stop_reason=stop_reason,
        )
    if error_type == "LLMResponseError":
        return _generation_error(
            status_code=502,
            message="Shader 模型返回了无法处理的响应。",
            code="model_response_invalid",
            run_id=run_id,
            stage=stage,
            retryable=True,
            stop_reason=stop_reason,
        )
    provider_error_name = error_type.casefold().replace("_", "")
    if any(
        token in provider_error_name
        for token in (
            "llminvocation",
            "apiconnection",
            "ratelimit",
            "modelgateway",
            "providererror",
        )
    ):
        return _generation_error(
            status_code=503,
            message="Shader 模型服务暂时不可用。",
            code="model_unavailable",
            run_id=run_id,
            stage=stage,
            retryable=True,
            stop_reason=stop_reason,
        )
    if stop_reason == "model_budget_exhausted":
        return _generation_error(
            status_code=503,
            message="Shader 模型调用预算已耗尽。",
            code="model_budget_exhausted",
            run_id=run_id,
            stage=stage,
            retryable=False,
            stop_reason=stop_reason,
        )
    if stop_reason == "compile_repair_exhausted":
        return _generation_error(
            status_code=422,
            message="Shader 编译修复次数已耗尽，未生成可运行结果。",
            code="shader_validation_failed",
            run_id=run_id,
            stage=stage,
            retryable=False,
            stop_reason=stop_reason,
        )
    return _generation_error(
        status_code=422,
        message="自动闭环未生成通过 WebGL1 门禁的 Shader。",
        code="no_validated_shader",
        run_id=run_id,
        stage=stage,
        retryable=False,
        stop_reason=stop_reason,
    )


async def _record_failure_without_masking(
    pool: Any,
    *,
    run_id: UUID,
    project_id: UUID,
    generation_mode: GenerationMode,
    stop_reason: str,
    failure_stage: str,
    **record_kwargs: Any,
) -> None:
    """失败账本不可用时保留原始业务错误，并额外打印持久化阶段."""
    try:
        await record_shader_generation_failure(
            pool,
            run_id=run_id,
            stop_reason=stop_reason,
            **record_kwargs,
        )
    except Exception as persistence_error:
        logger.error(
            "shader.generate.failure_persistence_failed run_id=%s project_id=%s "
            "generation_mode=%s stop_reason=%s failure_stage=%s "
            "persistence_stage=outcome_transaction error_type=%s",
            run_id,
            project_id,
            generation_mode,
            stop_reason,
            failure_stage,
            type(persistence_error).__name__,
        )


async def _record_success_without_masking(
    pool: Any,
    *,
    run_id: UUID,
    project_id: UUID,
    generation_mode: GenerationMode,
    **record_kwargs: Any,
) -> None:
    """已生成 Shader 时，账本故障只告警，不覆盖可返回的成功结果."""
    try:
        await record_shader_generation_success(
            pool,
            run_id=run_id,
            **record_kwargs,
        )
    except Exception as persistence_error:
        logger.error(
            "shader.generate.success_persistence_failed run_id=%s project_id=%s "
            "generation_mode=%s stop_reason=persistence_failed "
            "failure_stage=none persistence_stage=outcome_commit error_type=%s",
            run_id,
            project_id,
            generation_mode,
            type(persistence_error).__name__,
        )


async def _start_generation_run_or_raise(pool: Any, **start_kwargs: Any) -> None:
    """创建生成 run；把数据库原始异常收敛为安全内部类型."""
    try:
        await start_shader_generation_run(pool, **start_kwargs)
    except Exception as persistence_error:
        raise _GenerationRunPersistenceError(
            type(persistence_error).__name__
        ) from persistence_error


def _procedural_review(value: dict[str, Any] | None) -> ShaderReview | None:
    """把 Critic 结构化结果收敛为现有 Review 展示契约."""
    if value is None:
        return None
    suggestions = []
    for item in value.get("recommended_changes", []):
        if not isinstance(item, dict):
            continue
        target = str(item.get("target", "目标区域"))
        direction = str(item.get("direction", "调整参数"))
        reason = str(item.get("reason", ""))
        suggestions.append(
            f"{target}：{direction}" + (f"（{reason}）" if reason else "")
        )
    return ShaderReview(
        evaluation=str(value.get("overall_assessment", "自动闭环评审已完成。")),
        suggestions=suggestions,
    )


def _scene_value(value: Any) -> dict[str, Any] | None:
    """把 Pydantic/dataclass scene 收敛为可持久化、可公开的字典."""
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if model_dump is not None:
        return cast(dict[str, Any], model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError("scene_mvp scene 必须是结构化对象。")


def _scene_trace(value: Any) -> list[dict[str, Any]]:
    """规范化最小流水线阶段 trace，避免把内部对象直接暴露给响应."""
    if value is None:
        return []
    trace: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            trace.append(dict(item))
            continue
        model_dump = getattr(item, "model_dump", None)
        if model_dump is not None:
            trace.append(cast(dict[str, Any], model_dump(mode="json")))
            continue
        if is_dataclass(item) and not isinstance(item, type):
            trace.append(asdict(item))
            continue
        raise TypeError("scene_mvp trace 必须由结构化阶段记录组成。")
    return trace


def _scene_trace_events(trace: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    """把公开阶段摘要映射为过程账本事件，不写图片或完整 GLSL."""
    events: list[dict[str, Any]] = []
    for item in trace:
        phase = str(item.get("phase") or item.get("stage") or "scene_mvp")
        status = str(item.get("status") or "completed")
        payload = {
            key: value
            for key, value in item.items()
            if key not in {"phase", "stage", "status"}
        }
        events.append(
            {
                "stage": phase,
                "event_type": f"scene_mvp_{status}",
                "payload": payload,
            }
        )
    return tuple(events)


async def execute_shader_generation(
    command: ShaderGenerationCommand,
    dependencies: ShaderGenerationDependencies,
) -> ShaderResponse:
    """执行一次完整生成用例并返回已通过公开契约校验的响应."""
    project_id = command.project_id
    run_id = command.run_id
    generation_mode = command.generation_mode
    quality_preset = command.quality_preset
    pool = dependencies.pool
    if generation_mode == "procedural_v1":
        glsl_model_name, vision_model_name = get_png_to_shader_v1_models()
    else:
        glsl_model_name = vision_model_name = "scene_mvp"
    run_started = False
    result: Any = None
    logger.info(
        "shader.generate.started run_id=%s project_id=%s generation_mode=%s "
        "quality_preset=%s image_bytes=%s database_enabled=%s",
        run_id,
        project_id,
        generation_mode,
        quality_preset,
        len(command.image),
        pool is not None,
    )

    try:
        async with dependencies.locks.hold(str(project_id)):
            if pool is not None:
                await _start_generation_run_or_raise(
                    pool,
                    run_id=run_id,
                    project_id=project_id,
                    filename=command.filename,
                    content_type=command.content_type,
                    size_bytes=len(command.image),
                    glsl_model_name=glsl_model_name,
                    vision_model_name=vision_model_name,
                    generation_mode=generation_mode,
                    quality_preset=quality_preset,
                    instruction=command.instruction,
                )
                run_started = True

            if generation_mode == "procedural_v1":
                result = await generate_procedural_shader_from_image(
                    command.image,
                    command.content_type,
                    project_id=str(project_id),
                    run_id=str(run_id),
                    quality_preset=quality_preset,
                    instruction=command.instruction,
                    service=dependencies.procedural_service,
                )
            else:
                if dependencies.min_service is None:
                    raise RuntimeError("scene_mvp service 未就绪。")
                result = await generate_scene_shader_from_image(
                    command.image,
                    command.content_type,
                    project_id=str(project_id),
                    run_id=str(run_id),
                    quality_preset=quality_preset,
                    instruction=command.instruction,
                    service=dependencies.min_service,
                )
            if result is None:
                raise RuntimeError(f"{generation_mode} 未返回结果。")
    except ProjectBusyError as exc:
        duration_ms = (time.perf_counter() - command.started_at) * 1000
        logger.warning(
            "shader.generate.project_busy run_id=%s project_id=%s "
            "generation_mode=%s stage=project_lock stop_reason=project_busy "
            "retryable=true duration_ms=%.2f",
            run_id,
            project_id,
            generation_mode,
            duration_ms,
        )
        raise _generation_error(
            status_code=409,
            message="当前项目已有任务正在执行。",
            code="project_busy",
            run_id=run_id,
            stage="project_lock",
            retryable=True,
            stop_reason="project_busy",
        ) from exc
    except _GenerationRunPersistenceError as exc:
        duration_ms = (time.perf_counter() - command.started_at) * 1000
        logger.error(
            "shader.generate.persistence_unavailable run_id=%s project_id=%s "
            "generation_mode=%s quality_preset=%s "
            "stop_reason=persistence_unavailable failure_stage=persistence "
            "persistence_stage=create_generation_run retryable=true "
            "error_type=%s duration_ms=%.2f",
            run_id,
            project_id,
            generation_mode,
            quality_preset,
            exc.error_type,
            duration_ms,
        )
        raise _generation_error(
            status_code=503,
            message="运行账本暂时不可用，请稍后重试。",
            code="persistence_unavailable",
            run_id=run_id,
            stage="persistence",
            retryable=True,
            stop_reason="persistence_unavailable",
        ) from exc
    except NoValidatedShaderError as exc:
        duration_ms = (time.perf_counter() - command.started_at) * 1000
        diagnostics = {
            **exc.diagnostics,
            "backend_duration_ms": round(duration_ms, 2),
        }
        if pool is not None and run_started:
            await _record_failure_without_masking(
                pool,
                run_id=run_id,
                project_id=project_id,
                generation_mode=generation_mode,
                stop_reason=exc.stop_reason,
                failure_stage=str(diagnostics.get("failure_stage", "unknown")),
                error=exc,
                model_calls=exc.model_calls,
                events=exc.events,
                logs=exc.logs,
                diagnostics=diagnostics,
            )
        logger.warning(
            "shader.generate.no_validated_result run_id=%s project_id=%s "
            "generation_mode=%s quality_preset=%s stop_reason=%s "
            "failure_stage=%s failure_event=%s failure_error_type=%s "
            "duration_ms=%.2f graph_elapsed_seconds=%s candidate_count=%s "
            "model_call_count=%s recorded_model_calls=%s model_latency_ms=%s "
            "compile_repair_count=%s",
            run_id,
            project_id,
            generation_mode,
            quality_preset,
            exc.stop_reason,
            diagnostics.get("failure_stage", "unknown"),
            diagnostics.get("failure_event", "unknown"),
            diagnostics.get("failure_error_type", "unknown"),
            duration_ms,
            diagnostics.get("elapsed_seconds", 0),
            diagnostics.get("candidate_count", 0),
            diagnostics.get("model_call_count", 0),
            diagnostics.get("recorded_model_calls", 0),
            diagnostics.get("model_latency_ms", 0),
            diagnostics.get("compile_repair_count", 0),
        )
        raise _no_validated_shader_error(exc, run_id=run_id) from exc
    except MemoryUnavailableError as exc:
        duration_ms = (time.perf_counter() - command.started_at) * 1000
        diagnostics = {
            "failure_stage": "memory",
            "failure_event": "memory_unavailable",
            "failure_error_type": type(exc).__name__,
            "backend_duration_ms": round(duration_ms, 2),
        }
        if pool is not None and run_started:
            await _record_failure_without_masking(
                pool,
                run_id=run_id,
                project_id=project_id,
                generation_mode=generation_mode,
                stop_reason="memory_unavailable",
                failure_stage="memory",
                error=exc,
                diagnostics=diagnostics,
            )
        logger.exception(
            "shader.generate.memory_unavailable run_id=%s project_id=%s "
            "generation_mode=%s quality_preset=%s stop_reason=memory_unavailable "
            "failure_stage=memory retryable=true duration_ms=%.2f",
            run_id,
            project_id,
            generation_mode,
            quality_preset,
            duration_ms,
        )
        raise _generation_error(
            status_code=503,
            message="任务记忆暂时不可用。",
            code="memory_unavailable",
            run_id=run_id,
            stage="memory",
            retryable=True,
            stop_reason="memory_unavailable",
        ) from exc
    except Exception as exc:
        duration_ms = (time.perf_counter() - command.started_at) * 1000
        is_timeout = (
            isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.casefold()
        )
        failure_stage = "model" if is_timeout else "pipeline"
        stop_reason = "model_timeout" if is_timeout else "internal_pipeline_error"
        diagnostics = {
            "failure_stage": failure_stage,
            "failure_event": stop_reason,
            "failure_error_type": type(exc).__name__,
            "backend_duration_ms": round(duration_ms, 2),
        }
        if pool is not None and run_started:
            await _record_failure_without_masking(
                pool,
                run_id=run_id,
                project_id=project_id,
                generation_mode=generation_mode,
                stop_reason=stop_reason,
                failure_stage=failure_stage,
                error=exc,
                diagnostics=diagnostics,
            )
        logger.error(
            "shader.generate.failed run_id=%s project_id=%s generation_mode=%s "
            "quality_preset=%s stop_reason=%s failure_stage=%s "
            "error_type=%s retryable=%s duration_ms=%.2f",
            run_id,
            project_id,
            generation_mode,
            quality_preset,
            stop_reason,
            failure_stage,
            type(exc).__name__,
            str(is_timeout).lower(),
            duration_ms,
        )
        status_code = 504 if is_timeout else 500
        message = (
            "Shader 模型阶段响应超时。"
            if is_timeout
            else "Shader 自动闭环发生内部错误。"
        )
        code = "model_timeout" if is_timeout else "internal_pipeline_error"
        raise _generation_error(
            status_code=status_code,
            message=message,
            code=code,
            run_id=run_id,
            stage=failure_stage,
            retryable=is_timeout,
            stop_reason=stop_reason,
        ) from exc

    if result is not None and generation_mode == "scene_mvp":
        artifact_base = f"/api/shader/runs/{run_id}/artifacts"
        try:
            if str(result.project_id) != str(project_id) or str(result.run_id) != str(
                run_id
            ):
                raise ValueError("scene_mvp 返回的 project_id/run_id 与请求不一致。")
            if result.renderer_path != "prepared_uniforms_v1":
                raise ValueError("scene_mvp 返回了未知 Renderer 路径。")
            scene = _scene_value(result.scene)
            trace = _scene_trace(result.trace)
            response = ShaderResponse(
                project_id=project_id,
                run_id=run_id,
                glsl=str(result.glsl),
                memory_status="ephemeral",
                generation_mode="scene_mvp",
                quality_preset=quality_preset,
                stop_reason=str(result.stop_reason),
                render_width=int(result.render_width),
                render_height=int(result.render_height),
                final_render_url=f"{artifact_base}/final-render",
                metrics_url=f"{artifact_base}/metrics",
                manifest_url=f"{artifact_base}/manifest",
                min_pipeline=ShaderMinPipelineSummary(
                    mae=(
                        float(result.current_best_mae)
                        if result.current_best_mae is not None
                        else None
                    ),
                    objective_loss=float(result.current_best_loss),
                    metric_breakdown=dict(result.metric_breakdown),
                    template_version=str(result.template_version),
                    render_count=int(result.render_count),
                    render_budget=int(result.render_budget),
                    llm_call_count=int(result.llm_call_count),
                    llm_budget=int(result.llm_budget),
                    refine_budget=int(result.refine_budget),
                    renderer_path="prepared_uniforms_v1",
                    target_mae=float(result.target_mae),
                    target_loss=float(result.target_loss),
                    target_reached=bool(result.target_reached),
                    prepare_duration_ms=float(result.prepare_duration_ms),
                    uniform_render_count=int(result.uniform_render_count),
                    uniform_render_p95_ms=float(result.uniform_render_p95_ms),
                    scene=scene,
                    trace=trace,
                ),
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - command.started_at) * 1000
            diagnostics = {
                "failure_stage": "backend_response",
                "failure_event": "response_contract_failed",
                "failure_error_type": type(exc).__name__,
                "stop_reason": getattr(result, "stop_reason", None),
                "backend_duration_ms": round(duration_ms, 2),
            }
            if pool is not None and run_started:
                await _record_failure_without_masking(
                    pool,
                    run_id=run_id,
                    project_id=project_id,
                    generation_mode="scene_mvp",
                    stop_reason=str(
                        getattr(result, "stop_reason", "response_contract_failed")
                    ),
                    failure_stage="backend_response",
                    error=exc,
                    diagnostics=diagnostics,
                )
            raise _generation_error(
                status_code=500,
                message="生成已完成，但结果格式校验失败。",
                code="response_contract_failed",
                run_id=run_id,
                stage="backend_response",
                retryable=False,
                stop_reason=str(getattr(result, "stop_reason", "unknown")),
            ) from exc

        events = _scene_trace_events(trace)
        result_summary = {
            "generation_mode": "scene_mvp",
            "status": str(result.status),
            "stop_reason": str(result.stop_reason),
            "render_width": int(result.render_width),
            "render_height": int(result.render_height),
            "current_best_mae": result.current_best_mae,
            "current_best_loss": float(result.current_best_loss),
            "metric_breakdown": dict(result.metric_breakdown),
            "template_version": str(result.template_version),
            "quality_preset": str(result.quality_preset),
            "render_count": int(result.render_count),
            "render_budget": int(result.render_budget),
            "llm_call_count": int(result.llm_call_count),
            "llm_budget": int(result.llm_budget),
            "refine_budget": int(result.refine_budget),
            "renderer_path": str(result.renderer_path),
            "target_mae": float(result.target_mae),
            "target_loss": float(result.target_loss),
            "target_reached": bool(result.target_reached),
            "prepare_duration_ms": float(result.prepare_duration_ms),
            "uniform_render_count": int(result.uniform_render_count),
            "uniform_render_p95_ms": float(result.uniform_render_p95_ms),
            "scene": scene,
            "trace": trace,
            "final_render_url": f"{artifact_base}/final-render",
            "metrics_url": f"{artifact_base}/metrics",
            "manifest_url": f"{artifact_base}/manifest",
        }
        if pool is not None:
            await _record_success_without_masking(
                pool,
                run_id=run_id,
                project_id=project_id,
                generation_mode="scene_mvp",
                model_name="scene_mvp",
                glsl_chars=len(result.glsl),
                events=events,
                result_summary=result_summary,
                record_default_model_call=False,
            )
        logger.info(
            "shader.generate.succeeded run_id=%s project_id=%s "
            "generation_mode=scene_mvp stop_reason=%s failure_stage=none "
            "render_count=%s llm_call_count=%s current_best_mae=%s "
            "renderer_path=%s uniform_render_count=%s uniform_render_p95_ms=%.2f "
            "duration_ms=%.2f",
            run_id,
            project_id,
            result.stop_reason,
            result.render_count,
            result.llm_call_count,
            result.current_best_mae,
            result.renderer_path,
            result.uniform_render_count,
            result.uniform_render_p95_ms,
            (time.perf_counter() - command.started_at) * 1000,
        )
        return response

    if result is not None:
        artifact_base = f"/api/shader/runs/{run_id}/artifacts"
        metrics_available = result.score is not None
        metrics_url = f"{artifact_base}/metrics" if metrics_available else None
        try:
            response = ShaderResponse(
                project_id=project_id,
                run_id=run_id,
                glsl=result.glsl,
                memory_status=result.memory_status,
                generation_mode="procedural_v1",
                quality_preset=cast(QualityPresetName, result.quality_preset),
                iterations=result.iterations,
                stop_reason=result.stop_reason,
                best_candidate_id=result.best_candidate_id,
                unscored_fallback=result.unscored_fallback,
                render_width=result.render_width,
                render_height=result.render_height,
                final_render_url=f"{artifact_base}/final-render",
                metrics_url=metrics_url,
                manifest_url=f"{artifact_base}/manifest",
                score=(
                    ShaderScore.model_validate(result.score)
                    if result.score is not None
                    else None
                ),
                review=_procedural_review(result.review),
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - command.started_at) * 1000
            diagnostics = {
                "failure_stage": "backend_response",
                "failure_event": "response_contract_failed",
                "failure_error_type": type(exc).__name__,
                "stop_reason": result.stop_reason,
                "model_call_count": len(result.model_calls),
                "backend_duration_ms": round(duration_ms, 2),
            }
            if pool is not None and run_started:
                await _record_failure_without_masking(
                    pool,
                    run_id=run_id,
                    project_id=project_id,
                    generation_mode="procedural_v1",
                    stop_reason=result.stop_reason,
                    failure_stage="backend_response",
                    error=exc,
                    model_calls=result.model_calls,
                    events=result.events,
                    logs=result.logs,
                    diagnostics=diagnostics,
                )
            logger.error(
                "shader.generate.response_contract_failed run_id=%s project_id=%s "
                "generation_mode=procedural_v1 quality_preset=%s "
                "stop_reason=%s failure_stage=backend_response "
                "best_candidate_id=%s error_type=%s retryable=false duration_ms=%.2f",
                run_id,
                project_id,
                result.quality_preset,
                result.stop_reason,
                result.best_candidate_id,
                type(exc).__name__,
                duration_ms,
            )
            raise _generation_error(
                status_code=500,
                message="生成已完成，但结果格式校验失败。",
                code="response_contract_failed",
                run_id=run_id,
                stage="backend_response",
                retryable=False,
                stop_reason=result.stop_reason,
            ) from exc
        if pool is not None:
            await _record_success_without_masking(
                pool,
                run_id=run_id,
                project_id=project_id,
                generation_mode="procedural_v1",
                model_name=result.glsl_model_name,
                glsl_chars=len(result.glsl),
                model_calls=result.model_calls,
                events=result.events,
                logs=result.logs,
                result_summary={
                    "generation_mode": generation_mode,
                    "quality_preset": result.quality_preset,
                    "iterations": result.iterations,
                    "stop_reason": result.stop_reason,
                    "best_candidate_id": result.best_candidate_id,
                    "unscored_fallback": result.unscored_fallback,
                    "render_width": result.render_width,
                    "render_height": result.render_height,
                    "score": result.score,
                    "metrics_available": metrics_available,
                    "final_render_url": f"{artifact_base}/final-render",
                    "metrics_url": metrics_url,
                    "manifest_url": f"{artifact_base}/manifest",
                },
            )
        total_loss = (
            f"{float(result.score.get('total_loss', 0.0)):.6f}"
            if result.score is not None
            else "unavailable"
        )
        logger.info(
            "shader.generate.succeeded run_id=%s project_id=%s "
            "generation_mode=procedural_v1 quality_preset=%s stop_reason=%s "
            "failure_stage=none best_candidate_id=%s unscored_fallback=%s "
            "iterations=%s metrics_available=%s total_loss=%s duration_ms=%.2f",
            run_id,
            project_id,
            result.quality_preset,
            result.stop_reason,
            result.best_candidate_id,
            str(result.unscored_fallback).lower(),
            result.iterations,
            str(metrics_available).lower(),
            total_loss,
            (time.perf_counter() - command.started_at) * 1000,
        )
        return response

    raise AssertionError("受控生成路径必须返回结果或类型化失败。")
