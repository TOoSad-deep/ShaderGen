"""最小 scene Graph 的确定性工作节点。."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from typing import Any, cast

import numpy as np
from langchain_core.messages import SystemMessage
from PIL import Image

from agent.app.contracts.llm import LLMGateway
from agent.app.contracts.png_to_shader_min import (
    MinAuthorPatch,
    apply_min_author_patch,
)
from agent.app.messages.png_to_shader_v1 import (
    labeled_image_parts,
    multimodal_human_message,
    text_part,
)
from agent.app.nodes.png_to_shader_min.model_author import (
    MIN_AUTHOR_INITIAL_PROMPT,
    MIN_AUTHOR_REFINE_PROMPT,
    effective_llm_budget,
    invoke_min_author,
    remaining_llm_calls,
)
from agent.app.parsers.png_to_shader_min import (
    min_author_patch_json_schema,
    parse_min_author_patch,
    parse_min_scene,
)
from shaderforge.evaluation import rgb_mae
from shaderforge.generation import (
    bake_min_uniforms,
    materialize_min_shader,
)
from shaderforge.optimization import propose_min_scene_candidates
from shaderforge.public import MinScene, perceive_min_target
from shaderforge.rendering import (
    PREPARED_RENDERER_PATH,
    PlaywrightWebGL1Renderer,
    PreparedWebGL1Renderer,
)
from shaderforge.store import LocalArtifactStore

RendererFactory = Callable[[], PlaywrightWebGL1Renderer]


def _trace(
    state: dict[str, Any],
    phase: str,
    message: str,
    *,
    status: str = "completed",
    **details: Any,
) -> tuple[dict[str, Any], ...]:
    return (
        *tuple(state.get("trace", ())),
        {"phase": phase, "status": status, "message": message, **details},
    )


@dataclass(frozen=True)
class _PreparedEntry:
    """一个 run 内固定的 prepared program 及其不变签名."""

    signature: tuple[Any, ...]
    prepared: PreparedWebGL1Renderer


class MinRendererRegistry:
    """按 project/run 隔离并复用 Renderer。."""

    def __init__(self, factory: RendererFactory = PlaywrightWebGL1Renderer) -> None:
        """保存惰性 Renderer 工厂。."""
        self._factory = factory
        self._renderers: dict[tuple[str, str], PlaywrightWebGL1Renderer] = {}
        self._prepared: dict[tuple[str, str], _PreparedEntry] = {}

    def get(self, project_id: str, run_id: str) -> PlaywrightWebGL1Renderer:
        """获取或创建指定 run 的 Renderer。."""
        key = (project_id, run_id)
        renderer = self._renderers.get(key)
        if renderer is None:
            renderer = self._factory()
            self._renderers[key] = renderer
        return renderer

    async def prepare(
        self,
        project_id: str,
        run_id: str,
        fragment_source: str,
        width: int,
        height: int,
        uniform_schema: dict[str, Any],
    ) -> PreparedWebGL1Renderer:
        """为 run 惰性准备唯一 program，后续候选只复用它."""
        key = (project_id, run_id)
        signature = (
            fragment_source,
            width,
            height,
            tuple(sorted((name, spec.type) for name, spec in uniform_schema.items())),
        )
        entry = self._prepared.get(key)
        if entry is not None:
            if entry.signature != signature:
                raise RuntimeError("scene_mvp run 内 prepared program 签名发生了变化。")
            return entry.prepared
        renderer = self.get(project_id, run_id)
        prepared = await renderer.prepare(
            fragment_source,
            width,
            height,
            uniform_schema,
        )
        self._prepared[key] = _PreparedEntry(signature, prepared)
        return prepared

    def metrics(self, project_id: str, run_id: str) -> dict[str, float | int | str]:
        """返回 run 内 prepared 路径的公开可观测摘要."""
        entry = self._prepared.get((project_id, run_id))
        if entry is None:
            return {
                "renderer_path": PREPARED_RENDERER_PATH,
                "prepare_duration_ms": 0.0,
                "uniform_render_count": 0,
                "uniform_render_p95_ms": 0.0,
            }
        durations = sorted(entry.prepared.render_durations_ms)
        p95 = (
            durations[max(0, math.ceil(len(durations) * 0.95) - 1)]
            if durations
            else 0.0
        )
        return {
            "renderer_path": PREPARED_RENDERER_PATH,
            "prepare_duration_ms": entry.prepared.prepare_duration_ms,
            "uniform_render_count": entry.prepared.render_count,
            "uniform_render_p95_ms": p95,
        }

    async def close(self, project_id: str, run_id: str) -> None:
        """幂等关闭指定 run 的 Renderer。."""
        prepared_entry = self._prepared.pop((project_id, run_id), None)
        renderer = self._renderers.pop((project_id, run_id), None)
        first_error: Exception | None = None
        if prepared_entry is not None:
            try:
                await prepared_entry.prepared.close()
            except Exception as exc:
                first_error = exc
        if renderer is not None:
            try:
                await renderer.close()
            except Exception as exc:
                first_error = first_error or exc
        if first_error is not None:
            raise first_error


def _raw_rgb_array(rgb_bytes: bytes, width: int, height: int) -> np.ndarray:
    """把 Renderer 原始 RGB bytes 视图转为 MAE 使用的 float32 数组."""
    expected = width * height * 3
    if len(rgb_bytes) != expected:
        raise ValueError(f"prepared RGB 长度应为 {expected}，实际为 {len(rgb_bytes)}。")
    return (
        np.frombuffer(rgb_bytes, dtype=np.uint8)
        .reshape(height, width, 3)
        .astype(np.float32)
        / 255.0
    )


def _encode_rgb_png(rgb_bytes: bytes, width: int, height: int) -> bytes:
    """仅在候选被接受时把已返回的 RGB 编码为 PNG."""
    _raw_rgb_array(rgb_bytes, width, height)
    image = Image.frombytes("RGB", (width, height), rgb_bytes)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def _evaluate_scene(
    state: dict[str, Any],
    scene: MinScene,
    registry: MinRendererRegistry,
    *,
    capture_png: bool,
) -> dict[str, Any]:
    if int(state.get("render_count", 0)) >= int(state.get("render_budget", 0)):
        raise RuntimeError("render_budget_exhausted")
    materialized = materialize_min_shader(scene)
    glsl = bake_min_uniforms(materialized)
    prepared = await registry.prepare(
        str(state["project_id"]),
        str(state["run_id"]),
        materialized.webgl1_source,
        scene.canvas.width,
        scene.canvas.height,
        materialized.uniform_schema,
    )
    result = await prepared.render_uniforms(
        materialized.uniform_values,
        capture_png=capture_png,
    )
    count = int(state.get("render_count", 0)) + 1
    if not result.success or result.rgb_bytes is None:
        return {
            "success": False,
            "render_count": count,
            "glsl": glsl,
            "materialized": materialized,
            "error": result.draw_error or "render_failed",
        }
    mae = rgb_mae(
        state["target_rgb"],
        _raw_rgb_array(result.rgb_bytes, scene.canvas.width, scene.canvas.height),
    )
    return {
        "success": True,
        "render_count": count,
        "glsl": glsl,
        "materialized": materialized,
        "image": result.image_bytes,
        "rgb": result.rgb_bytes,
        "mae": mae,
    }


def make_min_nodes(
    artifacts: LocalArtifactStore,
    registry: MinRendererRegistry,
    gateway: LLMGateway,
) -> dict[str, Callable[..., Any]]:
    """创建共享 Gateway/Artifact/Renderer 边界的九个工作节点和三个决定节点。."""

    async def initialize_run(state: dict[str, Any]) -> dict[str, Any]:
        project_id, run_id = str(state["project_id"]), str(state["run_id"])
        run = artifacts.register_run(project_id, run_id)
        reference = run.write_bytes(
            "input/reference.png", state["image"], content_type=state["content_type"]
        )
        return {
            "phase": "initialize",
            "status": "running",
            "render_count": int(state.get("render_count", 0)),
            "render_budget": int(state.get("render_budget", 8)),
            "llm_call_count": min(
                effective_llm_budget(state.get("llm_budget", 0)),
                max(0, int(state.get("llm_call_count", 0))),
            ),
            "llm_budget": effective_llm_budget(state.get("llm_budget", 0)),
            "refine_count": 0,
            "refine_budget": int(state.get("refine_budget", 0)),
            "target_mae": float(state.get("target_mae", 0.08)),
            "feature_queue": ("rim", "shadow"),
            "trace": _trace(
                state, "initialize_run", f"输入已登记：{reference.sha256[:12]}"
            ),
        }

    async def perceive_target(state: dict[str, Any]) -> dict[str, Any]:
        perception = perceive_min_target(state["image"])
        return {
            "phase": "perception",
            "perception": perception.summary,
            "target_rgb": perception.target_rgb,
            "scene": perception.fallback_scene.model_dump(mode="json"),
            "trace": _trace(
                state,
                "perceive_target",
                f"{perception.width}x{perception.height}，scope={perception.summary['supported_scope']}",
            ),
        }

    async def author_initial(state: dict[str, Any]) -> dict[str, Any]:
        fallback = MinScene.model_validate(state["scene"])
        remaining = remaining_llm_calls(state)
        if remaining <= 0:
            return {
                "phase": "author_initial",
                "scene": fallback.model_dump(mode="json"),
                "trace": _trace(
                    state,
                    "author_initial",
                    "模型预算为 0，使用确定性感知 scene。",
                    author_source="perception_fallback",
                ),
            }
        schema = MinScene.model_json_schema(mode="validation")
        content = [
            text_part("perception", state.get("perception", {})),
            text_part("fallback_scene", fallback),
            text_part("user_instruction", state.get("instruction", "")),
            text_part("expected_json_schema", schema),
            *labeled_image_parts(
                "reference_image",
                state["image"],
                state.get("content_type", "image/png"),
            ),
        ]
        result = await invoke_min_author(
            gateway=gateway,
            messages=[
                SystemMessage(content=MIN_AUTHOR_INITIAL_PROMPT.prompt),
                multimodal_human_message(content),
            ],
            prompt=MIN_AUTHOR_INITIAL_PROMPT,
            schema=schema,
            parser=lambda text: parse_min_scene(
                text,
                expected_width=fallback.canvas.width,
                expected_height=fallback.canvas.height,
            ),
            remaining_calls=remaining,
            max_output_tokens=1800,
        )
        call_count = int(state.get("llm_call_count", 0)) + result.call_count
        if not isinstance(result.value, MinScene):
            return {
                "phase": "author_initial",
                "scene": fallback.model_dump(mode="json"),
                "llm_call_count": call_count,
                "author_model": result.model_ref,
                "author_error": result.error_code,
                "trace": _trace(
                    state,
                    "author_initial",
                    "模型调用或严格解析失败，安全回退到感知 scene。",
                    author_source="perception_fallback",
                    model_calls=result.call_count,
                    error_code=result.error_code,
                ),
            }
        return {
            "phase": "author_initial",
            "scene": result.value.model_dump(mode="json"),
            "llm_call_count": call_count,
            "author_model": result.model_ref,
            "author_error": None,
            "trace": _trace(
                state,
                "author_initial",
                "完整 MinScene 已通过严格模型契约。",
                author_source="model",
                model_calls=result.call_count,
                repaired=result.repaired,
            ),
        }

    async def materialize_shader(state: dict[str, Any]) -> dict[str, Any]:
        scene = MinScene.model_validate(state["scene"])
        materialized = materialize_min_shader(scene)
        return {
            "phase": "materialize",
            "materialized": materialized,
            "current_glsl": bake_min_uniforms(materialized),
            "trace": _trace(
                state, "materialize_shader", f"template={materialized.template_version}"
            ),
        }

    async def render_and_evaluate(state: dict[str, Any]) -> dict[str, Any]:
        scene = MinScene.model_validate(state["scene"])
        outcome = await _evaluate_scene(state, scene, registry, capture_png=True)
        if not outcome["success"]:
            return {
                "phase": "render",
                "error": str(outcome["error"]),
                "render_count": outcome["render_count"],
                "trace": _trace(
                    state, "render_and_evaluate", str(outcome["error"]), status="failed"
                ),
            }
        candidate = {
            "scene": scene.model_dump(mode="json"),
            "mae": outcome["mae"],
            "glsl": outcome["glsl"],
            "render": outcome["image"],
        }
        previous = state.get("current_best")
        accepted = not isinstance(previous, dict) or float(candidate["mae"]) < float(
            previous["mae"]
        )
        best = candidate if accepted else previous
        assert isinstance(best, dict)
        return {
            "phase": "render",
            "render_count": outcome["render_count"],
            "scene": best["scene"],
            "current_glsl": best["glsl"],
            "current_render": best["render"],
            "current_mae": outcome["mae"],
            "current_best_mae": best["mae"],
            "current_best": best,
            "error": None,
            "trace": _trace(
                state,
                "render_and_evaluate",
                f"{'accepted' if accepted else 'rejected'}，候选 MAE={outcome['mae']:.6f}，best MAE={best['mae']:.6f}",
            ),
        }

    async def optimize_base(state: dict[str, Any]) -> dict[str, Any]:
        baseline = dict(state["current_best"])
        best = baseline
        render_count = int(state["render_count"])
        baseline_scene = MinScene.model_validate(baseline["scene"])
        proposals = propose_min_scene_candidates(
            baseline_scene,
            stage="base",
            remaining_draw_budget=max(0, int(state["render_budget"]) - render_count),
            batch_size=24,
        )
        accepted_parameter: str | None = None
        for proposal in proposals:
            outcome = await _evaluate_scene(
                {**state, "render_count": render_count},
                proposal.scene,
                registry,
                capture_png=False,
            )
            render_count = outcome["render_count"]
            if outcome["success"] and float(outcome["mae"]) < float(best["mae"]):
                best = {
                    "scene": proposal.scene.model_dump(mode="json"),
                    "mae": outcome["mae"],
                    "glsl": outcome["glsl"],
                    "render": _encode_rgb_png(
                        outcome["rgb"],
                        proposal.scene.canvas.width,
                        proposal.scene.canvas.height,
                    ),
                }
                accepted_parameter = proposal.parameter.path
        improved = float(best["mae"]) < float(baseline["mae"])
        return {
            "phase": "base",
            "scene": best["scene"],
            "current_best": best,
            "current_best_mae": best["mae"],
            "current_glsl": best["glsl"],
            "current_render": best["render"],
            "render_count": render_count,
            "trace": _trace(
                state,
                "optimize_base",
                f"{'accepted' if improved else 'rolled_back'}，MAE={best['mae']:.6f}",
                candidates_evaluated=render_count - int(state["render_count"]),
                accepted_parameter=accepted_parameter,
            ),
        }

    async def optimize_feature(state: dict[str, Any]) -> dict[str, Any]:
        queue = list(state.get("feature_queue", ()))
        feature_type = queue.pop(0) if queue else "none"
        baseline = dict(state["current_best"])
        best = baseline
        render_count = int(state["render_count"])
        baseline_scene = MinScene.model_validate(baseline["scene"])
        feature = next(
            (
                item
                for item in baseline_scene.object.features
                if item.id == feature_type or item.type == feature_type
            ),
            None,
        )
        proposals = (
            propose_min_scene_candidates(
                baseline_scene,
                stage="feature",
                feature_id=feature.id,
                remaining_draw_budget=max(
                    0, int(state["render_budget"]) - render_count
                ),
                batch_size=4,
            )
            if feature is not None
            else ()
        )
        accepted_parameter: str | None = None
        for proposal in proposals:
            outcome = await _evaluate_scene(
                {**state, "render_count": render_count},
                proposal.scene,
                registry,
                capture_png=False,
            )
            render_count = outcome["render_count"]
            if outcome["success"] and float(outcome["mae"]) < float(best["mae"]):
                best = {
                    "scene": proposal.scene.model_dump(mode="json"),
                    "mae": outcome["mae"],
                    "glsl": outcome["glsl"],
                    "render": _encode_rgb_png(
                        outcome["rgb"],
                        proposal.scene.canvas.width,
                        proposal.scene.canvas.height,
                    ),
                }
                accepted_parameter = proposal.parameter.path
        improved = float(best["mae"]) < float(baseline["mae"])
        return {
            "phase": "feature",
            "scene": best["scene"],
            "current_best": best,
            "current_best_mae": best["mae"],
            "current_glsl": best["glsl"],
            "current_render": best["render"],
            "render_count": render_count,
            "feature_queue": tuple(queue),
            "trace": _trace(
                state,
                "optimize_feature",
                f"{feature_type} {'accepted' if improved else 'rolled_back'}，MAE={best['mae']:.6f}",
                candidates_evaluated=render_count - int(state["render_count"]),
                accepted_parameter=accepted_parameter,
            ),
        }

    async def author_refine(state: dict[str, Any]) -> dict[str, Any]:
        best = state.get("current_best")
        refine_count = int(state.get("refine_count", 0)) + 1
        if not isinstance(best, dict):
            return {
                "phase": "refine",
                "refine_count": refine_count,
                "trace": _trace(
                    state,
                    "author_refine",
                    "缺少 current_best，拒绝生成候选。",
                    status="failed",
                ),
            }
        best_scene = MinScene.model_validate(best["scene"])
        remaining = remaining_llm_calls(state)
        if remaining <= 0:
            return {
                "phase": "refine",
                "scene": best_scene.model_dump(mode="json"),
                "refine_count": refine_count,
                "trace": _trace(
                    state,
                    "author_refine",
                    "模型预算已耗尽，保留 current_best。",
                    author_source="current_best",
                ),
            }
        schema = min_author_patch_json_schema()
        content = [
            text_part("current_best_scene", best_scene),
            text_part("current_best_mae", best.get("mae")),
            text_part("user_instruction", state.get("instruction", "")),
            text_part("expected_json_schema", schema),
            *labeled_image_parts(
                "reference_image",
                state["image"],
                state.get("content_type", "image/png"),
            ),
        ]
        render = best.get("render")
        if isinstance(render, bytes):
            content.extend(
                labeled_image_parts("current_best_render", render, "image/png")
            )
        result = await invoke_min_author(
            gateway=gateway,
            messages=[
                SystemMessage(content=MIN_AUTHOR_REFINE_PROMPT.prompt),
                multimodal_human_message(content),
            ],
            prompt=MIN_AUTHOR_REFINE_PROMPT,
            schema=schema,
            parser=parse_min_author_patch,
            remaining_calls=remaining,
            max_output_tokens=500,
        )
        call_count = int(state.get("llm_call_count", 0)) + result.call_count
        candidate: MinScene | None = None
        error_code = result.error_code
        if result.value is not None:
            try:
                candidate = apply_min_author_patch(
                    best_scene,
                    cast(MinAuthorPatch, result.value),
                )
            except (TypeError, ValueError) as exc:
                error_code = f"patch_apply_failed:{type(exc).__name__}"
        if candidate is None:
            return {
                "phase": "refine",
                "scene": best_scene.model_dump(mode="json"),
                "llm_call_count": call_count,
                "refine_count": refine_count,
                "author_model": result.model_ref,
                "author_error": error_code,
                "trace": _trace(
                    state,
                    "author_refine",
                    "Patch 无效或调用失败，保留 current_best。",
                    author_source="current_best",
                    model_calls=result.call_count,
                    error_code=error_code,
                ),
            }
        return {
            "phase": "refine",
            "scene": candidate.model_dump(mode="json"),
            "llm_call_count": call_count,
            "refine_count": refine_count,
            "author_model": result.model_ref,
            "author_error": None,
            "trace": _trace(
                state,
                "author_refine",
                "已从 current_best 派生一个 typed patch 候选，等待真实渲染选择。",
                author_source="model_patch",
                model_calls=result.call_count,
                repaired=result.repaired,
            ),
        }

    async def finalize(state: dict[str, Any]) -> dict[str, Any]:
        project_id, run_id = str(state["project_id"]), str(state["run_id"])
        best = state.get("current_best")
        if not isinstance(best, dict):
            await registry.close(project_id, run_id)
            return {
                "status": "failed",
                "stop_reason": state.get("error") or "no_valid_render",
                "final_result": {},
                "trace": _trace(
                    state, "finalize", "没有有效渲染结果。", status="failed"
                ),
            }
        scene = MinScene.model_validate(best["scene"])
        materialized = materialize_min_shader(scene)
        run = artifacts.start_run(project_id, run_id)
        run.write_text("final/webgl1.glsl", str(best["glsl"]))
        run.write_text("final/shadertoy.glsl", materialized.shadertoy_source)
        run.write_bytes("final/render.png", best["render"], content_type="image/png")
        renderer_metrics = registry.metrics(project_id, run_id)
        target_mae = float(state.get("target_mae", 0.08))
        target_reached = float(best["mae"]) <= target_mae
        metrics = {
            "metric_version": "rgb_mae_v1",
            "mae": float(best["mae"]),
            "render_count": int(state.get("render_count", 0)),
            "llm_call_count": int(state.get("llm_call_count", 0)),
            **renderer_metrics,
            "target_mae": target_mae,
            "target_reached": target_reached,
        }
        run.write_json("final/metrics.json", metrics)
        trace = _trace(
            state,
            "finalize",
            f"已固化 final，MAE={best['mae']:.6f}",
            renderer_path=renderer_metrics["renderer_path"],
            target_mae=target_mae,
            target_reached=target_reached,
            prepare_duration_ms=renderer_metrics["prepare_duration_ms"],
            uniform_render_count=renderer_metrics["uniform_render_count"],
            uniform_render_p95_ms=renderer_metrics["uniform_render_p95_ms"],
        )
        manifest = {
            "schema_version": "png_to_shader_min_manifest_v1",
            "project_id": project_id,
            "run_id": run_id,
            "status": "completed",
            "stop_reason": state.get("stop_reason", "bounded_mvp_complete"),
            "scene": best["scene"],
            "metrics": metrics,
            "trace": trace,
        }
        manifest_ref = run.write_json("final/manifest.json", manifest)
        await registry.close(project_id, run_id)
        return {
            "status": "completed",
            "stop_reason": str(state.get("stop_reason", "bounded_mvp_complete")),
            "trace": trace,
            "final_manifest_ref": manifest_ref.relative_path,
            "final_result": {
                "project_id": project_id,
                "run_id": run_id,
                "glsl": best["glsl"],
                "render_width": scene.canvas.width,
                "render_height": scene.canvas.height,
                "status": "completed",
                "stop_reason": str(state.get("stop_reason", "bounded_mvp_complete")),
                "current_best_mae": float(best["mae"]),
                "render_count": int(state.get("render_count", 0)),
                "llm_call_count": int(state.get("llm_call_count", 0)),
                "renderer_path": renderer_metrics["renderer_path"],
                "target_mae": target_mae,
                "target_reached": target_reached,
                "prepare_duration_ms": renderer_metrics["prepare_duration_ms"],
                "uniform_render_count": renderer_metrics["uniform_render_count"],
                "uniform_render_p95_ms": renderer_metrics["uniform_render_p95_ms"],
                "scene": best["scene"],
                "trace": trace,
            },
        }

    def decide_after_render(state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error"):
            action, reason = "finalize", "render_failed"
        elif float(state["current_best_mae"]) <= float(state["target_mae"]):
            action, reason = "finalize", "target_mae_reached"
        elif int(state["render_count"]) >= int(state["render_budget"]):
            action, reason = "finalize", "render_budget_exhausted"
        else:
            action, reason = "optimize_base", "continue"
        return {"next_action": action, "stop_reason": reason}

    def _after_optimization(state: dict[str, Any]) -> dict[str, Any]:
        if float(state["current_best_mae"]) <= float(state["target_mae"]):
            return {"next_action": "finalize", "stop_reason": "target_mae_reached"}
        if int(state["render_count"]) >= int(state["render_budget"]):
            return {"next_action": "finalize", "stop_reason": "render_budget_exhausted"}
        if state.get("feature_queue"):
            return {"next_action": "optimize_feature", "stop_reason": "continue"}
        if int(state.get("llm_call_count", 0)) < int(
            state.get("llm_budget", 0)
        ) and int(state.get("refine_count", 0)) < int(state.get("refine_budget", 0)):
            return {"next_action": "author_refine", "stop_reason": "continue"}
        return {"next_action": "finalize", "stop_reason": "bounded_mvp_complete"}

    return {
        "initialize_run": initialize_run,
        "perceive_target": perceive_target,
        "author_initial": author_initial,
        "materialize_shader": materialize_shader,
        "render_and_evaluate": render_and_evaluate,
        "optimize_base": optimize_base,
        "optimize_feature": optimize_feature,
        "author_refine": author_refine,
        "finalize": finalize,
        "decide_after_render": decide_after_render,
        "decide_after_base": _after_optimization,
        "decide_after_feature": _after_optimization,
    }


__all__ = ["MinRendererRegistry", "make_min_nodes"]
