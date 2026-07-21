"""最小 scene Graph 的确定性工作节点。."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from shaderforge.evaluation import decode_rgb, rgb_mae
from shaderforge.generation import (
    bake_min_uniforms,
    materialize_min_shader,
)
from shaderforge.public import MinScene, perceive_min_target
from shaderforge.rendering import PlaywrightWebGL1Renderer
from shaderforge.store import LocalArtifactStore

RendererFactory = Callable[[], PlaywrightWebGL1Renderer]


def _trace(
    state: dict[str, Any], phase: str, message: str, *, status: str = "completed"
) -> tuple[dict[str, Any], ...]:
    return (
        *tuple(state.get("trace", ())),
        {"phase": phase, "status": status, "message": message},
    )


class MinRendererRegistry:
    """按 project/run 隔离并复用 Renderer。."""

    def __init__(self, factory: RendererFactory = PlaywrightWebGL1Renderer) -> None:
        """保存惰性 Renderer 工厂。."""
        self._factory = factory
        self._renderers: dict[tuple[str, str], PlaywrightWebGL1Renderer] = {}

    def get(self, project_id: str, run_id: str) -> PlaywrightWebGL1Renderer:
        """获取或创建指定 run 的 Renderer。."""
        key = (project_id, run_id)
        renderer = self._renderers.get(key)
        if renderer is None:
            renderer = self._factory()
            self._renderers[key] = renderer
        return renderer

    async def close(self, project_id: str, run_id: str) -> None:
        """幂等关闭指定 run 的 Renderer。."""
        renderer = self._renderers.pop((project_id, run_id), None)
        if renderer is not None:
            await renderer.close()


async def _evaluate_scene(
    state: dict[str, Any],
    scene: MinScene,
    registry: MinRendererRegistry,
) -> dict[str, Any]:
    if int(state.get("render_count", 0)) >= int(state.get("render_budget", 0)):
        raise RuntimeError("render_budget_exhausted")
    materialized = materialize_min_shader(scene)
    glsl = bake_min_uniforms(materialized)
    renderer = registry.get(str(state["project_id"]), str(state["run_id"]))
    result = await renderer.render(glsl, scene.canvas.width, scene.canvas.height)
    count = int(state.get("render_count", 0)) + 1
    if not result.success or result.image_bytes is None:
        return {
            "success": False,
            "render_count": count,
            "glsl": glsl,
            "materialized": materialized,
            "error": result.compile.draw_error or "render_failed",
        }
    mae = rgb_mae(state["target_rgb"], decode_rgb(result.image_bytes))
    return {
        "success": True,
        "render_count": count,
        "glsl": glsl,
        "materialized": materialized,
        "image": result.image_bytes,
        "mae": mae,
    }


def make_min_nodes(
    artifacts: LocalArtifactStore,
    registry: MinRendererRegistry,
) -> dict[str, Callable[..., Any]]:
    """创建共享 Artifact/Renderer 边界的九个工作节点和三个决定节点。."""

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
            "llm_call_count": int(state.get("llm_call_count", 0)),
            "llm_budget": int(state.get("llm_budget", 0)),
            "refine_count": 0,
            "refine_budget": int(state.get("refine_budget", 0)),
            "target_mae": float(state.get("target_mae", 0.08)),
            "feature_queue": ("rim", "shadow"),
            "trace": _trace(state, "initialize_run", f"输入已登记：{reference.sha256[:12]}"),
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
        return {
            "phase": "author_initial",
            "trace": _trace(
                state,
                "author_initial",
                "快速贯通版使用确定性感知 scene；模型 Author 接口保留为后续增量。",
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
        outcome = await _evaluate_scene(state, scene, registry)
        if not outcome["success"]:
            return {
                "phase": "render",
                "error": str(outcome["error"]),
                "render_count": outcome["render_count"],
                "trace": _trace(
                    state, "render_and_evaluate", str(outcome["error"]), status="failed"
                ),
            }
        best = {
            "scene": scene.model_dump(mode="json"),
            "mae": outcome["mae"],
            "glsl": outcome["glsl"],
            "render": outcome["image"],
        }
        return {
            "phase": "render",
            "render_count": outcome["render_count"],
            "current_glsl": outcome["glsl"],
            "current_render": outcome["image"],
            "current_mae": outcome["mae"],
            "current_best_mae": outcome["mae"],
            "current_best": best,
            "error": None,
            "trace": _trace(
                state, "render_and_evaluate", f"MAE={outcome['mae']:.6f}"
            ),
        }

    async def optimize_base(state: dict[str, Any]) -> dict[str, Any]:
        baseline = dict(state["current_best"])
        best = baseline
        render_count = int(state["render_count"])
        for multiplier in (0.96, 1.04):
            if render_count >= int(state["render_budget"]):
                break
            data = copy.deepcopy(baseline["scene"])
            axes = data["object"]["primitive"]["axes"]
            data["object"]["primitive"]["axes"] = [
                max(0.03, float(value) * multiplier) for value in axes
            ]
            candidate = MinScene.model_validate(data)
            outcome = await _evaluate_scene(
                {**state, "render_count": render_count}, candidate, registry
            )
            render_count = outcome["render_count"]
            if outcome["success"] and float(outcome["mae"]) < float(best["mae"]):
                best = {
                    "scene": candidate.model_dump(mode="json"),
                    "mae": outcome["mae"],
                    "glsl": outcome["glsl"],
                    "render": outcome["image"],
                }
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
            ),
        }

    async def optimize_feature(state: dict[str, Any]) -> dict[str, Any]:
        queue = list(state.get("feature_queue", ()))
        feature = queue.pop(0) if queue else "none"
        return {
            "phase": "feature",
            "feature_queue": tuple(queue),
            "trace": _trace(
                state,
                "optimize_feature",
                f"{feature} 使用感知初值，本轮无额外搜索。",
            ),
        }

    async def author_refine(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "phase": "refine",
            "refine_count": int(state.get("refine_count", 0)) + 1,
            "trace": _trace(
                state,
                "author_refine",
                "快速贯通版未启用真实模型，保留 current_best。",
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
                "trace": _trace(state, "finalize", "没有有效渲染结果。", status="failed"),
            }
        scene = MinScene.model_validate(best["scene"])
        materialized = materialize_min_shader(scene)
        run = artifacts.start_run(project_id, run_id)
        run.write_text("final/webgl1.glsl", str(best["glsl"]))
        run.write_text("final/shadertoy.glsl", materialized.shadertoy_source)
        run.write_bytes("final/render.png", best["render"], content_type="image/png")
        metrics = {
            "metric_version": "rgb_mae_v1",
            "mae": float(best["mae"]),
            "render_count": int(state.get("render_count", 0)),
            "llm_call_count": int(state.get("llm_call_count", 0)),
        }
        run.write_json("final/metrics.json", metrics)
        trace = _trace(state, "finalize", f"已固化 final，MAE={best['mae']:.6f}")
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
        if (
            int(state.get("llm_call_count", 0)) < int(state.get("llm_budget", 0))
            and int(state.get("refine_count", 0)) < int(state.get("refine_budget", 0))
        ):
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
