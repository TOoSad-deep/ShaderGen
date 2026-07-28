"""scene_mvp 生成用例编排：锁、过程总账、Agent 调用和响应契约."""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Literal, Protocol, cast
from uuid import UUID

from backend.app.schemas.shader import (
    QualityPresetName,
    ShaderEngineAttemptSummary,
    ShaderEngineRunSummary,
    ShaderGraphShadowSummary,
    ShaderMinPipelineSummary,
    ShaderResponse,
    ShaderShadowSubmissionSummary,
)
from backend.app.services.agent_process_store import (
    record_shader_generation_failure,
    record_shader_generation_success,
    start_shader_generation_run,
)
from backend.app.services.engine_rollout import (
    EngineResponseContractFailure,
    ParentRunFailure,
)
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
    """一次生成请求在 HTTP 校验后的稳定输入."""

    image: bytes
    filename: str | None
    content_type: str
    project_id: UUID
    run_id: UUID
    quality_preset: QualityPresetName
    instruction: str
    started_at: float


class ProductionShadowSubmitter(Protocol):
    """生成用例依赖的最小非阻塞 shadow 提交边界."""

    def submit(
        self,
        *,
        project_id: str,
        parent_run_id: UUID | str,
        image: bytes,
        content_type: str,
        instruction: str,
    ) -> dict[str, Any]:
        """提交一次非权威 shadow；实现不得等待队列容量."""


@dataclass(frozen=True)
class ShaderGenerationDependencies:
    """由 Backend 应用生命周期注入的生成用例依赖."""

    pool: Any
    min_service: Any | None
    locks: ProjectLockRegistry
    progress: RunProgressRegistry | None = None
    production_shadow: ProductionShadowSubmitter | None = None
    engine_rollout_service: Any | None = None


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
    """表示生成 run 总账创建失败."""

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


async def _start_generation_run_or_raise(pool: Any, **kwargs: Any) -> None:
    """创建生成 run；把数据库原始异常收敛为安全内部类型."""
    try:
        await start_shader_generation_run(pool, **kwargs)
    except Exception as exc:
        raise _GenerationRunPersistenceError(type(exc).__name__) from exc


async def _record_failure_without_masking(
    pool: Any,
    *,
    run_id: UUID,
    project_id: UUID,
    stop_reason: str,
    failure_stage: str,
    **kwargs: Any,
) -> None:
    """失败账本不可用时保留原始业务错误."""
    try:
        await record_shader_generation_failure(
            pool,
            run_id=run_id,
            stop_reason=stop_reason,
            **kwargs,
        )
    except Exception as exc:
        logger.error(
            "shader.generate.failure_persistence_failed run_id=%s project_id=%s "
            "generation_mode=%s stop_reason=%s failure_stage=%s error_type=%s",
            run_id,
            project_id,
            GENERATION_MODE,
            stop_reason,
            failure_stage,
            type(exc).__name__,
        )


async def _record_success_without_masking(
    pool: Any,
    *,
    run_id: UUID,
    project_id: UUID,
    **kwargs: Any,
) -> None:
    """已生成 Shader 时，账本故障只告警，不覆盖成功结果."""
    try:
        await record_shader_generation_success(pool, run_id=run_id, **kwargs)
    except Exception as exc:
        logger.error(
            "shader.generate.success_persistence_failed run_id=%s project_id=%s "
            "generation_mode=%s error_type=%s",
            run_id,
            project_id,
            GENERATION_MODE,
            type(exc).__name__,
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
    """规范化最小流水线阶段 trace."""
    trace: list[dict[str, Any]] = []
    for item in value or ():
        if isinstance(item, dict):
            trace.append(dict(item))
        elif (model_dump := getattr(item, "model_dump", None)) is not None:
            trace.append(cast(dict[str, Any], model_dump(mode="json")))
        elif is_dataclass(item) and not isinstance(item, type):
            trace.append(asdict(item))
        else:
            raise TypeError("scene_mvp trace 必须由结构化阶段记录组成。")
    return trace


def _scene_trace_events(trace: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    """把公开阶段摘要映射为过程账本事件."""
    return tuple(
        {
            "stage": str(item.get("phase") or item.get("stage") or GENERATION_MODE),
            "event_type": f"scene_mvp_{item.get('status') or 'completed'}",
            "payload": {
                key: value
                for key, value in item.items()
                if key not in {"phase", "stage", "status"}
            },
        }
        for item in trace
    )


def _legacy_authority_engine_run(
    *,
    run_id: UUID,
    shadow_submission: dict[str, Any],
) -> ShaderEngineRunSummary:
    """为未进入 canary 协调器的旧权威路径补充只读执行来源."""
    return ShaderEngineRunSummary(
        policy_id=str(shadow_submission["policy_id"]),
        policy_sha256=str(shadow_submission["policy_sha256"]),
        configured_stage=str(shadow_submission["configured_stage"]),
        stage=str(shadow_submission["effective_stage"]),
        bucket=(
            int(shadow_submission["bucket"])
            if isinstance(shadow_submission.get("bucket"), int)
            else None
        ),
        selected_attempt_id=str(run_id),
        attempt_refs=[
            ShaderEngineAttemptSummary(
                attempt_id=str(run_id),
                engine="shader_graph_v1",
                representation="shader_document_v1",
                status="succeeded",
                failure_code=None,
            )
        ],
        shadow_submission=ShaderShadowSubmissionSummary.model_validate(
            shadow_submission
        ),
    )


async def execute_shader_generation(
    command: ShaderGenerationCommand,
    dependencies: ShaderGenerationDependencies,
) -> ShaderResponse:
    """执行一次 scene_mvp 生成并返回公开契约."""
    project_id = command.project_id
    run_id = command.run_id
    quality_preset = command.quality_preset
    pool = dependencies.pool
    progress = dependencies.progress
    run_started = False
    result: Any = None
    succeeded = False
    terminal_stop_reason: str | None = None

    if progress is not None:
        try:
            progress.begin(
                str(run_id),
                project_id=str(project_id),
                generation_mode=GENERATION_MODE,
                quality_preset=quality_preset,
            )
        except ValueError as exc:
            raise _generation_error(
                status_code=409,
                message="相同 run_id 的运行正在执行中。",
                code="run_conflict",
                run_id=run_id,
                stage="run_registry",
                retryable=False,
                stop_reason="run_conflict",
            ) from exc

    logger.info(
        "shader.generate.started run_id=%s project_id=%s generation_mode=%s "
        "quality_preset=%s image_bytes=%s database_enabled=%s",
        run_id,
        project_id,
        GENERATION_MODE,
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
                    glsl_model_name=GENERATION_MODE,
                    vision_model_name=GENERATION_MODE,
                    generation_mode=GENERATION_MODE,
                    quality_preset=quality_preset,
                    instruction=command.instruction,
                )
                run_started = True
            generation_service = (
                dependencies.engine_rollout_service or dependencies.min_service
            )
            if generation_service is None:
                raise RuntimeError("scene_mvp service 未就绪。")

            def publish(event: dict[str, Any], render: bytes | None) -> None:
                if progress is None:
                    return
                if render is not None:
                    progress.publish_render(str(run_id), render)
                progress.publish(str(run_id), event)

            result = await generate_scene_shader_from_image(
                command.image,
                command.content_type,
                project_id=str(project_id),
                run_id=str(run_id),
                quality_preset=quality_preset,
                instruction=command.instruction,
                service=generation_service,
                on_progress=publish if progress is not None else None,
            )
            succeeded = True
    except ProjectBusyError as exc:
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
        logger.error(
            "shader.generate.persistence_unavailable run_id=%s error_type=%s",
            run_id,
            exc.error_type,
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
    except ShaderGenerationUseCaseError:
        raise
    except EngineResponseContractFailure as exc:
        terminal_stop_reason = exc.code
        contract_diagnostics = {
            "failure_stage": "response_contract",
            "failure_event": exc.code,
            "failure_error_type": type(exc).__name__,
            "contract_field": exc.field,
            "backend_duration_ms": round(
                (time.perf_counter() - command.started_at) * 1000,
                2,
            ),
        }
        if pool is not None and run_started:
            await _record_failure_without_masking(
                pool,
                run_id=run_id,
                project_id=project_id,
                stop_reason=exc.code,
                failure_stage="response_contract",
                error=exc,
                diagnostics=contract_diagnostics,
            )
        logger.error(
            "shader.generate.engine_response_contract_failed run_id=%s "
            "project_id=%s contract_field=%s",
            run_id,
            project_id,
            exc.field,
        )
        raise _generation_error(
            status_code=500,
            message="Shader 引擎返回了无效的公开响应契约。",
            code="response_contract_failed",
            run_id=run_id,
            stage="response_contract",
            retryable=False,
            stop_reason=exc.code,
        ) from exc
    except ParentRunFailure as exc:
        terminal_stop_reason = exc.code
        attempt_refs = [item.to_dict() for item in exc.attempt_refs]
        rollout_diagnostics = {
            "failure_stage": "engine_rollout",
            "failure_event": exc.code,
            "failure_error_type": type(exc).__name__,
            "attempt_refs": attempt_refs,
            "backend_duration_ms": round(
                (time.perf_counter() - command.started_at) * 1000,
                2,
            ),
        }
        if progress is not None:
            progress.publish(
                str(run_id),
                {
                    "node": "engine_rollout",
                    "phase": "engine_failed",
                    "status": "failed",
                    "failure_code": exc.code,
                    "attempt_refs": attempt_refs,
                },
            )
        if pool is not None and run_started:
            await _record_failure_without_masking(
                pool,
                run_id=run_id,
                project_id=project_id,
                stop_reason=exc.code,
                failure_stage="engine_rollout",
                error=exc,
                diagnostics=rollout_diagnostics,
            )
        logger.error(
            "shader.generate.engine_rollout_failed run_id=%s project_id=%s "
            "generation_mode=%s quality_preset=%s failure_code=%s "
            "attempt_count=%s",
            run_id,
            project_id,
            GENERATION_MODE,
            quality_preset,
            exc.code,
            len(attempt_refs),
        )
        raise _generation_error(
            status_code=502,
            message="Shader 引擎执行失败，未发布父运行结果。",
            code=exc.code,
            run_id=run_id,
            stage="engine_rollout",
            retryable=exc.code
            in {"direct_attempts_failed", "shader_graph_attempt_failed"},
            stop_reason=exc.code,
        ) from exc
    except Exception as exc:
        duration_ms = (time.perf_counter() - command.started_at) * 1000
        # 只认标准 timeout 类型；异常类名里的字符串不能作为协议，否则业务异常
        # 可能被误报为可重试的模型超时。
        is_timeout = isinstance(exc, TimeoutError)
        stage = "model" if is_timeout else "pipeline"
        stop_reason = "model_timeout" if is_timeout else "internal_pipeline_error"
        diagnostics: dict[str, Any] = {
            "failure_stage": stage,
            "failure_event": stop_reason,
            "failure_error_type": type(exc).__name__,
            "backend_duration_ms": round(duration_ms, 2),
        }
        if progress is not None:
            snapshot = progress.diagnostic_snapshot(str(run_id))
            if snapshot:
                diagnostics["progress_snapshot"] = snapshot
        if pool is not None and run_started:
            await _record_failure_without_masking(
                pool,
                run_id=run_id,
                project_id=project_id,
                stop_reason=stop_reason,
                failure_stage=stage,
                error=exc,
                diagnostics=diagnostics,
            )
        logger.error(
            "shader.generate.failed run_id=%s project_id=%s generation_mode=%s "
            "quality_preset=%s stop_reason=%s failure_stage=%s error_type=%s",
            run_id,
            project_id,
            GENERATION_MODE,
            quality_preset,
            stop_reason,
            stage,
            type(exc).__name__,
        )
        raise _generation_error(
            status_code=504 if is_timeout else 500,
            message="Shader 模型阶段响应超时。"
            if is_timeout
            else "Shader 最小管线发生内部错误。",
            code="model_timeout" if is_timeout else "internal_pipeline_error",
            run_id=run_id,
            stage=stage,
            retryable=is_timeout,
            stop_reason=stop_reason,
        ) from exc
    finally:
        if progress is not None:
            progress.finish(
                str(run_id),
                "succeeded" if succeeded else "failed",
                terminal_stop_reason
                or str(getattr(result, "stop_reason", "") or "")
                or None,
            )

    artifact_base = f"/api/shader/runs/{run_id}/artifacts"
    try:
        if str(result.project_id) != str(project_id) or str(result.run_id) != str(
            run_id
        ):
            raise ValueError("scene_mvp 返回的 project_id/run_id 与请求不一致。")
        if result.renderer_path not in {
            "prepared_uniforms_v1",
            "compiled_graph_program_cache_v1",
            "direct_program_spec_v1",
        }:
            raise ValueError("scene_mvp 返回了未知 Renderer 路径。")
        scene = _scene_value(result.scene)
        trace = _scene_trace(result.trace)
        raw_shader_graph_shadow = getattr(result, "shader_graph_shadow", None)
        shader_graph_shadow = (
            ShaderGraphShadowSummary.model_validate(raw_shader_graph_shadow)
            if isinstance(raw_shader_graph_shadow, dict)
            else None
        )
        raw_engine = getattr(result, "engine", None)
        raw_representation = getattr(result, "representation", None)
        raw_engine_run = getattr(result, "engine_run", None)
        engine_run = (
            ShaderEngineRunSummary.model_validate(raw_engine_run)
            if isinstance(raw_engine_run, dict)
            else None
        )
        response = ShaderResponse(
            project_id=project_id,
            run_id=run_id,
            glsl=str(result.glsl),
            generation_mode=GENERATION_MODE,
            quality_preset=quality_preset,
            engine=raw_engine,
            representation=raw_representation,
            engine_run=engine_run,
            stop_reason=str(result.stop_reason),
            render_width=int(result.render_width),
            render_height=int(result.render_height),
            final_render_url=f"{artifact_base}/final-render",
            metrics_url=f"{artifact_base}/metrics",
            manifest_url=f"{artifact_base}/manifest",
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
                run_classification=result.run_classification,
                experiment_id=result.experiment_id,
                config_fingerprint=str(result.config_fingerprint),
                report_schema_version=str(result.report_schema_version),
                patch_candidate_draw_budget=int(result.patch_candidate_draw_budget),
                patch_evidence=[dict(item) for item in result.patch_evidence],
                renderer_path=result.renderer_path,
                target_mae=float(result.target_mae),
                target_loss=float(result.target_loss),
                target_reached=bool(result.target_reached),
                prepare_duration_ms=float(result.prepare_duration_ms),
                uniform_render_count=int(result.uniform_render_count),
                uniform_render_p95_ms=float(result.uniform_render_p95_ms),
                scene=scene,
                trace=trace,
                shader_graph_shadow=shader_graph_shadow,
            ),
        )
    except Exception as exc:
        if pool is not None and run_started:
            await _record_failure_without_masking(
                pool,
                run_id=run_id,
                project_id=project_id,
                stop_reason=str(
                    getattr(result, "stop_reason", "response_contract_failed")
                ),
                failure_stage="backend_response",
                error=exc,
                diagnostics={"failure_error_type": type(exc).__name__},
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

    # 只有权威 shader_graph_v1 已成功且公开响应契约已完整构造后才允许排队。
    # submit 是同步 put_nowait；此处已离开 project lock，shadow 永不阻塞产品路径。
    shadow_submission: dict[str, Any] | None = None
    if (
        dependencies.engine_rollout_service is None
        and dependencies.production_shadow is not None
    ):
        try:
            submitted = dependencies.production_shadow.submit(
                project_id=str(project_id),
                parent_run_id=run_id,
                image=command.image,
                content_type=command.content_type,
                instruction=command.instruction,
            )
            if isinstance(submitted, dict):
                shadow_submission = submitted
                logger.info(
                    "shader.shadow.submission parent_run_id=%s project_id=%s "
                    "status=%s reason=%s attempt_id=%s bucket=%s",
                    run_id,
                    project_id,
                    submitted.get("status"),
                    submitted.get("reason"),
                    submitted.get("attempt_id"),
                    submitted.get("bucket"),
                )
        except Exception as exc:
            logger.error(
                "shader.shadow.submission_failed parent_run_id=%s project_id=%s "
                "error_type=%s",
                run_id,
                project_id,
                type(exc).__name__,
            )
    if shadow_submission is not None:
        try:
            response.engine = "shader_graph_v1"
            response.representation = "shader_document_v1"
            response.engine_run = _legacy_authority_engine_run(
                run_id=run_id,
                shadow_submission=shadow_submission,
            )
        except (KeyError, TypeError, ValueError):
            logger.error(
                "shader.engine_summary.invalid parent_run_id=%s project_id=%s",
                run_id,
                project_id,
            )

    result_summary = {
        "generation_mode": GENERATION_MODE,
        "status": str(result.status),
        "stop_reason": str(result.stop_reason),
        "quality_preset": str(result.quality_preset),
        "render_width": int(result.render_width),
        "render_height": int(result.render_height),
        "current_best_mae": float(result.current_best_mae),
        "current_best_loss": float(result.current_best_loss),
        "metric_breakdown": dict(result.metric_breakdown),
        "template_version": str(result.template_version),
        "render_count": int(result.render_count),
        "render_budget": int(result.render_budget),
        "llm_call_count": int(result.llm_call_count),
        "llm_budget": int(result.llm_budget),
        "refine_budget": int(result.refine_budget),
        "run_classification": str(result.run_classification),
        "experiment_id": result.experiment_id,
        "config_fingerprint": str(result.config_fingerprint),
        "report_schema_version": str(result.report_schema_version),
        "patch_candidate_draw_budget": int(result.patch_candidate_draw_budget),
        "patch_evidence": [dict(item) for item in result.patch_evidence],
        "renderer_path": str(result.renderer_path),
        "target_mae": float(result.target_mae),
        "target_loss": float(result.target_loss),
        "target_reached": bool(result.target_reached),
        "prepare_duration_ms": float(result.prepare_duration_ms),
        "uniform_render_count": int(result.uniform_render_count),
        "uniform_render_p95_ms": float(result.uniform_render_p95_ms),
        "scene": scene,
        "trace": trace,
        "shader_graph_shadow": (
            dict(result.shader_graph_shadow)
            if isinstance(getattr(result, "shader_graph_shadow", None), dict)
            else None
        ),
        "final_render_url": f"{artifact_base}/final-render",
        "metrics_url": f"{artifact_base}/metrics",
        "manifest_url": f"{artifact_base}/manifest",
        "engine": response.engine,
        "representation": response.representation,
        "engine_run": (
            response.engine_run.model_dump(mode="json")
            if response.engine_run is not None
            else None
        ),
    }
    if pool is not None:
        await _record_success_without_masking(
            pool,
            run_id=run_id,
            project_id=project_id,
            model_name=GENERATION_MODE,
            glsl_chars=len(result.glsl),
            events=_scene_trace_events(trace),
            result_summary=result_summary,
            record_default_model_call=False,
        )
    logger.info(
        "shader.generate.succeeded run_id=%s project_id=%s generation_mode=%s "
        "stop_reason=%s render_count=%s llm_call_count=%s duration_ms=%.2f",
        run_id,
        project_id,
        GENERATION_MODE,
        result.stop_reason,
        result.render_count,
        result.llm_call_count,
        (time.perf_counter() - command.started_at) * 1000,
    )
    return response
