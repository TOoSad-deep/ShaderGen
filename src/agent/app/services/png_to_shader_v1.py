"""PNG 转无贴图 Shader V1 的 Agent 公共用例服务."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol, cast

from agent.app.config.model_config import SHADER_GEN_MODEL_NAME
from agent.app.graphs.png_to_shader_v1_graph import (
    DEFAULT_ARTIFACT_ROOT,
    build_default_png_to_shader_v1_graph,
    create_default_png_to_shader_v1_renderer_registry,
    png_to_shader_v1_artifact_store,
    png_to_shader_v1_checkpointer,
    png_to_shader_v1_graph,
    png_to_shader_v1_renderer_registry,
    png_to_shader_v1_store,
)
from agent.app.memory.models import MemoryStatus
from agent.app.memory.store import clear_project_memories
from agent.app.services.errors import MemoryUnavailableError
from shaderforge.contracts import QualityPreset
from shaderforge.store import LocalArtifactStore

logger = logging.getLogger("agent.png_to_shader")
SERVICE_RENDERER_CLOSE_TIMEOUT_SECONDS = 3.0

PUBLIC_ARTIFACTS = {
    "final-render": ("final/render.png", "image/png", "final-render.png"),
    "metrics": (
        "final/metrics.json",
        "application/json; charset=utf-8",
        "metrics.json",
    ),
    "manifest": (
        "final/manifest.json",
        "application/json; charset=utf-8",
        "manifest.json",
    ),
}

_FAILURE_EVENT_TYPES = {
    "compile_failed",
    "evaluation_failed",
    "model_failed",
    "renderer_failed",
    "renderer_skipped",
}


def _failure_diagnostics(
    state: dict[str, Any], final: dict[str, Any]
) -> dict[str, Any]:
    """从终态提取不含图片、GLSL、reasoning 和供应商原文的失败摘要."""
    events = tuple(item for item in state.get("events", ()) if isinstance(item, dict))
    model_calls = tuple(
        item for item in state.get("model_calls", ()) if isinstance(item, dict)
    )
    failed_event = next(
        (
            event
            for event in reversed(events)
            if str(event.get("event_type", "")) in _FAILURE_EVENT_TYPES
        ),
        None,
    )
    cleanup_failed_event = next(
        (
            event
            for event in reversed(events)
            if str(event.get("event_type", "")) == "renderer_close_failed"
        ),
        None,
    )
    last_pipeline_event = next(
        (
            event
            for event in reversed(events)
            if str(event.get("stage", "")) not in {"finalize", "memory"}
        ),
        None,
    )
    failure_payload = (
        dict(failed_event.get("payload", {})) if failed_event is not None else {}
    )
    compile_failed_event = next(
        (
            event
            for event in reversed(events)
            if str(event.get("event_type", "")) == "compile_failed"
        ),
        None,
    )
    compile_failure_payload = (
        dict(compile_failed_event.get("payload", {}))
        if compile_failed_event is not None
        else {}
    )
    structured_output_error_codes = sorted(
        {
            str(code)
            for call in model_calls
            for code in call.get("error_codes", ())
            if code
        }
    )
    shader_validation_violation_codes = sorted(
        {
            str(code)
            for code in compile_failure_payload.get("violation_codes", ())
            if code
        }
    )
    shader_validation_violations = [
        {
            "code": str(item.get("code", "unknown")),
            "severity": str(item.get("severity", "unknown")),
            "line": (
                int(item["line"])
                if isinstance(item.get("line"), int)
                and not isinstance(item.get("line"), bool)
                else None
            ),
        }
        for item in compile_failure_payload.get("violations", ())
        if isinstance(item, dict)
    ]
    compatibility_validation_codes = sorted(
        set(structured_output_error_codes) | set(shader_validation_violation_codes)
    )
    last_call = model_calls[-1] if model_calls else {}
    diagnostics: dict[str, Any] = {
        "stop_reason": str(final.get("stop_reason", "completed_with_best_effort")),
        "elapsed_seconds": round(float(final.get("elapsed_seconds", 0.0)), 3),
        "candidate_count": int(final.get("candidate_count", 0)),
        "model_call_count": int(final.get("model_call_count", len(model_calls))),
        "recorded_model_calls": len(model_calls),
        "model_latency_ms": sum(
            int(call.get("latency_ms", 0) or 0) for call in model_calls
        ),
        "compile_repair_count": int(final.get("compile_repair_count", 0)),
        "visual_refinement_count": int(final.get("visual_refinement_count", 0)),
        "failure_stage": str(
            failure_payload.get(
                "failure_stage",
                failed_event.get("stage", "unknown") if failed_event else "unknown",
            )
        ),
        "failure_event": str(failed_event.get("event_type", "unknown"))
        if failed_event
        else "unknown",
        "failure_error_type": str(failure_payload.get("error_type", "unknown")),
        "cleanup_failure_error_type": (
            str(dict(cleanup_failed_event.get("payload", {})).get("error_type"))
            if cleanup_failed_event is not None
            else None
        ),
        "last_pipeline_stage": str(last_pipeline_event.get("stage", "unknown"))
        if last_pipeline_event
        else "unknown",
        "last_pipeline_event": str(last_pipeline_event.get("event_type", "unknown"))
        if last_pipeline_event
        else "unknown",
        "last_model_role": str(last_call.get("role", "unknown")),
        "last_model_parse_status": str(last_call.get("parse_status", "unknown")),
        "structured_output_error_codes": structured_output_error_codes,
        "shader_validation_violation_codes": shader_validation_violation_codes,
        "shader_validation_violations": shader_validation_violations,
        "shader_failure_stage": str(
            compile_failure_payload.get(
                "failure_stage",
                compile_failed_event.get("stage", "unknown")
                if compile_failed_event
                else "unknown",
            )
        ),
        # 兼容旧消费者；新代码必须读取上面两个职责明确的字段。
        "validation_error_codes": compatibility_validation_codes,
        "validation_error_codes_deprecated": True,
    }
    for field in (
        "remaining_wall_seconds",
        "reserved_wall_seconds",
        "stage_elapsed_seconds",
        "timeout_seconds",
    ):
        if field in failure_payload:
            diagnostics[field] = round(float(failure_payload[field]), 3)
    for field in ("timeout_source",):
        if field in failure_payload:
            diagnostics[field] = str(failure_payload[field])
    if "attempt_count_incomplete" in failure_payload:
        diagnostics["attempt_count_incomplete"] = bool(
            failure_payload["attempt_count_incomplete"]
        )
    return diagnostics


def _normalize_score_breakdown(value: dict[str, Any]) -> dict[str, Any]:
    """兼容读取早期 Artifact 中被 JSON 编码为 pair-list 的评分映射."""
    normalized = dict(value)
    for field in (
        "roi_losses",
        "protected_region_losses",
        "effective_weights",
    ):
        field_value = normalized.get(field, {})
        if isinstance(field_value, dict):
            continue
        if isinstance(field_value, list):
            try:
                normalized[field] = {
                    str(key): float(number) for key, number in field_value
                }
            except (TypeError, ValueError):
                pass
    return normalized


class NoValidatedShaderError(RuntimeError):
    """表示闭环正常停止，但没有任何通过硬门禁的候选."""

    def __init__(self, state: dict[str, Any]) -> None:
        """保存可安全入账的终止证据，不暴露模型 reasoning."""
        final = dict(state.get("final_result", {}))
        self.stop_reason = str(final.get("stop_reason", "completed_with_best_effort"))
        self.model_calls = tuple(state.get("model_calls", ()))
        self.events = tuple(state.get("events", ()))
        self.logs = tuple(state.get("logs", ()))
        self.final_result = final
        self.diagnostics = _failure_diagnostics(state, final)
        super().__init__(f"未生成通过 WebGL1 门禁的 Shader：{self.stop_reason}")


class PublicArtifactNotFoundError(FileNotFoundError):
    """表示公开白名单中不存在请求的运行产物."""


@dataclass(frozen=True)
class PublicArtifact:
    """后端可安全返回的白名单 Artifact."""

    data: bytes
    content_type: str
    filename: str


@dataclass(frozen=True)
class PngToShaderV1Result:
    """V1 Graph 对后端暴露的稳定成功结果."""

    project_id: str
    run_id: str
    glsl: str
    memory_status: MemoryStatus
    quality_preset: str
    iterations: int
    stop_reason: str
    best_candidate_id: str
    render_width: int
    render_height: int
    score: dict[str, Any] | None
    unscored_fallback: bool
    review: dict[str, Any] | None
    glsl_model_name: str
    vision_model_name: str
    model_calls: tuple[dict[str, Any], ...] = ()
    events: tuple[dict[str, Any], ...] = ()
    logs: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ClearPngToShaderMemoryResult:
    """V1 checkpoint 与项目 Memory 清除计数."""

    deleted_memories: int


class RunResourceCleaner(Protocol):
    """Agent Service 可兜底释放的最小 run 级资源接口."""

    async def close(self, key: tuple[str, str]) -> None:
        """幂等释放指定 project/run 的资源."""
        ...


class PngToShaderV1Service:
    """持有 V1 Graph、persistence 和 Artifact 边界的服务."""

    def __init__(
        self,
        graph: Any,
        checkpointer: Any,
        store: Any,
        artifact_store: LocalArtifactStore,
        memory_status: MemoryStatus,
        renderer_registry: RunResourceCleaner | None = None,
        renderer_close_timeout_seconds: float = SERVICE_RENDERER_CLOSE_TIMEOUT_SECONDS,
    ) -> None:
        """保存一次后端生命周期内复用的依赖."""
        self.graph = graph
        self.checkpointer = checkpointer
        self.store = store
        self.artifact_store = artifact_store
        self.memory_status = memory_status
        self.renderer_registry = renderer_registry
        self.renderer_close_timeout_seconds = renderer_close_timeout_seconds

    @staticmethod
    def thread_id(project_id: str) -> str:
        """使用稳定前缀构造 V1 项目 checkpoint thread id."""
        return f"png-to-shader-v1:{project_id}"

    async def invoke(self, project_id: str, state: dict[str, Any]) -> dict[str, Any]:
        """调用 V1 Graph，映射 persistence 故障并兜底释放 run 级资源."""
        state_project_id = str(state.get("project_id", "")).strip()
        if state_project_id != project_id:
            raise ValueError("Service project_id 与 Graph State 不一致。")
        run_id = str(state.get("run_id", "")).strip()
        try:
            return cast(
                dict[str, Any],
                await self.graph.ainvoke(
                    state,
                    {"configurable": {"thread_id": self.thread_id(project_id)}},
                ),
            )
        except Exception as exc:
            module = type(exc).__module__
            if module.startswith(("psycopg", "langgraph.checkpoint")):
                raise MemoryUnavailableError("任务记忆暂时不可用。") from exc
            raise
        finally:
            if self.renderer_registry is not None and run_id:
                try:
                    await asyncio.wait_for(
                        self.renderer_registry.close((state_project_id, run_id)),
                        timeout=self.renderer_close_timeout_seconds,
                    )
                except Exception as cleanup_error:
                    logger.warning(
                        "png_to_shader.service_renderer_close_failed "
                        "project_id=%s run_id=%s error_type=%s",
                        project_id,
                        run_id,
                        type(cleanup_error).__name__,
                    )

    async def clear_memory(self, project_id: str) -> ClearPngToShaderMemoryResult:
        """清除 V1/历史 checkpoint 和该项目的共享长期 Memory."""
        try:
            await self.checkpointer.adelete_thread(self.thread_id(project_id))
            # 下线的 legacy Graph 使用裸 project_id 作为 thread id；继续清理它，
            # 避免升级后项目删除操作遗留无入口可访问的历史 checkpoint。
            await self.checkpointer.adelete_thread(project_id)
            deleted = await clear_project_memories(self.store, project_id)
        except Exception as exc:
            raise MemoryUnavailableError("清除 PNG-to-Shader 记忆失败。") from exc
        return ClearPngToShaderMemoryResult(deleted_memories=deleted)

    def read_public_artifact(self, run_id: str, artifact_name: str) -> PublicArtifact:
        """只读取固定白名单文件，不接受任意相对或绝对路径."""
        descriptor = PUBLIC_ARTIFACTS.get(artifact_name)
        if descriptor is None:
            raise PublicArtifactNotFoundError("Artifact 不在公开白名单中。")
        relative_path, content_type, filename = descriptor
        try:
            run = self.artifact_store.resolve_run(run_id)
            data = run.read_bytes(relative_path)
        except FileNotFoundError as exc:
            raise PublicArtifactNotFoundError("未找到运行 Artifact。") from exc
        return PublicArtifact(data=data, content_type=content_type, filename=filename)


def create_png_to_shader_v1_service(
    *,
    checkpointer: Any,
    store: Any,
    memory_status: MemoryStatus,
    artifact_store: LocalArtifactStore | None = None,
) -> PngToShaderV1Service:
    """使用外部 persistence 创建后端可注入的 V1 服务."""
    artifacts = artifact_store or LocalArtifactStore(DEFAULT_ARTIFACT_ROOT)
    renderer_registry = create_default_png_to_shader_v1_renderer_registry()
    graph = build_default_png_to_shader_v1_graph(
        artifact_store=artifacts,
        checkpointer=checkpointer,
        store=store,
        renderer_registry=renderer_registry,
    )
    return PngToShaderV1Service(
        graph,
        checkpointer,
        store,
        artifacts,
        memory_status,
        renderer_registry,
    )


default_png_to_shader_v1_service = PngToShaderV1Service(
    png_to_shader_v1_graph,
    png_to_shader_v1_checkpointer,
    png_to_shader_v1_store,
    png_to_shader_v1_artifact_store,
    "ephemeral",
    png_to_shader_v1_renderer_registry,
)


def png_to_shader_v1_models() -> tuple[str, str]:
    """返回 V1 Author 与 Visual 角色当前模型名."""
    return SHADER_GEN_MODEL_NAME, SHADER_GEN_MODEL_NAME


async def generate_png_to_shader_v1(
    image: bytes,
    content_type: str,
    *,
    project_id: str,
    run_id: str,
    quality_preset: QualityPreset | str,
    instruction: str,
    service: PngToShaderV1Service = default_png_to_shader_v1_service,
) -> PngToShaderV1Result:
    """执行自动 render-evaluate-review-refine 闭环并返回历史最佳结果."""
    preset = (
        quality_preset
        if isinstance(quality_preset, QualityPreset)
        else QualityPreset(quality_preset)
    )
    state = await service.invoke(
        project_id,
        {
            "project_id": project_id,
            "run_id": run_id,
            "image": image,
            "content_type": content_type,
            "quality_preset": preset.value,
            "instruction": instruction,
            "memory_status": service.memory_status,
            "model_calls": (),
            "events": (),
            "logs": (),
        },
    )
    final = dict(state.get("final_result", {}))
    glsl = final.get("glsl")
    score_value = final.get("score_breakdown")
    score = (
        _normalize_score_breakdown(score_value)
        if isinstance(score_value, dict)
        else score_value
    )
    candidate_id = final.get("candidate_id")
    if not final.get("success") or not isinstance(glsl, str):
        raise NoValidatedShaderError(state)
    if score is not None and not isinstance(score, dict):
        raise RuntimeError("V1 score_breakdown 必须是 object 或 null。")
    unscored_fallback_value = final.get("unscored_fallback", False)
    if not isinstance(unscored_fallback_value, bool):
        raise RuntimeError("V1 unscored_fallback 必须是 bool。")
    if not isinstance(candidate_id, str):
        raise RuntimeError("V1 成功结果缺少 candidate_id。")
    review_value = state.get("visual_review")
    review = (
        dict(review_value)
        if isinstance(review_value, dict)
        and str(review_value.get("candidate_id", "")) == candidate_id
        else None
    )
    return PngToShaderV1Result(
        project_id=project_id,
        run_id=run_id,
        glsl=glsl,
        memory_status=state.get("memory_status", service.memory_status),
        quality_preset=preset.value,
        iterations=int(final.get("visual_refinement_count", 0)),
        stop_reason=str(final["stop_reason"]),
        best_candidate_id=candidate_id,
        render_width=int(final["render_width"]),
        render_height=int(final["render_height"]),
        score=score,
        unscored_fallback=unscored_fallback_value,
        review=review,
        glsl_model_name=SHADER_GEN_MODEL_NAME,
        vision_model_name=SHADER_GEN_MODEL_NAME,
        model_calls=tuple(state.get("model_calls", ())),
        events=tuple(state.get("events", ())),
        logs=tuple(state.get("logs", ())),
    )
