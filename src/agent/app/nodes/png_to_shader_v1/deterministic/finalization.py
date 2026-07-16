"""PNG 转 Shader V1 的兜底选择与资源收口节点."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from shaderforge.contracts import (
    StopReason,
)
from shaderforge.evaluation import (
    CandidateRecord,
)
from shaderforge.store import LocalArtifactStore

from .runtime import (
    RENDERER_CLOSE_TIMEOUT_SECONDS,
    Clock,
    RunNode,
    RunRendererRegistry,
    _elapsed_seconds,
    _read_json,
    _record,
    _run_key,
    _run_store,
    logger,
)


def _latest_validated_fallback(state: Mapping[str, Any]) -> CandidateRecord | None:
    """返回最近通过静态检查与真实 WebGL 渲染、但可能未评分的候选."""
    for value in reversed(tuple(state.get("candidate_records", ()))):
        candidate = _record(value)
        if (
            candidate.hard_constraints_passed
            and candidate.render_ref is not None
            and candidate.render_sha256 is not None
        ):
            return candidate
    return None


def make_finalize_png_to_shader_v1_node(
    artifact_store: LocalArtifactStore,
    renderer_registry: RunRendererRegistry,
    *,
    clock: Clock,
) -> RunNode:
    """创建永远从 current_best Artifact 组装最终结果的节点."""

    async def finalize(state: Mapping[str, Any]) -> dict[str, Any]:
        store = _run_store(artifact_store, state)
        best_raw = state.get("current_best_record")
        best = None if best_raw is None else _record(best_raw)
        unscored_fallback = False
        if best is None:
            best = _latest_validated_fallback(state)
            unscored_fallback = best is not None and best.score_summary is None
        reason = str(
            state.get("stop_reason") or StopReason.COMPLETED_WITH_BEST_EFFORT.value
        )
        if unscored_fallback:
            reason = StopReason.COMPLETED_WITH_BEST_EFFORT.value
        result: dict[str, Any]
        final_render: bytes | None = None
        if best is None:
            result = {
                "success": False,
                "candidate_id": None,
                "glsl": None,
                "glsl_sha256": None,
                "render_ref": None,
                "render_sha256": None,
                "score_breakdown": None,
            }
        else:
            if (
                not best.hard_constraints_passed
                or best.render_ref is None
                or best.render_sha256 is None
            ):
                raise RuntimeError("finalize 拒绝不完整的 current_best。")
            glsl_bytes = store.read_bytes(best.glsl_ref)
            final_render = store.read_bytes(best.render_ref)
            if sha256(glsl_bytes).hexdigest() != best.glsl_sha256:
                raise RuntimeError("finalize 读取的 GLSL hash 不一致。")
            if sha256(final_render).hexdigest() != best.render_sha256:
                raise RuntimeError("finalize 读取的 Render hash 不一致。")
            metrics: dict[str, Any] | None = None
            if best.score_summary is not None:
                if best.metrics_ref is None:
                    raise RuntimeError("已评分 current_best 缺少 metrics Artifact。")
                metrics = _read_json(store, best.metrics_ref)
                if float(metrics["total_loss"]) != best.score_summary.total_loss:
                    raise RuntimeError(
                        "finalize 读取的 metrics 与 current_best 不一致。"
                    )
            final_glsl_ref = store.write_bytes(
                "final/shader.frag",
                glsl_bytes,
                content_type="text/x-glsl; charset=utf-8",
            )
            final_render_ref = store.write_bytes(
                "final/render.png",
                final_render,
                content_type="image/png",
            )
            final_metrics_ref = (
                store.write_json("final/metrics.json", metrics)
                if metrics is not None
                else None
            )
            result = {
                "success": True,
                "candidate_id": best.candidate_id,
                "glsl": glsl_bytes.decode("utf-8"),
                "glsl_sha256": best.glsl_sha256,
                "glsl_ref": final_glsl_ref.relative_path,
                "render_ref": final_render_ref.relative_path,
                "render_sha256": best.render_sha256,
                "metrics_ref": (
                    final_metrics_ref.relative_path
                    if final_metrics_ref is not None
                    else None
                ),
                "score_breakdown": metrics,
                "unscored_fallback": unscored_fallback,
            }

        measurements = state["target_measurements"]
        if isinstance(measurements, Mapping):
            render_width = int(measurements["analysis_width"])
            render_height = int(measurements["analysis_height"])
        else:
            render_width = int(measurements.analysis_width)
            render_height = int(measurements.analysis_height)
        result.update(
            {
                "schema_version": 1,
                "project_id": str(state["project_id"]),
                "run_id": str(state["run_id"]),
                "stop_reason": reason,
                "candidate_count": len(tuple(state.get("candidate_records", ()))),
                "model_call_count": int(state.get("model_call_count", 0)),
                "compile_repair_count": int(state.get("compile_repair_count", 0)),
                "visual_refinement_count": int(state.get("visual_refinement_count", 0)),
                "no_improvement_count": int(state.get("no_improvement_count", 0)),
                "render_width": render_width,
                "render_height": render_height,
                "elapsed_seconds": max(
                    0.0,
                    clock() - float(state["started_at"]),
                ),
            }
        )
        manifest_value = {key: value for key, value in result.items() if key != "glsl"}
        manifest = store.write_json("final/manifest.json", manifest_value)
        result["manifest_ref"] = manifest.relative_path
        events = tuple(state.get("events", ()))
        if unscored_fallback and best is not None:
            events = (
                *events,
                {
                    "stage": "finalize",
                    "event_type": "validated_candidate_fallback_selected",
                    "payload": {
                        "candidate_id": best.candidate_id,
                        "reason": "evaluation_unavailable",
                        "elapsed_seconds": round(_elapsed_seconds(state, clock), 3),
                    },
                },
            )
        try:
            await asyncio.wait_for(
                renderer_registry.close(_run_key(state)),
                timeout=RENDERER_CLOSE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            events = (
                *events,
                {
                    "stage": "finalize",
                    "event_type": "renderer_close_failed",
                    "payload": {
                        "error_type": "TimeoutError",
                        "timeout_seconds": RENDERER_CLOSE_TIMEOUT_SECONDS,
                        "elapsed_seconds": round(_elapsed_seconds(state, clock), 3),
                    },
                },
            )
        except Exception as exc:
            events = (
                *events,
                {
                    "stage": "finalize",
                    "event_type": "renderer_close_failed",
                    "payload": {"error_type": type(exc).__name__},
                },
            )
        logger.info(
            "shader.pipeline.finalized run_id=%s project_id=%s success=%s "
            "stop_reason=%s candidate_id=%s candidate_count=%s model_call_count=%s "
            "elapsed_seconds=%.3f",
            state["run_id"],
            state["project_id"],
            result["success"],
            reason,
            result["candidate_id"],
            result["candidate_count"],
            result["model_call_count"],
            result["elapsed_seconds"],
        )
        return {
            "phase": "finalized",
            "stop_reason": reason,
            "final_result": result,
            "final_manifest_ref": manifest.relative_path,
            "rendered_image": final_render or b"",
            "events": (
                *events,
                {
                    "stage": "finalize",
                    "event_type": "run_finalized",
                    "payload": {
                        "success": result["success"],
                        "candidate_id": result["candidate_id"],
                        "stop_reason": reason,
                        "manifest_ref": manifest.relative_path,
                    },
                },
            ),
        }

    return finalize
